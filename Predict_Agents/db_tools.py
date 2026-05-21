"""数据库查询工具：获取基金数据、净值、指数信息"""

import pymysql
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
from config import DB_CONFIG, TOP_N_CANDIDATES, NAV_LOOKBACK_DAYS


def get_conn():
    return pymysql.connect(**DB_CONFIG)


def get_latest_data_date() -> Optional[str]:
    """获取 fund_data 表最新数据日期"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(date) FROM fund_data")
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def get_nearest_fund_data_date(target_date: str) -> Optional[str]:
    """获取 fund_data 表中 <= target_date 的最近日期"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(date) FROM fund_data WHERE date <= %s",
                (target_date,)
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None
    finally:
        conn.close()


def get_fund_candidates(target_date: str, top_n: int = TOP_N_CANDIDATES) -> pd.DataFrame:
    """
    筛选权益型候选基金（混合型+股票型+指数型），按近1年涨幅降序。
    始终使用 fund_data 最新可用快照（因为 fund_data 不是每日更新）。

    target_date 用于决定 fund_values 查询的时间边界，不影响候选池。
    返回包含 fund_code, fund_name, fund_type_main, 阶段涨幅的 DataFrame。
    """
    conn = get_conn()
    try:
        # 取 fund_data 最新日期
        latest_data_date = get_latest_data_date()
        if latest_data_date is None:
            return pd.DataFrame()

        query = """
        SELECT
            f.fund_code,
            f.fund_name,
            i.fund_type_main,
            f.date,
            f.unit_nav,
            f.month_1,
            f.month_3,
            f.month_6,
            f.year_1,
            f.this_year,
            f.daily_growth
        FROM fund_data f
        LEFT JOIN fund_info i ON LPAD(f.fund_code, 6, '0') = i.fund_code
        WHERE f.date = %s
          AND f.year_1 IS NOT NULL
        ORDER BY f.year_1 DESC
        LIMIT %s
        """
        df = pd.read_sql(query, conn, params=(latest_data_date, top_n))
        # fund_code 补零为 6 位字符串
        if 'fund_code' in df.columns:
            df['fund_code'] = df['fund_code'].apply(
                lambda x: str(x).zfill(6) if pd.notna(x) else x
            )
        if not df.empty:
            df.attrs['actual_date'] = latest_data_date
        return df
    finally:
        conn.close()


def get_fund_nav_history(fund_codes: list, target_date: str, lookback_days: int = NAV_LOOKBACK_DAYS) -> pd.DataFrame:
    """
    批量获取基金历史净值数据。
    取 target_date 前 lookback_days 个交易日的数据。
    """
    if not fund_codes:
        return pd.DataFrame()

    conn = get_conn()
    try:
        placeholders = ','.join(['%s'] * len(fund_codes))
        query = f"""
        SELECT fund_code, net_value_date, unit_net_value, cumulative_net_value, daily_growth_rate
        FROM fund_values
        WHERE fund_code IN ({placeholders})
          AND net_value_date <= %s
          AND net_value_date >= DATE_SUB(%s, INTERVAL {lookback_days} DAY)
        ORDER BY fund_code, net_value_date
        """
        params = fund_codes + [target_date, target_date]
        df = pd.read_sql(query, conn, params=params)
        return df
    finally:
        conn.close()


def get_market_index_data(target_date: str, lookback_days: int = NAV_LOOKBACK_DAYS) -> pd.DataFrame:
    """获取基准指数历史数据"""
    conn = get_conn()
    try:
        query = """
        SELECT index_code, index_name, trade_date, close_price
        FROM index_data
        WHERE trade_date <= %s
          AND trade_date >= DATE_SUB(%s, INTERVAL %s DAY)
        ORDER BY index_code, trade_date
        """
        df = pd.read_sql(query, conn, params=(target_date, target_date, lookback_days))
        return df
    finally:
        conn.close()


