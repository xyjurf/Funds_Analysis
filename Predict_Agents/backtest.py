#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
回测验证模块：用数据库中的真实历史数据验证预测准确性。

流程：
  1. 对某个历史日期跑预测（或加载已保存的预测 JSON）
  2. 从 fund_values 查该日期之后 7 个交易日的真实净值
  3. 对比预测 Top 10 / Bottom 10 与真实涨跌幅排名
  4. 输出命中率、方向准确率、排名重叠度等指标
"""

import json
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import PREDICT_DAYS, TOP_N_CANDIDATES, DB_CONFIG, RESULT_DIR
from db_tools import (
    get_fund_candidates,
    get_fund_candidates_backtest,
    get_fund_nav_history,
    get_market_index_data,
    get_fund_info_batch,
    compute_technical_indicators,
    get_actual_performance,
    get_latest_data_date,
)


def get_fund_info_all_codes() -> list:
    """获取 fund_info 全部基金代码（用于构建全量名称映射）"""
    import pymysql
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT fund_code FROM fund_info")
            return [str(r[0]).strip().zfill(6) for r in cur.fetchall()]
    finally:
        conn.close()


def calc_actual_returns(fund_codes: list, start_date: str, lookforward_days: int = 10) -> dict:
    """
    查询每只基金在 start_date 之后的实际收益率。

    Returns:
        {fund_code: {'return': float, 'start_nav': float, 'end_nav': float, 'start_date': str, 'end_date': str}}
    """
    perf_df = get_actual_performance(fund_codes, start_date, lookforward_days)
    if perf_df.empty:
        return {}

    results = {}
    for code, group in perf_df.groupby('fund_code'):
        group = group.sort_values('net_value_date')
        if len(group) < 2:
            continue
        first = group.iloc[0]
        last = group.iloc[-1]
        start_nav = first['unit_net_value']
        end_nav = last['unit_net_value']
        if start_nav and end_nav and start_nav > 0:
            ret = (end_nav - start_nav) / start_nav
        else:
            ret = None
        results[code] = {
            'return': float(ret) if ret is not None else None,
            'start_nav': float(start_nav) if start_nav else None,
            'end_nav': float(end_nav) if end_nav else None,
            'start_date': str(first['net_value_date']),
            'end_date': str(last['net_value_date']),
        }
    return results


def rank_candidates_by_actual_return(candidates_df, actual_returns: dict) -> pd.DataFrame:
    """将候选基金按实际收益率排序"""
    codes = candidates_df['fund_code'].tolist()
    rows = []
    for code in codes:
        ar = actual_returns.get(code)
        if ar and ar['return'] is not None:
            rows.append({
                'fund_code': code,
                'actual_return': ar['return'],
                'start_nav': ar['start_nav'],
                'end_nav': ar['end_nav'],
                'start_date': ar['start_date'],
                'end_date': ar['end_date'],
            })

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = result.sort_values('actual_return', ascending=False).reset_index(drop=True)
    result['actual_rank'] = range(1, len(result) + 1)
    return result


def calc_hit_rate(predicted: list, actual: list) -> dict:
    """
    计算命中率等指标。
    predicted: predicted fund_code list (ordered)
    actual: actual fund_code list (ordered by real return)
    """
    pred_set = set(predicted)
    actual_set = set(actual)

    # Top10 vs Top20/50 重叠
    overlap_with_20 = len(pred_set & set(actual[:20]))
    overlap_with_50 = len(pred_set & set(actual[:50]))
    overlap_with_100 = len(pred_set & set(actual[:100]))
    overlap_self = len(pred_set & actual_set)

    # 方向准确率（sign match）：如果是 top 预测，实际确实正收益的比例
    # 用在 paired data 中

    # 平均排名（预测的基金在真实列表中的平均位置）
    actual_rank_map = {code: i + 1 for i, code in enumerate(actual)}
    ranks = [actual_rank_map.get(code, len(actual)) for code in predicted]
    avg_rank = sum(ranks) / len(ranks) if ranks else None

    return {
        'predicted_count': len(pred_set),
        'actual_count': len(actual_set),
        'overlap_in_self': overlap_self,           # 在 actual 前 N 中的数量
        'overlap_in_top20': overlap_with_20,
        'overlap_in_top50': overlap_with_50,
        'overlap_in_top100': overlap_with_100,
        'avg_actual_rank': round(avg_rank, 1) if avg_rank else None,
    }


def calc_direction_accuracy(predicted: list, actual_returns: dict, direction: str = 'gain') -> dict:
    """
    计算方向准确率：预测涨的基金实际确实涨了？预测跌的基金实际确实跌了？
    direction: 'gain' 表示预测涨, 'loss' 表示预测跌
    """
    total = len(predicted)
    correct = 0
    details = []
    for code in predicted:
        ar = actual_returns.get(code)
        if ar and ar['return'] is not None:
            actual_dir = 'gain' if ar['return'] > 0 else ('loss' if ar['return'] < 0 else 'flat')
            if direction == 'gain':
                is_correct = ar['return'] > 0
            else:
                is_correct = ar['return'] < 0
            if is_correct:
                correct += 1
            details.append({
                'fund_code': code,
                'predicted': direction,
                'actual_return': f"{ar['return']*100:+.2f}%",
                'correct': is_correct,
            })
        else:
            details.append({
                'fund_code': code,
                'predicted': direction,
                'actual_return': 'N/A',
                'correct': None,
            })

    valid = sum(1 for d in details if d['correct'] is not None)
    accuracy = correct / valid if valid > 0 else None
    return {
        'accuracy': round(accuracy, 4) if accuracy else None,
        'correct_count': correct,
        'valid_count': valid,
        'total': total,
        'details': details,
    }


def validate_predictions(
    predicted: dict,
    actual_returns: dict,
    all_ranked: pd.DataFrame,
    fund_names: dict,
) -> dict:
    """
    核心验证逻辑。

    Args:
        predicted: 预测结果 dict (含 top_10, bottom_10, summary)
        actual_returns: {code: {return, ...}} 真实收益率
        all_ranked: DataFrame 所有候选基金按真实收益率排序
        fund_names: {code: name} 映射

    Returns:
        验证报告 dict
    """
    top10_pred = [f['fund_code'] for f in predicted.get('top_10', [])]
    bottom10_pred = [f['fund_code'] for f in predicted.get('bottom_10', [])]

    # 真实排名列表
    actual_top = all_ranked.head(50)['fund_code'].tolist() if not all_ranked.empty else []
    actual_bottom = all_ranked.tail(50)['fund_code'].tolist() if not all_ranked.empty else []
    actual_bottom.reverse()  # 跌幅最大的在前

    # 命中率
    top_hit = calc_hit_rate(top10_pred, actual_top)
    bottom_hit = calc_hit_rate(bottom10_pred, actual_bottom)

    # 方向准确率
    gain_accuracy = calc_direction_accuracy(top10_pred, actual_returns, 'gain')
    loss_accuracy = calc_direction_accuracy(bottom10_pred, actual_returns, 'loss')

    # 首尾收益率对比
    top_actual_return = all_ranked.head(10)['actual_return'].mean() if not all_ranked.empty else None
    bottom_actual_return = all_ranked.tail(10)['actual_return'].mean() if not all_ranked.empty else None

    # 逐一列出每只预测基金的实际表现
    top10_details = []
    for f in predicted.get('top_10', []):
        code = f['fund_code']
        ar = actual_returns.get(code)
        name = fund_names.get(code, '')
        if ar:
            # 在真实排名中的位置
            rank_pos = all_ranked[all_ranked['fund_code'] == code]['actual_rank'].values
            actual_rank = int(rank_pos[0]) if len(rank_pos) > 0 else '-'
            top10_details.append({
                'fund_code': code,
                'fund_name': name,
                'predicted_return': f.get('expected_return_7d', ''),
                'actual_return': f"{ar['return']*100:+.2f}%" if ar['return'] is not None else 'N/A',
                'actual_rank': actual_rank,
            })
        else:
            top10_details.append({
                'fund_code': code,
                'fund_name': name,
                'predicted_return': f.get('expected_return_7d', ''),
                'actual_return': 'N/A',
                'actual_rank': '-',
            })

    bottom10_details = []
    for f in predicted.get('bottom_10', []):
        code = f['fund_code']
        ar = actual_returns.get(code)
        name = fund_names.get(code, '')
        if ar:
            rank_pos = all_ranked[all_ranked['fund_code'] == code]['actual_rank'].values
            actual_rank = int(rank_pos[0]) if len(rank_pos) > 0 else '-'
            bottom10_details.append({
                'fund_code': code,
                'fund_name': name,
                'predicted_return': f.get('expected_return_7d', ''),
                'actual_return': f"{ar['return']*100:+.2f}%" if ar['return'] is not None else 'N/A',
                'actual_rank': actual_rank,
            })
        else:
            bottom10_details.append({
                'fund_code': code,
                'fund_name': name,
                'predicted_return': f.get('expected_return_7d', ''),
                'actual_return': 'N/A',
                'actual_rank': '-',
            })

    # 真实 Top 10 / Bottom 10（含名称）
    real_top10 = []
    for _, row in all_ranked.head(10).iterrows():
        code = row['fund_code']
        ar = actual_returns.get(code, {})
        real_top10.append({
            'rank': row['actual_rank'],
            'fund_code': code,
            'fund_name': fund_names.get(code, ''),
            'actual_return': f"{row['actual_return']*100:+.2f}%",
        })

    real_bottom10 = []
    for _, row in all_ranked.tail(10).iterrows():
        code = row['fund_code']
        ar = actual_returns.get(code, {})
        real_bottom10.append({
            'rank': row['actual_rank'],
            'fund_code': code,
            'fund_name': fund_names.get(code, ''),
            'actual_return': f"{row['actual_return']*100:+.2f}%",
        })

    return {
        'hit_rate': {
            'top10': top_hit,
            'bottom10': bottom_hit,
        },
        'direction_accuracy': {
            'gain': gain_accuracy,
            'loss': loss_accuracy,
        },
        'average_returns': {
            'predicted_top10_avg_real_return': f"{top_actual_return*100:+.2f}%" if top_actual_return else 'N/A',
            'predicted_bottom10_avg_real_return': f"{bottom_actual_return*100:+.2f}%" if bottom_actual_return else 'N/A',
        },
        'predicted_vs_actual_details': {
            'top10': top10_details,
            'bottom10': bottom10_details,
            'actual_top10': real_top10,
            'actual_bottom10': real_bottom10,
        },
    }


def format_validation_report(pred_date: str, validation: dict) -> str:
    """格式化验证报告"""
    lines = []
    lines.append(f"{'=' * 70}")
    lines.append(f"  回测验证报告")
    lines.append(f"  预测基准日: {pred_date}")
    lines.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"{'=' * 70}")

    hr = validation.get('hit_rate', {})
    da = validation.get('direction_accuracy', {})
    avg_ret = validation.get('average_returns', {})

    # 命中率
    lines.append("")
    lines.append("[命中率 - 预测 Top 10 在真实排名中的位置]")
    lines.append("-" * 40)
    top_hit = hr.get('top10', {})
    lines.append(f"  在真实 Top 20 内: {top_hit.get('overlap_in_top20', 0)} / 10")
    lines.append(f"  在真实 Top 50 内: {top_hit.get('overlap_in_top50', 0)} / 10")
    lines.append(f"  在真实 Top 100 内: {top_hit.get('overlap_in_top100', 0)} / 10")
    lines.append(f"  平均真实排名: {top_hit.get('avg_actual_rank', '-')}")

    bottom_hit = hr.get('bottom10', {})
    lines.append("")
    lines.append("[命中率 - 预测 Bottom 10 在真实跌幅排名中的位置]")
    lines.append("-" * 40)
    lines.append(f"  在真实 Bottom 20 内: {bottom_hit.get('overlap_in_top20', 0)} / 10")
    lines.append(f"  在真实 Bottom 50 内: {bottom_hit.get('overlap_in_top50', 0)} / 10")
    lines.append(f"  在真实 Bottom 100 内: {bottom_hit.get('overlap_in_top100', 0)} / 10")
    lines.append(f"  平均真实排名: {bottom_hit.get('avg_actual_rank', '-')}")

    # 方向准确率
    lines.append("")
    lines.append("[方向准确率]")
    lines.append("-" * 40)
    gain_acc = da.get('gain', {})
    acc = gain_acc.get('accuracy')
    lines.append(f"  预测涨 → 实际涨: {gain_acc.get('correct_count', 0)}/{gain_acc.get('valid_count', 0)}"
                 f" ({f'{acc*100:.1f}%' if acc else 'N/A'})")
    loss_acc = da.get('loss', {})
    acc2 = loss_acc.get('accuracy')
    lines.append(f"  预测跌 → 实际跌: {loss_acc.get('correct_count', 0)}/{loss_acc.get('valid_count', 0)}"
                 f" ({f'{acc2*100:.1f}%' if acc2 else 'N/A'})")

    # 平均收益率
    lines.append("")
    lines.append("[平均收益率对比]")
    lines.append("-" * 40)
    lines.append(f"  预测 Top 10 实际平均: {avg_ret.get('predicted_top10_avg_real_return', 'N/A')}")
    lines.append(f"  预测 Bottom 10 实际平均: {avg_ret.get('predicted_bottom10_avg_real_return', 'N/A')}")

    # 逐一对比
    details = validation.get('predicted_vs_actual_details', {})
    lines.append("")
    lines.append("[预测 Top 10 逐一验证]")
    lines.append("-" * 70)
    lines.append(f"  {'代码':>8} {'名称':<20} {'预测收益':<12} {'实际收益':<12} {'真实排名':<8}")
    lines.append(f"  {'-'*60}")
    for f in details.get('top10', []):
        lines.append(f"  {f['fund_code']:>8} {f['fund_name'][:18]:<20} "
                     f"{f['predicted_return']:<12} {f['actual_return']:<12} {f['actual_rank']:<8}")

    lines.append("")
    lines.append("[预测 Bottom 10 逐一验证]")
    lines.append("-" * 70)
    lines.append(f"  {'代码':>8} {'名称':<20} {'预测收益':<12} {'实际收益':<12} {'真实排名':<8}")
    lines.append(f"  {'-'*60}")
    for f in details.get('bottom10', []):
        lines.append(f"  {f['fund_code']:>8} {f['fund_name'][:18]:<20} "
                     f"{f['predicted_return']:<12} {f['actual_return']:<12} {f['actual_rank']:<8}")

    # 真实 Top 10
    lines.append("")
    lines.append("[真实涨幅 Top 10（期间实际表现最好）]")
    lines.append("-" * 50)
    for f in details.get('actual_top10', []):
        lines.append(f"  #{f['rank']:>4} {f['fund_code']:>8} {f['fund_name'][:20]:<20} {f['actual_return']:<12}")

    # 真实 Bottom 10
    lines.append("")
    lines.append("[真实跌幅 Bottom 10（期间实际表现最差）]")
    lines.append("-" * 50)
    for f in details.get('actual_bottom10', []):
        lines.append(f"  #{f['rank']:>4} {f['fund_code']:>8} {f['fund_name'][:20]:<20} {f['actual_return']:<12}")

    lines.append("")
    lines.append(f"{'=' * 70}")
    lines.append(f"  验证完成")
    lines.append(f"{'=' * 70}")

    return '\n'.join(lines)


def load_prediction_json(filepath: str) -> Optional[dict]:
    """加载已保存的预测 JSON 文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] 加载预测文件失败: {e}")
        return None


