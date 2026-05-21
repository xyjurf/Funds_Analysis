"""LangGraph 多智能体基金预测工作流

Agents:
  1. DataCollector   — 从数据库收集基金、指数数据
  2. TechAnalyst     — 计算技术指标（动量、波动率、趋势）
  3. MarketAnalyst   — DeepSeek Agent：分析市场环境
  4. Predictor       — DeepSeek Agent：综合预测 Top 10 / Bottom 10
  5. Reporter        — 格式化输出报告
"""

import json
import os
import glob
import time
import numpy as np
from datetime import datetime
from typing import TypedDict, Optional, Any

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from config import DEEPSEEK_CONFIG, TOP_N_CANDIDATES, PREDICT_DAYS
from db_tools import (
    get_fund_candidates,
    get_fund_candidates_backtest,
    get_fund_candidates_optimized,
    get_fund_nav_history,
    get_market_index_data,
    get_fund_info_batch,
    compute_technical_indicators,
    get_index_trend,
    get_latest_data_date,
    get_nearest_fund_data_date,
)
from ensemble_models import run_all_strategies
from external_data import get_sector_composition, get_index_correlation, get_recent_dividends


# ──────────────────── State ────────────────────

class PredictState(TypedDict):
    target_date: str                      # 用户指定的基准日期
    backtest_mode: bool                   # 是否回测模式（从 fund_values 推算特征）
    resolved_date: Optional[str]          # 实际使用的数据日期
    latest_data_date: Optional[str]       # 数据库最新数据日期
    report_date: str                      # 报告日期（当前实际日期）
    error: Optional[str]                  # 错误信息

    # 各阶段产出
    fund_candidates: Optional[Any]        # DataFrame: 候选基金列表
    nav_history: Optional[Any]            # DataFrame: 净值历史
    index_data: Optional[Any]             # DataFrame: 原始指数数据（用于Beta计算）
    tech_indicators: Optional[Any]        # DataFrame: 技术指标
    index_trend: Optional[dict]           # dict: 指数趋势摘要
    fund_info: Optional[Any]              # DataFrame: 基金详细信息

    # 外部数据
    sector_info: Optional[Any]            # dict: 板块/风格分析
    corr_info: Optional[Any]              # dict: 指数相关性

    # Ensemble 产出
    ensemble_signals: Optional[Any]       # dict: 三个量化策略的推荐列表

    # LLM 产出
    market_analysis: Optional[str]        # 市场环境分析
    predictions: Optional[Any]            # 预测结果 (dict or list)
    report: Optional[str]                 # 最终报告


# ──────────────────── LLM ────────────────────

def _build_llm():
    return ChatOpenAI(
        model=DEEPSEEK_CONFIG['model'],
        api_key=DEEPSEEK_CONFIG['api_key'],
        base_url=DEEPSEEK_CONFIG['base_url'],
        temperature=DEEPSEEK_CONFIG['temperature'],
    )


# ──────────────────── 节点函数 ────────────────────