def get_latest_fund_values_date() -> Optional[str]:
    """获取 fund_values 表最新日期"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(net_value_date) FROM fund_values")
            row = cur.fetchone()
            return str(row[0]) if row and row[0] else None
    finally:
        conn.close()


def get_fund_info_batch(fund_codes: list) -> pd.DataFrame:
    """获取基金详细信息（类型、公司、经理、规模、成立日期）"""
    if not fund_codes:
        return pd.DataFrame()
    conn = get_conn()
    try:
        placeholders = ','.join(['%s'] * len(fund_codes))
        query = f"""
        SELECT fund_code, fund_name, fund_type, fund_type_main,
               fund_company, fund_manager, establishment_date, fund_size
        FROM fund_info
        WHERE fund_code IN ({placeholders})
        """
        df = pd.read_sql(query, conn, params=fund_codes)
        return df
    finally:
        conn.close()


def get_fund_candidates_backtest(target_date: str, top_n: int = TOP_N_CANDIDATES) -> pd.DataFrame:
    """
    专用于回测：不依赖 fund_data 快照，直接用 fund_info + fund_values
    计算任意历史日期的候选基金（优化版：定向拉取快照，避免全量历史扫描）。

    流程：fund_info 获取权益型 → fund_values 快照算收益 → 按年收益排序
    返回全部有效基金（不做 top N 截断）。
    """
    import time
    t0 = time.time()
    conn = get_conn()
    try:
        # 1. 获取权益型基金
        print(f"[BacktestDB] 开始获取权益型基金列表...", flush=True)
        funds_df = pd.read_sql("""
            SELECT fund_code, fund_name, fund_type_main
            FROM fund_info
            WHERE (establishment_date IS NULL OR establishment_date <= %s)
        """, conn, params=(target_date,))
        if funds_df.empty:
            return pd.DataFrame()
        funds_df['fund_code'] = funds_df['fund_code'].astype(str).str.strip().str.zfill(6)
        total_funds = len(funds_df)
        print(f"[BacktestDB] 权益型基金: {total_funds} 只 ({time.time()-t0:.1f}s)", flush=True)

        name_map = dict(zip(funds_df['fund_code'], funds_df['fund_name']))
        type_map = dict(zip(funds_df['fund_code'], funds_df['fund_type_main']))
        all_codes = funds_df['fund_code'].tolist()

        # 2. 取每只基金在 target_date 当日或之前的最近净值（分批）
        BATCH_SIZE = 2000
        latest_nav_all = []
        n_batches = (len(all_codes) + BATCH_SIZE - 1) // BATCH_SIZE
        for batch_idx, batch_start in enumerate(range(0, len(all_codes), BATCH_SIZE)):
            batch = all_codes[batch_start:batch_start + BATCH_SIZE]
            ph = ','.join(['%s'] * len(batch))
            sql = f"""
            SELECT t.fund_code, t.unit_net_value, t.net_value_date
            FROM fund_values t
            INNER JOIN (
                SELECT fund_code, MAX(net_value_date) AS max_date
                FROM fund_values
                WHERE fund_code IN ({ph}) AND net_value_date <= %s
                GROUP BY fund_code
            ) latest ON t.fund_code = latest.fund_code AND t.net_value_date = latest.max_date
            WHERE t.unit_net_value > 0
            """
            df_part = pd.read_sql(sql, conn, params=(batch + [target_date]))
            latest_nav_all.append(df_part)
            print(f"[BacktestDB] 最新净值批 {batch_idx+1}/{n_batches}: {len(df_part)} 只", flush=True)
        latest_nav = pd.concat(latest_nav_all, ignore_index=True) if latest_nav_all else pd.DataFrame()
        if latest_nav.empty:
            return pd.DataFrame()
        latest_nav['fund_code'] = latest_nav['fund_code'].astype(str).str.strip().str.zfill(6)
        print(f"[BacktestDB] 最新净值获取完成 ({time.time()-t0:.1f}s)", flush=True)

        # 3. 定向拉取各区间快照：1月前(35天)、3月前(95天)、6月前(185天)、1年前(370天)
        snapshot_dates = [35, 95, 185, 370]
        snapshot_labels = ['1月前', '3月前', '6月前', '1年前']
        snapshot_navs = {}
        for sdi, lookback in enumerate(snapshot_dates):
            cutoff = pd.Timestamp(target_date) - pd.Timedelta(days=lookback)
            rows = []
            n_batches = (len(all_codes) + BATCH_SIZE - 1) // BATCH_SIZE
            for batch_idx, batch_start in enumerate(range(0, len(all_codes), BATCH_SIZE)):
                batch = all_codes[batch_start:batch_start + BATCH_SIZE]
                ph = ','.join(['%s'] * len(batch))
                sql = f"""
                SELECT t.fund_code, t.unit_net_value
                FROM fund_values t
                INNER JOIN (
                    SELECT fund_code, MAX(net_value_date) AS md
                    FROM fund_values
                    WHERE fund_code IN ({ph})
                      AND net_value_date <= %s
                      AND net_value_date >= DATE_SUB(%s, INTERVAL 10 DAY)
                    GROUP BY fund_code
                ) s ON t.fund_code = s.fund_code AND t.net_value_date = s.md
                WHERE t.unit_net_value > 0
                """
                df_part = pd.read_sql(sql, conn, params=(batch + [str(cutoff.date()), str(cutoff.date())]))
                rows.append(df_part)
            snap = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
            if not snap.empty:
                snap['fund_code'] = snap['fund_code'].astype(str).str.strip().str.zfill(6)
                snap['unit_net_value'] = snap['unit_net_value'].astype(float)
            snapshot_navs[lookback] = snap
            print(f"[BacktestDB] {snapshot_labels[sdi]}快照: {len(snap)} 只 ({time.time()-t0:.1f}s)", flush=True)

        # 4. 合并计算收益率
        lnav = latest_nav.set_index('fund_code')['unit_net_value'].astype(float)

        def get_snapshot_nav(lookback):
            snap = snapshot_navs.get(lookback)
            if snap is not None and not snap.empty:
                return snap.set_index('fund_code')['unit_net_value']
            return pd.Series(dtype=float)

        nav_35d = get_snapshot_nav(35)
        nav_95d = get_snapshot_nav(95)
        nav_185d = get_snapshot_nav(185)
        nav_370d = get_snapshot_nav(370)

        results = []
        for idx, code in enumerate(lnav.index):
            if idx > 0 and idx % 2000 == 0:
                print(f"[BacktestDB] 收益率计算: {idx}/{len(lnav.index)} ({time.time()-t0:.1f}s)", flush=True)
            current = lnav[code]
            if not current or current <= 0:
                continue

            def safe_ret(past_series):
                if code in past_series.index:
                    past = past_series[code]
                    if past and past > 0:
                        return (current - past) / past
                return None

            ret_1m = safe_ret(nav_35d)
            ret_3m = safe_ret(nav_95d)
            ret_6m = safe_ret(nav_185d)
            ret_1y = safe_ret(nav_370d)

            if ret_1m is None:
                continue

            results.append({
                'fund_code': code,
                'fund_name': name_map.get(code, ''),
                'fund_type_main': type_map.get(code, ''),
                'date': target_date,
                'unit_nav': current,
                'month_1': ret_1m,
                'month_3': ret_3m if ret_3m is not None else ret_1m,
                'month_6': ret_6m if ret_6m is not None else ret_3m or ret_1m,
                'year_1': ret_1y if ret_1y is not None else ret_6m or ret_3m or ret_1m,
            })

        result_df = pd.DataFrame(results)
        if result_df.empty:
            return result_df

        # 按 year_1 降序排列，但不截断——返回全部有效基金
        result_df = result_df.sort_values('year_1', ascending=False).reset_index(drop=True)
        print(f"[BacktestDB] 候选基金: {len(result_df)} 只 (从 {len(results)} 只有效, 总耗时 {time.time()-t0:.1f}s)", flush=True)
        return result_df

    finally:
        conn.close()


def compute_technical_indicators(nav_df: pd.DataFrame, index_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    从净值历史数据中计算技术指标（增强版）。
    对每只基金计算：近期收益、波动率、最大回撤、趋势指标、RSI、KD、Beta等。

    Args:
        nav_df: 基金净值历史数据
        index_df: 可选，指数数据，用于计算 Beta
    """
    if nav_df.empty:
        return pd.DataFrame()

    nav_df = nav_df.sort_values(['fund_code', 'net_value_date']).copy()
    nav_df['daily_growth_rate'] = nav_df['daily_growth_rate'].str.replace('%', '').astype(float) / 100

    # 预处理指数日收益率用于 Beta 计算
    index_returns = None
    if index_df is not None and not index_df.empty:
        idx = index_df.sort_values('trade_date').copy()
        idx['daily_return'] = idx['close_price'].pct_change()
        # 使用沪深300作为基准
        idx_csi300 = idx[idx['index_code'] == '000300'].copy()
        if not idx_csi300.empty:
            index_returns = idx_csi300.set_index('trade_date')['daily_return']

    results = []
    for code, group in nav_df.groupby('fund_code'):
        group = group.reset_index(drop=True)
        if len(group) < 5:
            continue

        latest = group.iloc[-1]
        latest_nav = latest['unit_net_value']
        prices = group['unit_net_value'].values

        # ── 近期收益 ──
        def period_return(days):
            if len(group) < days + 1:
                return None
            past = group.iloc[-(days + 1)]
            if past['unit_net_value'] and past['unit_net_value'] > 0:
                return (latest_nav - past['unit_net_value']) / past['unit_net_value']
            return None

        ret_5d = period_return(5)
        ret_10d = period_return(10)
        ret_20d = period_return(20)
        ret_60d = period_return(60)

        # ── 波动率 ──
        recent_20 = group.tail(20)['daily_growth_rate']
        volatility_20d = recent_20.std() if len(recent_20) > 5 else None
        recent_60 = group.tail(60)['daily_growth_rate']
        volatility_60d = recent_60.std() if len(recent_60) > 5 else None

        # ── 最大回撤 (60日) ──
        if len(group) >= 10:
            peak = prices[0]
            max_drawdown = 0
            for p in prices:
                if p > peak:
                    peak = p
                dd = (peak - p) / peak if peak > 0 else 0
                if dd > max_drawdown:
                    max_drawdown = dd
        else:
            max_drawdown = None

        # ── MA 趋势 ──
        ma20 = prices[-20:].mean() if len(prices) >= 20 else None
        ma60 = prices[-60:].mean() if len(prices) >= 60 else None
        pos_vs_ma20 = (latest_nav - ma20) / ma20 if ma20 else None
        pos_vs_ma60 = (latest_nav - ma60) / ma60 if ma60 else None

        # ── 近5日动量 ──
        recent_5_returns = group.tail(5)['daily_growth_rate']
        momentum_5d = recent_5_returns.mean() if len(recent_5_returns) >= 3 else None

        # ── RSI(14) ──
        rsi_14 = None
        if len(group) >= 15:
            closes = group['unit_net_value'].values[-15:]
            gains, losses = 0.0, 0.0
            for i in range(1, len(closes)):
                diff = closes[i] - closes[i-1]
                if diff > 0:
                    gains += diff
                else:
                    losses -= diff  # 取正值
            avg_gain = gains / 14
            avg_loss = losses / 14
            if avg_loss == 0:
                rsi_14 = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_14 = 100.0 - (100.0 / (1.0 + rs))

        # ── KD 随机指标(9,3,3) ──
        k_value, d_value = None, None
        if len(group) >= 14:
            recent_high = group['unit_net_value'].rolling(9).max()
            recent_low = group['unit_net_value'].rolling(9).min()
            close = group['unit_net_value']
            rsv = (close - recent_low) / (recent_high - recent_low).replace(0, np.nan) * 100
            k = rsv.ewm(com=2).mean()  # K = 1/3 * RSV + 2/3 * prev_K
            d = k.ewm(com=2).mean()    # D = 1/3 * K + 2/3 * prev_D
            k_value = k.iloc[-1] if not pd.isna(k.iloc[-1]) else None
            d_value = d.iloc[-1] if not pd.isna(d.iloc[-1]) else None

        # ── Beta  vs 沪深300 ──
        beta = None
        if index_returns is not None and len(group) >= 20:
            fund_rets = group['daily_growth_rate'].values[-60:]
            # 对齐日期取最近的60个交易日
            idx_aligned = index_returns.reindex(group['net_value_date'].iloc[-60:]).dropna()
            fund_aligned = group.loc[group['net_value_date'].isin(idx_aligned.index), 'daily_growth_rate']
            if len(fund_aligned) >= 10 and len(idx_aligned) >= 10:
                cov = np.cov(fund_aligned.values[-min(60,len(fund_aligned)):],
                             idx_aligned.values[-min(60,len(idx_aligned)):])
                if cov[1][1] > 1e-10:
                    beta = float(cov[0][1] / cov[1][1])

        # ── 同类排名百分比（近1月在候选基金中的相对位置） ──
        rank_pct_20d = None  # 会在外层重新计算

        results.append({
            'fund_code': code,
            'latest_nav': latest_nav,
            'latest_date': str(latest['net_value_date']),
            'ret_5d': ret_5d,
            'ret_10d': ret_10d,
            'ret_20d': ret_20d,
            'ret_60d': ret_60d,
            'volatility_20d': volatility_20d,
            'volatility_60d': volatility_60d,
            'max_drawdown_60d': max_drawdown,
            'ma20': ma20,
            'ma60': ma60,
            'pos_vs_ma20': pos_vs_ma20,
            'pos_vs_ma60': pos_vs_ma60,
            'momentum_5d': momentum_5d,
            'rsi_14': rsi_14,
            'kd_k': k_value,
            'kd_d': d_value,
            'beta': beta,
        })

    result_df = pd.DataFrame(results)

    # 计算同类排名百分比
    if not result_df.empty and 'ret_20d' in result_df.columns:
        valid = result_df['ret_20d'].notna()
        result_df.loc[valid, 'rank_pct_20d'] = result_df.loc[valid, 'ret_20d'].rank(pct=True)

    return result_df


