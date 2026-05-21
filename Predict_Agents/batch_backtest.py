#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量回测：对多个日期连续运行预测 → 验证 → 统计汇总。

2025 年 7~8 月每周一次预测，用 fund_values 真实数据验证，汇总准确度指标。
"""

import sys
import os
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import run_backtest, format_validation_report
from config import PREDICT_DAYS, RESULT_DIR


# 2025 年 7~8 月预测日期（每周一，共 8 次）
PREDICTION_DATES = [
    '2025-07-07',
    '2025-07-14',
    '2025-07-21',
    '2025-07-28',
    '2025-08-04',
    '2025-08-11',
    '2025-08-18',
    '2025-08-25',
]

RUN_BATCH = False  # 默认不重新运行预测（可设为 True 执行全部）


def aggregate_results(results: list) -> dict:
    """汇总多期回测结果"""
    if not results:
        return {}

    metrics = {
        '方向准确率_预测涨': [],
        '方向准确率_预测跌': [],
        '命中率_Top10_in_Top20': [],
        '命中率_Top10_in_Top50': [],
        '命中率_Bottom10_in_Bottom20': [],
        '命中率_Bottom10_in_Bottom50': [],
        '平均真实排名_Top10': [],
        '平均真实排名_Bottom10': [],
        '预测Top10_平均收益': [],
        '预测Bottom10_平均收益': [],
    }

    details = []
    for r in results:
        date = r.get('date', '')
        hr = r.get('validation', {}).get('hit_rate', {})
        da = r.get('validation', {}).get('direction_accuracy', {})
        avg = r.get('validation', {}).get('average_returns', {})

        top_hit = hr.get('top10', {})
        bot_hit = hr.get('bottom10', {})

        row = {
            'date': date,
            'top10_in_top20': top_hit.get('overlap_in_top20', 0),
            'top10_in_top50': top_hit.get('overlap_in_top50', 0),
            'top10_in_top100': top_hit.get('overlap_in_top100', 0),
            'top10_avg_rank': top_hit.get('avg_actual_rank'),
            'bottom10_in_bottom20': bot_hit.get('overlap_in_top20', 0),
            'bottom10_in_bottom50': bot_hit.get('overlap_in_top50', 0),
            'bottom10_in_bottom100': bot_hit.get('overlap_in_top100', 0),
            'bottom10_avg_rank': bot_hit.get('avg_actual_rank'),
            'gain_accuracy': da.get('gain', {}).get('accuracy'),
            'loss_accuracy': da.get('loss', {}).get('accuracy'),
            'avg_top_return': avg.get('predicted_top10_avg_real_return', ''),
            'avg_bottom_return': avg.get('predicted_bottom10_avg_real_return', ''),
        }
        details.append(row)

        # 聚合数值
        for key in metrics:
            val_map = {
                '方向准确率_预测涨': row['gain_accuracy'],
                '方向准确率_预测跌': row['loss_accuracy'],
                '命中率_Top10_in_Top20': row['top10_in_top20'],
                '命中率_Top10_in_Top50': row['top10_in_top50'],
                '命中率_Bottom10_in_Bottom20': row['bottom10_in_bottom20'],
                '命中率_Bottom10_in_Bottom50': row['bottom10_in_bottom50'],
                '平均真实排名_Top10': row['top10_avg_rank'],
                '平均真实排名_Bottom10': row['bottom10_avg_rank'],
                '预测Top10_平均收益': row['avg_top_return'],
                '预测Bottom10_平均收益': row['avg_bottom_return'],
            }
            v = val_map.get(key)
            if v is not None:
                metrics[key].append(v)

    # 计算汇总统计
    stats = {}
    for key, values in metrics.items():
        if not values:
            stats[key] = 'N/A'
            continue
        if '准确率' in key:
            stats[key] = f"{sum(values)/len(values)*100:.1f}%"
        elif '命中率' in key:
            avg_val = sum(values)/len(values)
            stats[key] = f"{avg_val:.1f}/10"
        elif '排名' in key:
            stats[key] = f"{sum(values)/len(values):.1f}"
        elif '收益' in key:
            parsed = []
            for v in values:
                if isinstance(v, str):
                    try:
                        parsed.append(float(v.replace('%', '').replace('+', '')))
                    except (ValueError, TypeError):
                        pass
                elif isinstance(v, (int, float)):
                    parsed.append(float(v))
            if parsed:
                stats[key] = f"{sum(parsed)/len(parsed):+.2f}%"
            else:
                stats[key] = 'N/A'
        else:
            stats[key] = f"{sum(values)/len(values):.1f}"

    # 解析收益率字符串为数值
    top_returns = []
    bottom_returns = []
    for d in details:
        t = d.get('avg_top_return', '').replace('%', '')
        b = d.get('avg_bottom_return', '').replace('%', '')
        try:
            top_returns.append(float(t))
        except (ValueError, TypeError):
            pass
        try:
            bottom_returns.append(float(b))
        except (ValueError, TypeError):
            pass

    avg_spread = (sum(top_returns)/len(top_returns) - sum(bottom_returns)/len(bottom_returns)) if top_returns and bottom_returns else None

    return {
        'aggregate_stats': stats,
        'average_spread': f"{avg_spread:+.2f}%" if avg_spread else 'N/A',
        'period_details': details,
        'total_backtests': len(results),
        'successful_backtests': len([r for r in results if 'error' not in r]),
    }


def run_batch(dates: list, save_dir: str = None, skip_existing: bool = True):
    """运行批量回测"""
    if save_dir is None:
        save_dir = RESULT_DIR

    results = []
    total = len(dates)

    for i, date in enumerate(dates):
        # 跳过已有结果
        if skip_existing:
            existing_path = os.path.join(save_dir, f'backtest_{date}.json')
            if os.path.exists(existing_path):
                try:
                    with open(existing_path, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                    if 'error' not in existing_data and 'hit_rate' in existing_data:
                        print(f"[{i+1}/{total}] 跳过 {date}（已有结果）")
                        results.append({'date': date, 'validation': existing_data, 'loaded': True})
                        continue
                except Exception:
                    pass
        print(f"\n{'=' * 60}")
        print(f"[{i+1}/{total}] 回测日期: {date}")
        print(f"{'=' * 60}")

        start = time.time()
        try:
            validation = run_backtest(date)
            elapsed = time.time() - start

            if 'error' in validation:
                print(f"[{i+1}/{total}] 错误: {validation['error']}")
                results.append({'date': date, 'error': validation['error']})
            else:
                # 输出报告
                report = format_validation_report(date, validation)
                try:
                    print(report)
                except UnicodeEncodeError:
                    pass

                results.append({
                    'date': date,
                    'validation': validation,
                    'elapsed': round(elapsed, 1),
                })

                # 单期结果保存
                if save_dir:
                    path = os.path.join(save_dir, f'backtest_{date}.json')
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(validation, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"[{i+1}/{total}] 异常: {e}")
            import traceback
            traceback.print_exc()
            results.append({'date': date, 'error': str(e)})

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description='批量回测：连续一个月预测验证')
    parser.add_argument('--run', '-r', action='store_true', default=RUN_BATCH,
                        help='执行全部预测（默认只输出计划）')
    parser.add_argument('--dates', '-d', type=str, nargs='+', default=None,
                        help='指定回测日期列表（默认 2025-07 ~ 2025-08 每周一）')
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='汇总结果 JSON 保存路径')
    args = parser.parse_args()

    dates = args.dates or PREDICTION_DATES

    print(f"{'=' * 60}")
    print(f"  批量回测计划")
    print(f"  周期: {dates[0]} ~ {dates[-1]}")
    print(f"  次数: {len(dates)} 次")
    print(f"  预测周期: 每个日期向前看 {PREDICT_DAYS} 个交易日")
    print(f"{'=' * 60}")

    # 检查已有结果
    base_dir = RESULT_DIR
    existing = []
    for d in dates:
        path = os.path.join(base_dir, f'backtest_{d}.json')
        if os.path.exists(path):
            existing.append(d)
    if existing:
        print(f"\n已有 {len(existing)} 个回测结果: {', '.join(existing)}")

    if not args.run:
        print(f"\n使用 --run 参数执行全部预测（每次约 2 次 API 调用，共 {len(dates)*2} 次）")
        return

    # 执行批处理
    print(f"\n开始执行 {len(dates)} 次回测...")
    results = run_batch(dates, base_dir)

    # 汇总
    print(f"\n\n{'=' * 60}")
    print(f"  批量回测汇总报告")
    print(f"{'=' * 60}")

    summary = aggregate_results(results)
    if not summary:
        print("  无有效结果")
        return

    print(f"\n  有效回测: {summary['successful_backtests']} / {summary['total_backtests']}")
    print(f"  平均收益差 (Top-Bottom): {summary['average_spread']}")
    print(f"")

    stats = summary.get('aggregate_stats', {})
    print(f"  [方向准确率]")
    print(f"    预测涨 → 实际涨: {stats.get('方向准确率_预测涨', 'N/A')}")
    print(f"    预测跌 → 实际跌: {stats.get('方向准确率_预测跌', 'N/A')}")
    print(f"")
    print(f"  [命中率]")
    print(f"    预测 Top 10 进入真实 Top 20: {stats.get('命中率_Top10_in_Top20', 'N/A')}")
    print(f"    预测 Top 10 进入真实 Top 50: {stats.get('命中率_Top10_in_Top50', 'N/A')}")
    print(f"    预测 Bottom 10 进入真实 Bottom 20: {stats.get('命中率_Bottom10_in_Bottom20', 'N/A')}")
    print(f"    预测 Bottom 10 进入真实 Bottom 50: {stats.get('命中率_Bottom10_in_Bottom50', 'N/A')}")
    print(f"")
    print(f"  [平均排名]")
    print(f"    预测 Top 10 在真实排名中的平均位置: {stats.get('平均真实排名_Top10', 'N/A')}")
    print(f"    预测 Bottom 10 在真实跌幅排名中的平均位置: {stats.get('平均真实排名_Bottom10', 'N/A')}")
    print(f"")
    print(f"  [收益对比]")
    print(f"    预测 Top 10 实际平均收益: {stats.get('预测Top10_平均收益', 'N/A')}")
    print(f"    预测 Bottom 10 实际平均收益: {stats.get('预测Bottom10_平均收益', 'N/A')}")

    # 逐期详情
    print(f"\n")
    print(f"  [逐期详情]")
    print(f"  {'日期':<14} {'Top20命中':>8} {'Top50命中':>8} {'Bottom20命中':>12} {'Bottom50命中':>12} {'方向涨':>8} {'方向跌':>8}")
    print(f"  {'-'*70}")
    for d in summary.get('period_details', []):
        ga = d.get('gain_accuracy')
        la = d.get('loss_accuracy')
        ga_str = f"{ga*100:.0f}%" if ga else 'N/A'
        la_str = f"{la*100:.0f}%" if la else 'N/A'
        print(f"  {d['date']:<14} {d['top10_in_top20']:>8} {d['top10_in_top50']:>8} "
              f"{d['bottom10_in_bottom20']:>12} {d['bottom10_in_bottom50']:>12} "
              f"{ga_str:>8} {la_str:>8}")

    # 保存汇总
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\n[OK] 汇总结果已保存: {args.output}")

    auto_path = os.path.join(base_dir, 'batch_summary.json')
    with open(auto_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"[OK] 汇总结果已自动保存: {auto_path}")


if __name__ == '__main__':
    main()