def node_collect_data(state: PredictState) -> dict:
    """Agent 1: DataCollector — 从数据库拉取原始数据"""
    t0 = time.time()
    target_date = state['target_date']
    backtest_mode = state.get('backtest_mode', False)
    print(f"[DataCollector] 目标日期: {target_date}")
    if backtest_mode:
        print(f"[DataCollector] 模式: 回测模式 (从 fund_values 推算特征)")

    # 1. 获取数据库最新日期
    latest_date = get_latest_data_date()
    print(f"[DataCollector] 数据库最新数据日期: {latest_date}")

    # 2. 自动 fallback 到最近有效数据日期
    resolved = get_nearest_fund_data_date(target_date)
    if resolved:
        print(f"[DataCollector] 实际使用 fund_data 日期: {resolved}")

    # 3. 筛选权益型候选基金（全量分析，不做 top N 截断）
    if backtest_mode:
        candidates = get_fund_candidates_optimized(target_date, TOP_N_CANDIDATES)
    else:
        candidates = get_fund_candidates(target_date, TOP_N_CANDIDATES)
    print(f"[DataCollector] 候选基金: {len(candidates)} 只 ({time.time()-t0:.1f}s)")

    if candidates.empty:
        print(f"[DataCollector] 警告: 日期 {target_date} 无候选基金数据")
        return {
            'latest_data_date': latest_date,
            'resolved_date': resolved,
            'fund_candidates': candidates,
            'error': '无候选基金数据',
        }

    # 记录实际使用的数据日期
    actual_data_date = candidates.attrs.get('actual_date', target_date)
    print(f"[DataCollector] 实际数据日期: {actual_data_date}")

    fund_codes = candidates['fund_code'].tolist()
    print(f"[DataCollector] 共 {len(fund_codes)} 只基金，开始拉取净值数据...")

    # 4. 获取净值历史
    nav_hist = get_fund_nav_history(fund_codes, target_date)
    print(f"[DataCollector] 净值历史数据: {len(nav_hist)} 行 ({time.time()-t0:.1f}s)")

    # 5. 获取指数数据
    index_data = get_market_index_data(target_date)
    print(f"[DataCollector] 指数数据: {len(index_data)} 行 ({time.time()-t0:.1f}s)")

    # 6. 获取基金详细信息
    finfo = get_fund_info_batch(fund_codes)
    print(f"[DataCollector] 基金详情: {len(finfo)} 只 ({time.time()-t0:.1f}s)")

    index_trend = get_index_trend(index_data)

    # 外部数据：板块分析 + 指数相关性
    sector_info = get_sector_composition(index_data)
    corr_info = get_index_correlation(index_data)

    elapsed = time.time() - t0
    print(f"[DataCollector] 数据收集完成，总耗时 {elapsed:.1f}s")
    return {
        'latest_data_date': latest_date,
        'resolved_date': actual_data_date,
        'fund_candidates': candidates,
        'nav_history': nav_hist,
        'index_data': index_data,
        'index_trend': index_trend,
        'fund_info': finfo,
        'sector_info': sector_info,
        'corr_info': corr_info,
    }


def node_compute_indicators(state: PredictState) -> dict:
    """Agent 2: TechAnalyst — 计算技术指标（含RSI/KD/Beta）"""
    t0 = time.time()
    nav_hist = state.get('nav_history')
    n_funds = nav_hist['fund_code'].nunique() if nav_hist is not None and not nav_hist.empty else 0
    print(f"[TechAnalyst] 开始计算技术指标: {n_funds} 只基金...")
    if nav_hist is None or nav_hist.empty:
        print("[TechAnalyst] 无净值数据，跳过")
        return {}

    index_data = state.get('index_data')
    indicators = compute_technical_indicators(nav_hist, index_data)
    elapsed = time.time() - t0
    print(f"[TechAnalyst] 计算完成: {len(indicators)} 只基金有技术指标（含RSI/KD/Beta）, 耗时 {elapsed:.1f}s")
    return {'tech_indicators': indicators}