def get_actual_performance(fund_codes: list, start_date: str, lookforward_days: int = 10) -> pd.DataFrame:
    """
    获取基金在 start_date 之后的实际表现（用于验证预测）。
    取 start_date 之后 lookforward_days 个自然日内的首尾净值计算实际收益率。
    """
    if not fund_codes:
        return pd.DataFrame()

    import time
    t0 = time.time()
    print(f"[DB] 查询 {len(fund_codes)} 只基金的实际表现...", flush=True)
    conn = get_conn()
    try:
        placeholders = ','.join(['%s'] * len(fund_codes))
        # 取 start_date 当日及之后的净值
        query = f"""
        SELECT a.fund_code, a.net_value_date, a.unit_net_value
        FROM fund_values a
        INNER JOIN (
            SELECT fund_code,
                   MIN(net_value_date) AS first_date,
                   MAX(net_value_date) AS last_date
            FROM fund_values
            WHERE fund_code IN ({placeholders})
              AND net_value_date >= %s
              AND net_value_date <= DATE_ADD(%s, INTERVAL %s DAY)
            GROUP BY fund_code
        ) b ON a.fund_code = b.fund_code
           AND (a.net_value_date = b.first_date OR a.net_value_date = b.last_date)
        ORDER BY a.fund_code, a.net_value_date
        """
        params = fund_codes + [start_date, start_date, lookforward_days]
        df = pd.read_sql(query, conn, params=params)
        elapsed = time.time() - t0
        print(f"[DB] 实际表现查询完成: {len(df)} 行, {df['fund_code'].nunique() if not df.empty else 0} 只基金 ({elapsed:.1f}s)", flush=True)
        return df
    finally:
        conn.close()


