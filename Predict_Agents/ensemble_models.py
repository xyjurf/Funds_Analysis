"""量化策略模型 Ensemble：动量延续 + 均值回归 + 低波动防御

为 LLM 提供多个量化信号作为参考，辅助最终预测判断。
每个策略输出 Top 20 候选，LLM 综合决策。
"""

import pandas as pd
import numpy as np
from typing import Optional


def momentum_strategy(merged: pd.DataFrame, top_n: int = 50) -> list:
    """
    动量延续策略：近期强者恒强。

    因子：
    - ret_20d（近1月收益）
    - momentum_5d（近5日动量）
    - pos_vs_ma20（价格处在均线上方）
    综合评分选 Top N。
    """
    if merged is None or merged.empty:
        return []

    df = merged.copy()

    # 标准化各因子为 0~1 分
    scores = []
    for factor in ['ret_20d', 'momentum_5d', 'pos_vs_ma20']:
        if factor not in df.columns:
            continue
        valid = df[factor].notna()
        if valid.sum() < 10:
            continue
        col = df[factor].copy()
        col_min, col_max = col.min(), col.max()
        if col_max - col_min > 1e-10:
            df[f'score_{factor}'] = (col - col_min) / (col_max - col_min)
        else:
            df[f'score_{factor}'] = 0.5
        scores.append(f'score_{factor}')

    if not scores:
        return []

    df['momentum_score'] = df[scores].mean(axis=1)
    top = df.nlargest(top_n, 'momentum_score')
    return top['fund_code'].tolist()


def mean_reversion_strategy(merged: pd.DataFrame, top_n: int = 50) -> list:
    """
    均值回归策略：超跌反弹。

    因子：
    - pos_vs_ma60（低于均线越多越好，负值越大越好）
    - max_drawdown_60d（近期回撤大）
    - rsi_14（RSI < 40 表示超卖）
    综合评分选 Top N。
    """
    if merged is None or merged.empty:
        return []

    df = merged.copy()
    scores = []

    # pos_vs_ma60 负值越大（低于均线越多）得分越高
    if 'pos_vs_ma60' in df.columns:
        col = df['pos_vs_ma60'].copy()
        # 越低越好，取负号后归一化
        col = col * -1
        valid = col.notna()
        if valid.sum() >= 10:
            col_min, col_max = col.min(), col.max()
            if col_max - col_min > 1e-10:
                df['score_reversion'] = (col - col_min) / (col_max - col_min)
                scores.append('score_reversion')

    # 回撤大得分高
    if 'max_drawdown_60d' in df.columns:
        valid = df['max_drawdown_60d'].notna()
        if valid.sum() >= 10:
            col_min, col_max = df['max_drawdown_60d'].min(), df['max_drawdown_60d'].max()
            if col_max - col_min > 1e-10:
                df['score_drawdown'] = (df['max_drawdown_60d'] - col_min) / (col_max - col_min)
                scores.append('score_drawdown')

    # RSI 超卖得分（RSI 越低越好，<40超卖）
    if 'rsi_14' in df.columns:
        valid = df['rsi_14'].notna()
        if valid.sum() >= 10:
            rsi = df['rsi_14'].copy()
            rsi_score = 1.0 - (rsi / 100.0)  # RSI 越低得分越高
            df['score_rsi'] = rsi_score
            scores.append('score_rsi')

    if not scores:
        return []

    df['reversion_score'] = df[scores].mean(axis=1)
    top = df.nlargest(top_n, 'reversion_score')
    return top['fund_code'].tolist()


def low_volatility_strategy(merged: pd.DataFrame, top_n: int = 50) -> list:
    """
    低波动防御策略：波动小、回撤小的稳健基金。

    因子：
    - volatility_60d（波动率越低越好）
    - max_drawdown_60d（回撤越小越好）
    - beta（越低越好，<1 抗跌）
    综合评分选 Top N。
    """
    if merged is None or merged.empty:
        return []

    df = merged.copy()
    scores = []

    # 波动率越低越好
    if 'volatility_60d' in df.columns:
        valid = df['volatility_60d'].notna()
        if valid.sum() >= 10:
            col = df['volatility_60d'].copy()
            col = col * -1  # 越低越好
            col_min, col_max = col.min(), col.max()
            if col_max - col_min > 1e-10:
                df['score_vol'] = (col - col_min) / (col_max - col_min)
                scores.append('score_vol')

    # 回撤越小越好
    if 'max_drawdown_60d' in df.columns:
        valid = df['max_drawdown_60d'].notna()
        if valid.sum() >= 10:
            col = df['max_drawdown_60d'].copy() * -1
            col_min, col_max = col.min(), col.max()
            if col_max - col_min > 1e-10:
                df['score_dd'] = (col - col_min) / (col_max - col_min)
                scores.append('score_dd')

    # Beta 越低越好
    if 'beta' in df.columns:
        valid = df['beta'].notna()
        if valid.sum() >= 10:
            col = df['beta'].copy() * -1  # Beta 0~1 比 >1 好
            col_min, col_max = col.min(), col.max()
            if col_max - col_min > 1e-10:
                df['score_beta'] = (col - col_min) / (col_max - col_min)
                scores.append('score_beta')

    if not scores:
        return []

    df['lowvol_score'] = df[scores].mean(axis=1)
    top = df.nlargest(top_n, 'lowvol_score')
    return top['fund_code'].tolist()


def run_all_strategies(merged: pd.DataFrame) -> dict:
    """运行全部三个策略，返回各策略的推荐列表"""
    import time
    t0 = time.time()
    result = {
        'momentum_top': momentum_strategy(merged),
        'reversion_top': mean_reversion_strategy(merged),
        'lowvol_top': low_volatility_strategy(merged),
    }
    elapsed = time.time() - t0
    total = len(merged) if merged is not None else 0
    print(f"[Ensemble] 全量分析 {total} 只基金完成，3策略耗时 {elapsed:.1f}s", flush=True)
    return result