def _build_market_context(state: PredictState) -> str:
    """构建供 LLM 分析的市场上下文（含板块/相关性）"""
    candidates = state.get('fund_candidates')
    indicators = state.get('tech_indicators')
    index_trend = state.get('index_trend', {})
    finfo = state.get('fund_info')
    sector_info = state.get('sector_info', {})
    corr_info = state.get('corr_info', {})

    lines = []
    lines.append(f"=== 基金预测任务 ===")
    lines.append(f"基准日期: {state['target_date']}")
    lines.append(f"数据库最新数据: {state.get('latest_data_date', '未知')}")
    lines.append(f"预测周期: 未来 {PREDICT_DAYS} 个交易日")
    lines.append("")

    # 指数趋势
    lines.append("【市场指数趋势】")
    if index_trend:
        for name, info in index_trend.items():
            lines.append(f"  {name} ({info['code']}): "
                         f"最新 {info['latest_close']:.2f}, "
                         f"20日涨跌 {info['ret_20d']:+.2f}%, "
                         f"60日涨跌 {info['ret_60d']:+.2f}%, "
                         f"趋势: {info['trend']}")
    else:
        lines.append("  (无数据)")
    lines.append("")

    # 板块/风格分析
    if sector_info:
        lines.append("【市场风格与板块分布】")
        style = sector_info.get('style', '')
        if style:
            lines.append(f"  风格: {style}")
        lvs = sector_info.get('large_vs_small_20d', '')
        if lvs:
            lines.append(f"  近20日: {lvs}")
        lines.append("  基金类型规模分布:")
        for name, pct, cnt in sector_info.get('top_sectors', []):
            lines.append(f"    {name}: {pct:.1f}% ({cnt} 只)")
        lines.append("")

    # 指数相关性
    if corr_info:
        lines.append(f"【市场同步性】相关系数: {corr_info.get('avg_correlation', 'N/A')} "
                     f"→ {corr_info.get('market_breadth', '')}")
        lines.append("")

    # 合并技术指标和候选数据
    if indicators is not None and not indicators.empty and candidates is not None and not candidates.empty:
        merged = candidates.merge(indicators, on='fund_code', how='inner')

        # 排除无效数据
        merged = merged[merged['ret_60d'].notna()].copy()

        # 加入基金类型
        if finfo is not None and not finfo.empty:
            merged = merged.merge(finfo[['fund_code', 'fund_type', 'fund_company', 'fund_manager', 'fund_size']],
                                  on='fund_code', how='left')

        lines.append(f"【候选基金技术指标总览】共 {len(merged)} 只")
        lines.append(f"  近1月涨幅中位数: {merged['ret_20d'].median()*100:.2f}%")
        lines.append(f"  近3月涨幅中位数: {merged['ret_60d'].median()*100:.2f}%")
        lines.append(f"  波动率(60日)中位数: {merged['volatility_60d'].median()*100:.2f}%")
        lines.append(f"  最大回撤中位数: {merged['max_drawdown_60d'].median()*100:.2f}%")
        lines.append("")

        # 近期强势基金（ret_20d 最高）
        top_momentum = merged.nlargest(20, 'ret_20d')
        lines.append("【近期强势基金 Top 20】")
        for _, row in top_momentum.iterrows():
            company = row.get('fund_company', '')[:6] if pd_notna(row.get('fund_company')) else ''
            fund_type = row.get('fund_type', '') if pd_notna(row.get('fund_type')) else ''
            lines.append(
                f"  {row['fund_code']} {row['fund_name']} "
                f"| {fund_type} | {company}"
                f"| 1周:{row.get('ret_5d',0)*100:+.2f}%"
                f"| 1月:{row['ret_20d']*100:+.2f}%"
                f"| 3月:{row['ret_60d']*100:+.2f}%"
                f"| 波动:{row.get('volatility_60d',0)*100:.2f}%"
                f"| 回撤:{row.get('max_drawdown_60d',0)*100:.2f}%"
                f"| vsMA20:{row.get('pos_vs_ma20',0)*100:+.2f}%"
            )

        lines.append("")
        # 近期弱势基金（ret_20d 最低）
        bottom_momentum = merged.nsmallest(20, 'ret_20d')
        lines.append("【近期弱势基金 Bottom 20】")
        for _, row in bottom_momentum.iterrows():
            company = row.get('fund_company', '')[:6] if pd_notna(row.get('fund_company')) else ''
            fund_type = row.get('fund_type', '') if pd_notna(row.get('fund_type')) else ''
            lines.append(
                f"  {row['fund_code']} {row['fund_name']} "
                f"| {fund_type} | {company}"
                f"| 1周:{row.get('ret_5d',0)*100:+.2f}%"
                f"| 1月:{row['ret_20d']*100:+.2f}%"
                f"| 3月:{row['ret_60d']*100:+.2f}%"
                f"| 波动:{row.get('volatility_60d',0)*100:.2f}%"
                f"| vsMA20:{row.get('pos_vs_ma20',0)*100:+.2f}%"
            )
    else:
        lines.append("(技术指标数据不足)")

    return '\n'.join(lines)


def pd_notna(val):
    """NaN 安全判断"""
    try:
        import math
        if val is None:
            return False
        if isinstance(val, float) and math.isnan(val):
            return False
        return True
    except Exception:
        return val is not None