def get_index_trend(index_df: pd.DataFrame) -> dict:
    """分析指数趋势，返回摘要信息"""
    if index_df.empty:
        return {}

    summary = {}
    for code, group in index_df.groupby('index_code'):
        group = group.sort_values('trade_date')
        prices = group['close_price'].values
        if len(prices) < 2:
            continue

        ret_20d = (prices[-1] - prices[-min(21, len(prices))]) / prices[-min(21, len(prices))] * 100
        ret_60d = (prices[-1] - prices[-min(61, len(prices))]) / prices[-min(61, len(prices))] * 100
        volatility = pd.Series(prices).pct_change().std() * 100 if len(prices) > 5 else 0
        ma20 = prices[-20:].mean() if len(prices) >= 20 else None
        ma60 = prices[-60:].mean() if len(prices) >= 60 else None
        trend = "上升" if prices[-1] > ma60 else "下降" if ma60 else "震荡"

        name = group.iloc[0]['index_name']
        summary[name] = {
            'code': code,
            'latest_close': float(prices[-1]),
            'ret_20d': float(round(ret_20d, 2)),
            'ret_60d': float(round(ret_60d, 2)),
            'volatility': float(round(volatility, 2)),
            'vs_ma20': float(round((prices[-1] - ma20) / ma20 * 100, 2)) if ma20 else None,
            'trend': trend,
        }
    return summary


