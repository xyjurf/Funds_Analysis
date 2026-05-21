"""外部数据模块：利用现有数据库资源提取行业/板块/分红等补充信息

注意：当前数据库没有专门的板块资金流表。
以下函数均基于已有数据（fund_info, fund_dividends, index_data）提取有用信号。

如需真正引入外部数据（板块资金流向、北向资金、宏观指标），
需要额外搭建数据采集流程（例如通过 eastmoney API 或 akshare）。
"""

import pandas as pd
import numpy as np
from typing import Optional
from config import DB_CONFIG


def get_sector_composition(index_df: pd.DataFrame = None) -> dict:
    """
    从 fund_info 统计各类型基金的规模分布，判断市场风格。

    Returns:
        {
            'style': '大盘' or '小盘' or '均衡',
            'top_sectors': [('混合型', 45.2%), ...],
            'total_equity_size': 123456.78
        }
    """
    import pymysql
    conn = pymysql.connect(**DB_CONFIG)
    try:
        # 统计各主类型基金的规模和数量
        df = pd.read_sql("""
            SELECT fund_type_main,
                   COUNT(*) as fund_count,
                   SUM(fund_size) as total_size
            FROM fund_info
            WHERE fund_type_main IS NOT NULL
              AND fund_size > 0
            GROUP BY fund_type_main
            ORDER BY total_size DESC
        """, conn)
        if df.empty:
            return {}

        total = df['total_size'].sum()
        result = {
            'top_sectors': [
                (r['fund_type_main'],
                 float(r['total_size']) / total * 100 if total > 0 else 0,
                 int(r['fund_count']))
                for _, r in df.head(8).iterrows()
            ],
            'total_equity_size': float(total),
        }

        # 判断市场风格（以 沪深300 vs 中证1000 的相对表现为 proxy）
        if index_df is not None and not index_df.empty:
            idx = index_df[index_df['index_code'] == '000300'].sort_values('trade_date')
            idx_small = index_df[index_df['index_code'] == '000852'].sort_values('trade_date')
            if len(idx) >= 20 and len(idx_small) >= 20:
                ret_large = (idx['close_price'].iloc[-1] - idx['close_price'].iloc[-20]) / idx['close_price'].iloc[-20]
                ret_small = (idx_small['close_price'].iloc[-1] - idx_small['close_price'].iloc[-20]) / idx_small['close_price'].iloc[-20]
                if ret_large > ret_small + 0.02:
                    result['style'] = '大盘风格偏强'
                elif ret_small > ret_large + 0.02:
                    result['style'] = '小盘风格偏强'
                else:
                    result['style'] = '大小盘均衡'
                result['large_vs_small_20d'] = f"沪深300:{ret_large*100:+.1f}% vs 中证1000:{ret_small*100:+.1f}%"

        return result
    finally:
        conn.close()


def get_recent_dividends(fund_codes: list, target_date: str, lookback_days: int = 30) -> dict:
    """
    查询基金近期是否有分红（分红会导致净值异常下降，需注意）。

    Returns:
        {fund_code: {'dividend_date': '2025-06-01', 'unit_dividend': 0.05, 'nav_on_date': 1.50}}
    """
    if not fund_codes:
        return {}
    import pymysql
    conn = pymysql.connect(**DB_CONFIG)
    try:
        placeholders = ','.join(['%s'] * len(fund_codes))
        df = pd.read_sql(f"""
            SELECT fund_code, dividend_date, unit_dividend, nav_on_date
            FROM fund_dividends
            WHERE fund_code IN ({placeholders})
              AND dividend_date <= %s
              AND dividend_date >= DATE_SUB(%s, INTERVAL %s DAY)
            ORDER BY dividend_date DESC
        """, conn, params=fund_codes + [target_date, target_date, lookback_days])
        result = {}
        for _, row in df.iterrows():
            code = str(row['fund_code']).strip().zfill(6)
            result[code] = {
                'dividend_date': str(row['dividend_date']),
                'unit_dividend': float(row['unit_dividend']) if row['unit_dividend'] else 0,
                'nav_on_date': float(row['nav_on_date']) if row['nav_on_date'] else None,
            }
        return result
    finally:
        conn.close()


def get_index_correlation(index_df: pd.DataFrame) -> dict:
    """
    计算各指数之间的相关性，判断当前市场是普涨还是分化。

    Returns:
        {'avg_correlation': 0.85, 'market_breadth': '普涨'/'分化'/'不确定'}
    """
    if index_df is None or index_df.empty:
        return {}

    pivoted = index_df.pivot_table(
        index='trade_date', columns='index_code', values='close_price'
    ).dropna()
    if len(pivoted) < 10:
        return {}

    returns = pivoted.pct_change().dropna()
    corr_matrix = returns.corr()
    # 取上三角平均
    n = corr_matrix.shape[0]
    if n < 2:
        return {}
    triu_sum = 0
    triu_cnt = 0
    for i in range(n):
        for j in range(i + 1, n):
            triu_sum += corr_matrix.iloc[i, j]
            triu_cnt += 1
    avg_corr = triu_sum / triu_cnt if triu_cnt else 0

    if avg_corr > 0.85:
        breadth = '普涨/普跌（高度同步）'
    elif avg_corr > 0.65:
        breadth = '结构性行情（部分同步）'
    else:
        breadth = '严重分化（板块轮动快）'

    return {
        'avg_correlation': round(avg_corr, 3),
        'market_breadth': breadth,
    }