def node_analyze_market(state: PredictState) -> dict:
    """Agent 3: MarketAnalyst — DeepSeek 分析市场环境"""
    t0 = time.time()
    print("[MarketAnalyst] DeepSeek 分析市场环境...")
    context = _build_market_context(state)
    print(f"[MarketAnalyst] 市场上下文构建完成 ({len(context)} 字符)")

    system_prompt = """你是一位专业的中国公募基金市场分析师。你的任务是：

1. 分析当前市场环境（基于提供的指数趋势、基金表现数据）
2. 判断市场风格（大盘/小盘、价值/成长、板块轮动）
3. 评估市场风险偏好和情绪
4. 对未来7个交易日的市场走势给出判断

请给出结构化分析报告，包括：
- 市场整体判断
- 主要指数技术形态
- 市场风格偏好
- 风险提示
- 未来7日走势预判

分析要简洁、专业、有数据支撑。"""

    user_prompt = f"以下是中国基金市场数据，请分析市场环境：\n\n{context}"

    llm = _build_llm()
    print("[MarketAnalyst] 调用 DeepSeek API...")
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    analysis = response.content
    elapsed = time.time() - t0
    print(f"[MarketAnalyst] 分析完成 ({len(analysis)} 字, 耗时 {elapsed:.1f}s)")
    return {'market_analysis': analysis}