def get_fund_candidates_optimized(target_date: str, top_n: int = TOP_N_CANDIDATES) -> pd.DataFrame:
    """
    全量候选基金筛选：多维度评分排序，不做截断。

    相比 get_fund_candidates_backtest：
    1. 多维度加权评分（综合 1月/3月/6月/1年 收益，近期权重更高）
    2. 返回全部有效基金（不做 top N 截断）
    """
    import time
    t0 = time.time()
    conn = get_conn()
    try:
        # 1. 获取权益型基金
        print(f"[OptimizedDB] 开始获取权益型基金...", flush=True)
        funds_df = pd.read_sql("""
            SELECT fund_code, fund_name, fund_type_main
            FROM fund_info
            WHERE (establishment_date IS NULL OR establishment_date <= %s)
        """, conn, params=(target_date,))
        if funds_df.empty:
            return pd.DataFrame()
        funds_df['fund_code'] = funds_df['fund_code'].astype(str).str.strip().str.zfill(6)
        total_funds = len(funds_df)
        print(f"[OptimizedDB] 权益型基金: {total_funds} 只 ({time.time()-t0:.1f}s)", flush=True)

        name_map = dict(zip(funds_df['fund_code'], funds_df['fund_name']))
        type_map = dict(zip(funds_df['fund_code'], funds_df['fund_type_main']))
        all_codes = funds_df['fund_code'].tolist()

        # 2. 分批获取最新净值及多区间快照
        BATCH_SIZE = 2000
        snapshot_configs = [(35, '1月前'), (95, '3月前'), (185, '6月前'), (370, '1年前')]

        # 最新净值
        latest_nav_all = []
        n_batches = (len(all_codes) + BATCH_SIZE - 1) // BATCH_SIZE
        for batch_idx, batch_start in enumerate(range(0, len(all_codes), BATCH_SIZE)):
            batch = all_codes[batch_start:batch_start + BATCH_SIZE]
            ph = ','.join(['%s'] * len(batch))
            sql = f"""
            SELECT t.fund_code, t.unit_net_value, t.net_value_date
            FROM fund_values t
            INNER JOIN (
                SELECT fund_code, MAX(net_value_date) AS max_date
                FROM fund_values
                WHERE fund_code IN ({ph}) AND net_value_date <= %s
                GROUP BY fund_code
            ) latest ON t.fund_code = latest.fund_code AND t.net_value_date = latest.max_date
            WHERE t.unit_net_value > 0
            """
            df_part = pd.read_sql(sql, conn, params=(batch + [target_date]))
            latest_nav_all.append(df_part)
            print(f"[OptimizedDB] 最新净值批 {batch_idx+1}/{n_batches}: {len(df_part)} 只 ({time.time()-t0:.1f}s)", flush=True)
        latest_nav = pd.concat(latest_nav_all, ignore_index=True) if latest_nav_all else pd.DataFrame()
        if latest_nav.empty:
            return pd.DataFrame()
        latest_nav['fund_code'] = latest_nav['fund_code'].astype(str).str.strip().str.zfill(6)
        lnav = latest_nav.set_index('fund_code')['unit_net_value'].astype(float)
        print(f"[OptimizedDB] 最新净值获取完成 ({time.time()-t0:.1f}s)", flush=True)

        # 快照净值
        snapshot_navs = {}
        for lookback, label in snapshot_configs:
            cutoff = pd.Timestamp(target_date) - pd.Timedelta(days=lookback)
            rows = []
            for batch_start in range(0, len(all_codes), BATCH_SIZE):
                batch = all_codes[batch_start:batch_start + BATCH_SIZE]
                ph = ','.join(['%s'] * len(batch))
                sql = f"""
                SELECT t.fund_code, t.unit_net_value
                FROM fund_values t
                INNER JOIN (
                    SELECT fund_code, MAX(net_value_date) AS md
                    FROM fund_values
                    WHERE fund_code IN ({ph})
                      AND net_value_date <= %s
                      AND net_value_date >= DATE_SUB(%s, INTERVAL 10 DAY)
                    GROUP BY fund_code
                ) s ON t.fund_code = s.fund_code AND t.net_value_date = s.md
                WHERE t.unit_net_value > 0
                """
                df_part = pd.read_sql(sql, conn, params=(batch + [str(cutoff.date()), str(cutoff.date())]))
                rows.append(df_part)
            snap = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
            if not snap.empty:
                snap['fund_code'] = snap['fund_code'].astype(str).str.strip().str.zfill(6)
                snap['unit_net_value'] = snap['unit_net_value'].astype(float)
            snapshot_navs[lookback] = snap
            print(f"[OptimizedDB] {label}快照: {len(snap)} 只 ({time.time()-t0:.1f}s)", flush=True)

        # 3. 合并计算多维度收益 + 综合评分
        def get_snapshot_nav(lookback):
            snap = snapshot_navs.get(lookback)
            if snap is not None and not snap.empty:
                return snap.set_index('fund_code')['unit_net_value']
            return pd.Series(dtype=float)

        nav_35d = get_snapshot_nav(35)
        nav_95d = get_snapshot_nav(95)
        nav_185d = get_snapshot_nav(185)
        nav_370d = get_snapshot_nav(370)

        results = []
        n_total = len(lnav.index)
        for idx, code in enumerate(lnav.index):
            if idx > 0 and idx % 2000 == 0:
                print(f"[OptimizedDB] 评分计算: {idx}/{n_total} ({time.time()-t0:.1f}s)", flush=True)
            current = lnav[code]
            if not current or current <= 0:
                continue

            def safe_ret(past_series):
                if code in past_series.index:
                    past = past_series[code]
                    if past and past > 0:
                        return (current - past) / past
                return None

            ret_1m = safe_ret(nav_35d)
            ret_3m = safe_ret(nav_95d)
            ret_6m = safe_ret(nav_185d)
            ret_1y = safe_ret(nav_370d)

            if ret_1m is None:
                continue

            # 多维度综合评分（截断异常值）
            score_1m = max(-0.3, min(0.3, ret_1m))
            score_3m = max(-0.3, min(0.3, ret_3m if ret_3m is not None else ret_1m))
            score_6m = max(-0.3, min(0.3, ret_6m if ret_6m is not None else ret_3m or ret_1m))
            score_1y = max(-0.3, min(0.3, ret_1y if ret_1y is not None else ret_6m or ret_3m or ret_1m))
            composite_score = 0.4 * score_1m + 0.3 * score_3m + 0.2 * score_6m + 0.1 * score_1y

            results.append({
                'fund_code': code,
                'fund_name': name_map.get(code, ''),
                'fund_type_main': type_map.get(code, ''),
                'date': target_date,
                'unit_nav': current,
                'month_1': ret_1m,
                'month_3': ret_3m if ret_3m is not None else ret_1m,
                'month_6': ret_6m if ret_6m is not None else ret_3m or ret_1m,
                'year_1': ret_1y if ret_1y is not None else ret_6m or ret_3m or ret_1m,
                'composite_score': composite_score,
            })

        result_df = pd.DataFrame(results)
        if result_df.empty:
            return result_df

        # 全部返回，不做截断，按综合评分排序
        result_df = result_df.sort_values('composite_score', ascending=False).reset_index(drop=True)
        type_counts_str = ', '.join([f"{k}:{v}" for k, v in result_df['fund_type_main'].value_counts().items()])
        print(f"[OptimizedDB] 全量基金: {len(result_df)} 只 (从 {len(results)} 有效, "
              f"耗时 {time.time()-t0:.1f}s, 类型:{type_counts_str})", flush=True)
        return result_df

    finally:
        conn.close()


def compute_beta_vs_index(fund_returns: pd.Series, index_returns: pd.Series, min_periods: int = 20) -> float:
    """计算基金 vs 指数的 Beta 系数"""
    common = fund_returns.dropna().align(index_returns.dropna(), join='inner')
    if len(common[0]) < min_periods:
        return 1.0
    cov = np.cov(common[0].values[-60:], common[1].values[-60:])
    if cov[1][1] > 1e-10:
        return float(cov[0][1] / cov[1][1])
    return 1.0