def run_backtest(target_date: str, use_saved_prediction: Optional[str] = None) -> dict:
    """
    运行完整回测验证。

    Args:
        target_date: 预测基准日期 YYYY-MM-DD
        use_saved_prediction: 可选，已保存的预测 JSON 路径，不填则重新预测

    Returns:
        validation_result dict
    """
    print(f"[Backtest] 基准日期: {target_date}")
    print(f"[Backtest] 验证数据来源: fund_values 真实净值")

    # 1. 获取预测结果
    if use_saved_prediction:
        data = load_prediction_json(use_saved_prediction)
        if not data:
            return {'error': f'无法加载预测文件: {use_saved_prediction}'}
        predictions = data.get('predictions', {})
        print(f"[Backtest] 从文件加载预测: {use_saved_prediction}")
    else:
        print(f"[Backtest] 运行预测流程（回测模式）...")
        from agents_workflow import run_prediction as do_predict
        result = do_predict(target_date, backtest_mode=True)
        if 'error' in result:
            return {'error': result['error']}
        predictions = result.get('predictions', {})
        print(f"[Backtest] 预测完成")

    if not predictions or ('top_10' not in predictions and 'raw' in predictions):
        return {'error': '预测结果格式异常'}

    # 2. 获取候选基金（回测模式用 fund_values 推算）
    candidates = get_fund_candidates_backtest(target_date, TOP_N_CANDIDATES)
    if candidates.empty:
        return {'error': f'日期 {target_date} 无候选基金数据'}

    # 从 fund_info 构建全量名称映射（覆盖所有可能被预测的基金）
    fund_names = {}
    try:
        finfo_all = get_fund_info_batch(get_fund_info_all_codes())
        fund_names = dict(zip(finfo_all['fund_code'], finfo_all['fund_name']))
    except Exception:
        # fallback: 使用候选基金自身的名称
        fund_names = dict(zip(candidates['fund_code'], candidates['fund_name']))
    # 确保候选基金的名称优先
    for _, row in candidates.iterrows():
        fund_names[row['fund_code']] = row['fund_name']

    # 3. 收集预测涉及的所有基金代码
    pred_codes = set()
    for f in predictions.get('top_10', []):
        pred_codes.add(f['fund_code'])
    for f in predictions.get('bottom_10', []):
        pred_codes.add(f['fund_code'])

    # 也获取候选基金中所有基金的真实表现，用于排名
    all_codes = candidates['fund_code'].tolist()
    all_codes.extend([c for c in pred_codes if c not in all_codes])

    # 4. 查询实际表现
    print(f"[Backtest] 查询实际净值数据...")
    actual_returns = calc_actual_returns(all_codes, target_date)
    print(f"[Backtest] 查询完成: {len(actual_returns)} 只基金有实际数据")

    # 5. 计算候选基金排名
    all_ranked = rank_candidates_by_actual_return(candidates, actual_returns)
    print(f"[Backtest] 可排名基金: {len(all_ranked)} 只")

    if all_ranked.empty:
        return {'error': '无法计算真实排名'}

    # 6. 验证
    print(f"[Backtest] 计算验证指标...")
    validation = validate_predictions(
        predictions, actual_returns, all_ranked, fund_names
    )

    return validation


def main():
    import argparse
    parser = argparse.ArgumentParser(description='基金预测回测验证')
    parser.add_argument('--date', '-d', type=str, required=True,
                        help='预测基准日期 YYYY-MM-DD（回测标的日）')
    parser.add_argument('--load', '-l', type=str, default=None,
                        help='加载已保存的预测 JSON 文件（不传则重新预测）')
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='保存验证结果 JSON 的文件路径')
    args = parser.parse_args()

    result = run_backtest(args.date, args.load)

    if 'error' in result:
        print(f"\n[ERROR] {result['error']}")
        sys.exit(1)

    # 输出报告
    report = format_validation_report(args.date, result)
    try:
        print('\n' + report + '\n')
    except UnicodeEncodeError:
        print('\n' + report.encode('utf-8', errors='replace').decode('gbk', errors='replace') + '\n')

    # 保存 JSON
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[OK] 验证结果已保存: {args.output}")

    # 自动保存到 result_agents
    auto_path = os.path.join(
        RESULT_DIR,
        f'backtest_{args.date}.json'
    )
    with open(auto_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[OK] 验证结果已自动保存: {auto_path}")


if __name__ == '__main__':
    main()