def node_predict(state: PredictState) -> dict:
    """Agent 4: Predictor — DeepSeek 综合预测（使用蒸馏后的量化信号）"""
    t0 = time.time()
    print("[Predictor] DeepSeek 生成预测...")

    candidates = state.get('fund_candidates')
    indicators = state.get('tech_indicators')
    market_analysis = state.get('market_analysis', '')
    index_trend = state.get('index_trend', {})
    finfo = state.get('fund_info')
    ensemble = state.get('ensemble_signals', {})

    if candidates is None or candidates.empty:
        return {'error': '无候选基金数据'}

    # 合并指标
    merged = candidates.copy()
    if indicators is not None and not indicators.empty:
        merged = merged.merge(indicators, on='fund_code', how='inner')
    if finfo is not None and not finfo.empty:
        merged = merged.merge(
            finfo[['fund_code', 'fund_type', 'fund_company', 'fund_manager', 'fund_size']],
            on='fund_code', how='left'
        )
    merged = merged[merged['ret_60d'].notna()].copy()
    print(f"[Predictor] 合并数据: {len(merged)} 只基金含完整指标 ({time.time()-t0:.1f}s)")

    # ═══════════════════════════════════════════════════
    # 构建蒸馏后的 LLM 输入（不发送 13000+ 行原始数据）
    # ═══════════════════════════════════════════════════
    lines = []

    # 标题
    lines.append(f"【预测基准日期】{state['target_date']}")
    lines.append(f"【预测周期】未来 {PREDICT_DAYS} 个交易日")
    lines.append("")

    # ── 1. 全市场统计摘要 ──
    lines.append("【全市场基金统计摘要】（基于全量 {} 只权益基金分析）".format(len(merged)))
    lines.append("  近1月收益: 中位数 {:.2f}%, 均值 {:.2f}%, 标准差 {:.2f}%".format(
        merged['ret_20d'].median() * 100, merged['ret_20d'].mean() * 100, merged['ret_20d'].std() * 100))
    lines.append("  近3月收益: 中位数 {:.2f}%".format(merged['ret_60d'].median() * 100))
    lines.append("  波动率(60d): 中位数 {:.2f}%".format(merged['volatility_60d'].median() * 100))
    lines.append("  最大回撤(60d): 中位数 {:.2f}%".format(merged['max_drawdown_60d'].median() * 100))
    type_dist = merged['fund_type_main'].value_counts()
    lines.append("  类型分布: {}".format(', '.join(f'{k}:{v}' for k, v in type_dist.items())))
    lines.append("")

    # ── 2. 指数趋势 ──
    if index_trend:
        lines.append("【指数趋势】")
        for name, info in index_trend.items():
            lines.append("  {}: 最新{:.2f}, 20日{:+.2f}%, 60日{:+.2f}%, {}".format(
                name, info['latest_close'], info['ret_20d'], info['ret_60d'], info['trend']))
        lines.append("")

    # ── 3. 市场环境分析（精简） ──
    if market_analysis:
        lines.append("【市场环境分析】")
        lines.append(market_analysis[:2000])
        lines.append("")

    # ── 4. Ensemble 量化策略信号 ──
    strategy_labels = {
        'momentum_top': '【量化策略1】动量延续 Top 30（近期强势，有望续强）',
        'reversion_top': '【量化策略2】均值回归 Top 30（超跌反弹机会）',
        'lowvol_top': '【量化策略3】低波动防御 Top 30（稳健型基金）',
    }
    for strategy_key, label in strategy_labels.items():
        codes = ensemble.get(strategy_key, [])[:30]
        if not codes:
            continue
        lines.append(label)
        lines.append("  代码|名称|类型|1周|1月|vsMA20|RSI|Beta|波动率")
        for code in codes:
            match = merged[merged['fund_code'] == code]
            if match.empty:
                continue
            r = match.iloc[0]
            rsi_str = f"{r['rsi_14']:.0f}" if pd_notna(r.get('rsi_14')) else 'N/A'
            beta_str = f"{r['beta']:.2f}" if pd_notna(r.get('beta')) else 'N/A'
            lines.append(
                "  {}|{}|{}|{:+.1f}%|{:+.1f}%|{:+.1f}%|{}|{}|{:.1f}%".format(
                    code,
                    str(r['fund_name'])[:12],
                    r.get('fund_type_main', ''),
                    r.get('ret_5d', 0) * 100,
                    r['ret_20d'] * 100,
                    r.get('pos_vs_ma20', 0) * 100,
                    rsi_str,
                    beta_str,
                    r.get('volatility_60d', 0) * 100,
                ))
        lines.append("")

    # ── 5. 综合评分 Top 20 / Bottom 15 ──
    if 'composite_score' in merged.columns:
        lines.append("【综合评分 Top 20】（全市场评分最高）")
        for _, r in merged.nlargest(20, 'composite_score').iterrows():
            rsi_str = f"{r['rsi_14']:.0f}" if pd_notna(r.get('rsi_14')) else 'N/A'
            lines.append("  {}|{}|{}|1月:{:+.1f}%|3月:{:+.1f}%|波动:{:.1f}%|RSI:{}".format(
                r['fund_code'], str(r['fund_name'])[:12], r.get('fund_type_main', ''),
                r['ret_20d'] * 100, r['ret_60d'] * 100,
                r.get('volatility_60d', 0) * 100, rsi_str))
        lines.append("")
        lines.append("【综合评分 Bottom 15】（全市场评分最低）")
        for _, r in merged.nsmallest(15, 'composite_score').iterrows():
            rsi_str = f"{r['rsi_14']:.0f}" if pd_notna(r.get('rsi_14')) else 'N/A'
            lines.append("  {}|{}|{}|1月:{:+.1f}%|3月:{:+.1f}%|波动:{:.1f}%|RSI:{}".format(
                r['fund_code'], str(r['fund_name'])[:12], r.get('fund_type_main', ''),
                r['ret_20d'] * 100, r['ret_60d'] * 100,
                r.get('volatility_60d', 0) * 100, rsi_str))
        lines.append("")
    else:
        # fallback: year_1
        lines.append("【年收益 Top 20】")
        for _, r in merged.nlargest(20, 'year_1').iterrows():
            lines.append("  {}|{}|{}|1月:{:+.1f}%|年:{:+.1f}%".format(
                r['fund_code'], str(r['fund_name'])[:12], r.get('fund_type_main', ''),
                r['month_1'] * 100, r['year_1'] * 100))
        lines.append("")
        lines.append("【年收益 Bottom 15】")
        for _, r in merged.nsmallest(15, 'year_1').iterrows():
            lines.append("  {}|{}|{}|1月:{:+.1f}%|年:{:+.1f}%".format(
                r['fund_code'], str(r['fund_name'])[:12], r.get('fund_type_main', ''),
                r['month_1'] * 100, r['year_1'] * 100))
        lines.append("")

    # ── 6. Few-shot 历史成功案例 ──
    few_shot_text = _load_few_shot_examples(state['target_date'], max_examples=2)
    if few_shot_text:
        lines.append("【历史成功参考】\n{}\n".format(few_shot_text))

    user_content = '\n'.join(lines)
    print(f"[Predictor] 蒸馏提示构建完成 ({len(lines)} 行, {len(user_content)} 字符), 调用 DeepSeek API...")

    system_prompt = """你是一位顶级的中国公募基金量化分析师。你的任务是基于基金全量分析得到的量化信号，预测未来7个交易日内涨幅最大的10只基金和跌幅最大的10只基金。

预测逻辑：
1. **动量延续**：近期强势且有趋势支撑的基金可能延续上涨
2. **均值回归**：过度下跌且RSI超卖的基金可能反弹
3. **波动率分析**：高波动基金在趋势市场中机会大但风险也大
4. **市场环境匹配**：不同市场环境适合不同风格的基金
5. **风险控制**：关注最大回撤指标，避免追高风险
6. **分散化**：尽量选择不同类型/风格的基金，避免集中在单一板块
7. **RSI参考**：RSI>70可能超买，RSI<30可能超卖
8. **Beta参考**：Beta>1高弹性，Beta<1防御性
9. **量化策略信号**：三个策略（动量/均值回归/低波动）已从全市场筛选，综合参考

请严格按照以下 JSON 格式输出，不要包含其他内容：
```json
{
  "top_10": [
    {"fund_code": "000001", "fund_name": "基金名称", "reason": "预测理由", "expected_return_7d": "+5.2%"},
    ...
  ],
  "bottom_10": [
    {"fund_code": "000002", "fund_name": "基金名称", "reason": "预测理由", "expected_return_7d": "-3.8%"},
    ...
  ],
  "summary": "整体市场判断和预测说明"
}
```"""

    user_prompt = "{}\n\n请预测未来7个交易日涨幅 Top 10 和跌幅 Bottom 10。返回 JSON 格式。".format(user_content)

    llm = _build_llm()
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    # 解析 JSON 输出
    content = response.content.strip()
    predictions = _parse_prediction_json(content)

    # 后处理：同类基金分散化
    fund_type_map = {}
    if 'fund_type_main' in merged.columns:
        for _, row in merged.iterrows():
            fund_type_map[row['fund_code']] = row.get('fund_type_main', '未知')
    predictions = _diversify_predictions(predictions, fund_type_map, max_per_type=3)

    elapsed = time.time() - t0
    print(f"[Predictor] 预测完成: Top 10 + Bottom 10（已分散化, 总耗时 {elapsed:.1f}s）")
    return {'predictions': predictions}


def _parse_prediction_json(content: str) -> list:
    """从 LLM 输出中解析预测 JSON"""
    # 尝试提取 ```json ... ``` 块
    import re
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
    if json_match:
        content = json_match.group(1).strip()

    # 尝试直接解析
    try:
        parsed = json.loads(content)
        return parsed
    except json.JSONDecodeError:
        pass

    # 尝试找第一个 { 到最后一个 }
    try:
        start = content.index('{')
        end = content.rindex('}') + 1
        parsed = json.loads(content[start:end])
        return parsed
    except (ValueError, json.JSONDecodeError):
        print("[Predictor] 警告: 无法解析 LLM 输出为 JSON，返回原始文本")
        return {'raw': content}


def node_ensemble_models(state: PredictState) -> dict:
    """Agent 2.5: Ensemble — 运行3个量化策略生成信号"""
    t0 = time.time()
    print("[Ensemble] 运行量化策略模型（全量分析）...")

    candidates = state.get('fund_candidates')
    indicators = state.get('tech_indicators')
    finfo = state.get('fund_info')

    if candidates is None or candidates.empty or indicators is None or indicators.empty:
        print("[Ensemble] 数据不足，跳过")
        return {}

    # 合并数据
    merged = candidates.merge(indicators, on='fund_code', how='inner')
    if finfo is not None and not finfo.empty:
        merged = merged.merge(
            finfo[['fund_code', 'fund_type', 'fund_company', 'fund_manager', 'fund_size']],
            on='fund_code', how='left'
        )

    print(f"[Ensemble] 合并后 {len(merged)} 只基金进行策略评分 ({time.time()-t0:.1f}s)")

    # 运行三个策略
    signals = run_all_strategies(merged)
    print(f"[Ensemble] 动量策略: {len(signals.get('momentum_top', []))} 只")
    print(f"[Ensemble] 均值回归: {len(signals.get('reversion_top', []))} 只")
    print(f"[Ensemble] 低波动策略: {len(signals.get('lowvol_top', []))} 只")

    elapsed = time.time() - t0
    print(f"[Ensemble] 全部策略完成，总耗时 {elapsed:.1f}s")
    return {'ensemble_signals': signals}


def _diversify_predictions(predictions: dict, fund_type_map: dict, max_per_type: int = 3) -> dict:
    """后处理：同类基金最多入选 max_per_type 只，强制分散
    fund_type_map: {fund_code: fund_type_main_str} 映射"""
    if not predictions or 'top_10' not in predictions:
        return predictions

    def diversify(fund_list, max_per):
        type_count = {}
        result = []
        for f in fund_list:
            code = f.get('fund_code', '')
            ftype = fund_type_map.get(code, '未知')
            if type_count.get(ftype, 0) >= max_per:
                continue
            type_count[ftype] = type_count.get(ftype, 0) + 1
            result.append(f)
            if len(result) >= 10:
                break
        return result

    predictions['top_10'] = diversify(predictions.get('top_10', []), max_per_type)
    predictions['bottom_10'] = diversify(predictions.get('bottom_10', []), max_per_type)
    return predictions


def _load_few_shot_examples(target_date: str, max_examples: int = 3) -> str:
    """从历史回测结果加载成功预测案例作为 Few-shot 示例"""
    try:
        result_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'result_agents')
        pattern = os.path.join(result_dir, 'backtest_*.json')
        files = sorted(glob.glob(pattern))
    except Exception:
        return ''

    examples = []
    for fp in files:
        if len(examples) >= max_examples:
            break
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        if 'error' in data:
            continue
        da = data.get('direction_accuracy', {})
        gain = da.get('gain', {})
        if gain.get('accuracy', 0) >= 0.8 and gain.get('correct_count', 0) >= 8:
            details = data.get('predicted_vs_actual_details', {})
            top10 = details.get('top10', [])
            bottom10 = details.get('bottom10', [])
            lines = []
            for item in (top10 + bottom10)[:6]:
                lines.append(f"  {item.get('fund_code','')} {item.get('fund_name','')}: "
                             f"预测{item.get('predicted_return','')} / 实际{item.get('actual_return','')}")
            if lines:
                date_str = os.path.basename(fp).replace('backtest_', '').replace('.json', '')
                examples.append(f"[{date_str} 成功案例]\n" + '\n'.join(lines))
    if examples:
        return '\n\n'.join(examples)
    return ''


def _check_has_data(state: PredictState) -> str:
    """条件路由：有数据 → 继续；无数据 → 直接 END"""
    if state.get('error') or state.get('fund_candidates') is None or (
        hasattr(state.get('fund_candidates'), 'empty') and state['fund_candidates'].empty
    ):
        return 'skip'
    return 'continue'


def node_skip_report(state: PredictState) -> dict:
    """数据不足时的跳过报告"""
    print("[Reporter] 候选基金数据不足，跳过预测")
    error = state.get('error', '数据不足')
    return {'report': f"[预测失败] {error}\n基准日期: {state['target_date']}\n请检查数据库是否有该日期附近的数据。"}


def node_format_report(state: PredictState) -> dict:
    """Agent 5: Reporter — 格式化输出最终报告"""
    t0 = time.time()
    print("[Reporter] 生成预测报告...")
    predictions = state.get('predictions')
    if predictions is None:
        predictions = {}
    market_analysis = state.get('market_analysis', '')
    index_trend = state.get('index_trend', {})

    # 如果是原始文本（解析失败）
    if isinstance(predictions, dict) and 'raw' in predictions:
        return {'report': predictions['raw']}

    target_date = state['target_date']
    report_date = datetime.now().strftime('%Y-%m-%d %H:%M')

    lines = []
    lines.append(f"{'=' * 70}")
    lines.append(f"  基金未来 {PREDICT_DAYS} 个交易日走势预测")
    lines.append(f"  基准日期: {target_date}")
    lines.append(f"  报告生成: {report_date}")
    lines.append(f"  数据截至: {state.get('latest_data_date', '未知')}")
    if state.get('resolved_date'):
        lines.append(f"  数据实际日期: {state['resolved_date']}")
    lines.append(f"  API 模型: {DEEPSEEK_CONFIG['model']}")
    lines.append(f"{'=' * 70}")

    # 指数行情
    lines.append("")
    lines.append("[市场指数参考]")
    lines.append("-" * 40)
    for name, info in index_trend.items():
        lines.append(f"  {name}: {info.get('latest_close', '')} | "
                     f"20日 {info.get('ret_20d',0):+.2f}% | "
                     f"60日 {info.get('ret_60d',0):+.2f}% | "
                     f"趋势: {info.get('trend','')}")

    # Top 10
    top10 = (predictions or {}).get('top_10', [])
    lines.append("")
    lines.append(">>> 预测涨幅 TOP 10 <<<")
    lines.append("-" * 40)
    if top10:
        for i, fund in enumerate(top10, 1):
            lines.append(f"  {i}. {fund.get('fund_code', '')} {fund.get('fund_name', '')}")
            lines.append(f"     预期收益: {fund.get('expected_return_7d', '')}")
            lines.append(f"     理由: {fund.get('reason', '')}")
            lines.append("")
    else:
        lines.append("  (无数据)")

    # Bottom 10
    bottom10 = predictions.get('bottom_10', [])
    lines.append("")
    lines.append(">>> 预测跌幅 BOTTOM 10 <<<")
    lines.append("-" * 40)
    if bottom10:
        for i, fund in enumerate(bottom10, 1):
            lines.append(f"  {i}. {fund.get('fund_code', '')} {fund.get('fund_name', '')}")
            lines.append(f"     预期收益: {fund.get('expected_return_7d', '')}")
            lines.append(f"     理由: {fund.get('reason', '')}")
            lines.append("")
    else:
        lines.append("  (无数据)")

    # Summary
    summary = predictions.get('summary', '')
    if summary:
        lines.append("")
        lines.append("[市场综合判断]")
        lines.append("-" * 40)
        lines.append(f"  {summary}")

    # Market analysis
    lines.append("")
    lines.append("[市场环境分析详情]")
    lines.append("-" * 40)
    lines.append(market_analysis)

    lines.append("")
    lines.append(f"{'=' * 70}")
    lines.append("  * 免责声明：预测仅供参考，不构成投资建议 *")
    lines.append(f"{'=' * 70}")

    elapsed = time.time() - t0
    print(f"[Reporter] 报告生成完成 ({elapsed:.1f}s)")
    return {'report': '\n'.join(lines)}


# ──────────────────── 构建图 ────────────────────

def build_workflow() -> StateGraph:
    """构建 LangGraph 多智能体工作流"""
    workflow = StateGraph(PredictState)

    # 注册节点
    workflow.add_node('collect_data', node_collect_data)
    workflow.add_node('compute_indicators', node_compute_indicators)
    workflow.add_node('ensemble_models', node_ensemble_models)
    workflow.add_node('analyze_market', node_analyze_market)
    workflow.add_node('predict', node_predict)
    workflow.add_node('skip_report', node_skip_report)
    workflow.add_node('format_report', node_format_report)

    # 边：DataCollector → 条件路由
    workflow.set_entry_point('collect_data')
    workflow.add_conditional_edges(
        'collect_data',
        _check_has_data,
        {
            'continue': 'compute_indicators',
            'skip': 'skip_report',
        }
    )
    workflow.add_edge('compute_indicators', 'ensemble_models')
    workflow.add_edge('ensemble_models', 'analyze_market')
    workflow.add_edge('analyze_market', 'predict')
    workflow.add_edge('predict', 'format_report')
    workflow.add_edge('format_report', END)
    workflow.add_edge('skip_report', END)

    return workflow.compile()


def run_prediction(target_date: str, backtest_mode: bool = False) -> dict:
    """运行完整预测流程

    Args:
        target_date: 基准日期，格式 'YYYY-MM-DD'
        backtest_mode: 回测模式，从 fund_values 推算特征（适合历史日期）

    Returns:
        包含完整预测结果的 state dict
    """
    # 验证日期格式
    try:
        datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        print(f"[ERROR] 日期格式错误: {target_date}，请使用 YYYY-MM-DD 格式")
        return {'error': f'日期格式错误: {target_date}'}

    initial_state: PredictState = {
        'target_date': target_date,
        'backtest_mode': backtest_mode,
        'latest_data_date': None,
        'report_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'fund_candidates': None,
        'nav_history': None,
        'index_data': None,
        'tech_indicators': None,
        'index_trend': {},
        'fund_info': None,
        'sector_info': None,
        'corr_info': None,
        'ensemble_signals': None,
        'market_analysis': None,
        'predictions': None,
        'report': None,
    }

    graph = build_workflow()
    final_state = graph.invoke(initial_state)
    return final_state
