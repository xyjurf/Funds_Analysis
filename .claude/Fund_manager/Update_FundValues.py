# -*- coding: utf-8 -*-
"""
增量更新 fund_values 表（高速版）
- 预检探测 API 最新日期 → 设为 target_date
- 已有 target_date 的基金跳过 API 探测，直接标记完成（省去 99% 的 API 请求）
- 仅有缺漏的基金才逐个探测 + 拉取
- 支持断点重续
"""

import requests
import pymysql
from bs4 import BeautifulSoup
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# ========== 配置 ==========
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'weilian.x',
    'port': 3306,
    'database': 'Fund_DATA',
    'autocommit': False,
}
PROGRESS_TABLE = "update_fundvalues_progress"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Referer': 'https://fundf10.eastmoney.com/',
}
BASE_URL = 'https://fund.eastmoney.com/f10/F10DataApi.aspx'
PROBE_SAMPLE = 10   # 预检抽样数
FETCH_WORKERS = 15   # 拉取缺漏数据的并发线程数


# ========== 进度表 ==========
def ensure_progress_table(cursor):
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {PROGRESS_TABLE} (
            id INT AUTO_INCREMENT PRIMARY KEY,
            fund_code VARCHAR(20) NOT NULL,
            status VARCHAR(20) DEFAULT 'completed',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_fund_code (fund_code)
        )
    """)


def load_completed(cursor):
    cursor.execute(f"SELECT fund_code FROM {PROGRESS_TABLE} WHERE status = 'completed'")
    return {row[0] for row in cursor.fetchall()}


def mark_completed(cursor, conn, fund_code):
    cursor.execute(
        f"INSERT INTO {PROGRESS_TABLE} (fund_code, status) VALUES (%s, 'completed') "
        f"ON DUPLICATE KEY UPDATE status = 'completed'",
        (fund_code,)
    )
    conn.commit()


def clean_progress(cursor, conn):
    cursor.execute(f"DROP TABLE IF EXISTS {PROGRESS_TABLE}")
    conn.commit()


# ========== API 操作 ==========
def probe_api_latest_date(fund_code):
    """快速探测 API 最新净值日期"""
    try:
        resp = requests.get(
            f'{BASE_URL}?type=lsjz&code={fund_code}&page=1&per=3',
            headers=HEADERS, timeout=30
        )
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table')
        if not table:
            return None
        rows = table.find_all('tr')
        if len(rows) <= 1:
            return None
        cells = rows[1].find_all('td')
        if len(cells) < 6:
            return None
        return datetime.strptime(cells[0].get_text(strip=True), '%Y-%m-%d').date()
    except Exception:
        return None


def fetch_new_data(fund_code, latest_date):
    """拉取比 latest_date 更新的所有数据"""
    new_data = []
    page = 1
    while True:
        try:
            resp = requests.get(
                f'{BASE_URL}?type=lsjz&code={fund_code}&page={page}&per=20',
                headers=HEADERS, timeout=30
            )
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
            table = soup.find('table')
            if not table:
                break
            rows = table.find_all('tr')
            if len(rows) <= 1:
                break
            has_new = False
            for row in rows[1:]:
                cells = row.find_all('td')
                if len(cells) < 6:
                    continue
                date_str = cells[0].get_text(strip=True)
                row_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                if latest_date and row_date <= latest_date:
                    return new_data
                new_data.append({
                    'fund_code': fund_code,
                    'net_value_date': date_str,
                    'unit_net_value': cells[1].get_text(strip=True) or None,
                    'cumulative_net_value': cells[2].get_text(strip=True) or None,
                    'daily_growth_rate': cells[3].get_text(strip=True) or None,
                    'purchase_status': cells[4].get_text(strip=True) or None,
                    'redemption_status': cells[5].get_text(strip=True) or None,
                })
                has_new = True
            if not has_new or len(rows) - 1 < 20:
                break
            page += 1
        except Exception as e:
            print(f"          [请求失败] page={page}: {e}")
            break
    return new_data


# ========== 数据写入 ==========
INSERT_SQL = """
    INSERT IGNORE INTO fund_values
    (fund_code, net_value_date, unit_net_value, cumulative_net_value,
     daily_growth_rate, purchase_status, redemption_status)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
"""


def save_fund_values_to_db(cursor, conn, fund_values):
    if not fund_values:
        return 0
    insert_count = 0
    error_count = 0
    for v in fund_values:
        try:
            cursor.execute(INSERT_SQL, (
                v['fund_code'], v['net_value_date'],
                v['unit_net_value'], v['cumulative_net_value'],
                v['daily_growth_rate'], v['purchase_status'],
                v['redemption_status'],
            ))
            insert_count += 1
        except Exception as e:
            print(f"          [写入失败] {v.get('net_value_date', '?')}: {e}")
            error_count += 1
            continue
    conn.commit()
    if error_count:
        print(f"          [注意] {error_count} 行写入失败，{insert_count} 行成功")
    return insert_count


# ========== 主流程 ==========
def main():
    print("=" * 60)
    print("  基金净值增量更新（高速版）")
    print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        # ---- 1. 进度表 ----
        ensure_progress_table(cursor)
        completed_set = load_completed(cursor)
        if completed_set:
            print(f"[系统] 断点续传: 已跳过 {len(completed_set)} 只已完成基金")

        # ---- 2. 扫描基金最新日期 ----
        print("[系统] 扫描 fund_values 表...")
        cursor.execute("""
            SELECT fund_code, MAX(net_value_date) AS latest_date
            FROM fund_values
            GROUP BY fund_code
            ORDER BY fund_code ASC
        """)
        fund_rows = cursor.fetchall()
        total = len(fund_rows)
        print(f"[系统] 共 {total} 只基金")

        # ---- 3. 预检：确定 target_date ----
        print(f"\n[预检] 探测 API 最新可用日期...")
        target_date = None
        for fund_code, _ in fund_rows[:PROBE_SAMPLE]:
            api_newest = probe_api_latest_date(fund_code)
            if api_newest is not None:
                target_date = api_newest
                break
            time.sleep(0.15)

        if target_date is None:
            print("[预检] 无法获取 API 数据，退出")
            return

        print(f"[预检] API 最新日期: {target_date}")
        print(f"[预检] 已有该日期的基金直接跳过，无需 API 请求")

        # ---- 4. 逐个检查（跳过已是最新的，收集有缺漏的） ----
        stats = {'skipped': 0, 'updated': 0, 'error': 0, 'new_records': 0}
        gap_funds = []

        for idx, (fund_code, latest_date) in enumerate(fund_rows, 1):
            if fund_code in completed_set:
                stats['skipped'] += 1
                continue

            # 已有 target_date → 跳过（无需 API 请求！）
            if latest_date and latest_date >= target_date:
                stats['skipped'] += 1
                mark_completed(cursor, conn, fund_code)
                if idx <= 3 or idx % 2000 == 0 or idx == total:
                    print(f"  [{idx}/{total}] {fund_code} → ✓ 已是最新 ({latest_date})")
                continue

            # 有缺漏 → 收集
            gap_funds.append((idx, fund_code, latest_date))

        # ---- 5. 批量处理缺漏基金（并发拉取） ----
        if gap_funds:
            print(f"\n[系统] {len(gap_funds)} 只基金有数据缺漏，并发拉取中（{FETCH_WORKERS} 线程）...")

            def process_gap(gap_idx, fund_code, latest_date):
                api_newest = probe_api_latest_date(fund_code)
                if api_newest is None:
                    return (gap_idx, fund_code, 'error', None, 0)
                if latest_date and api_newest <= latest_date:
                    return (gap_idx, fund_code, 'latest', None, 0)
                new_data = fetch_new_data(fund_code, latest_date)
                return (gap_idx, fund_code, 'data', new_data, len(new_data) if new_data else 0)

            with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
                futures = {
                    executor.submit(process_gap, idx, code, latest): code
                    for idx, code, latest in gap_funds
                }

                for future in as_completed(futures):
                    gap_idx, fund_code, status, new_data, count = future.result()

                    if status == 'error':
                        print(f"  [{gap_idx}/{total}] {fund_code} → ✗ 探测失败", flush=True)
                        stats['error'] += 1
                        mark_completed(cursor, conn, fund_code)
                        continue

                    if status == 'latest' or not new_data:
                        print(f"  [{gap_idx}/{total}] {fund_code} → ✓ 已是最新", flush=True)
                        stats['skipped'] += 1
                        mark_completed(cursor, conn, fund_code)
                        continue

                    saved = save_fund_values_to_db(cursor, conn, new_data)
                    date_range = f"{new_data[-1]['net_value_date']} ~ {new_data[0]['net_value_date']}"
                    stats['updated'] += 1
                    stats['new_records'] += saved
                    print(f"  [{gap_idx}/{total}] {fund_code} → ★ +{saved}条 ({date_range})", flush=True)
                    mark_completed(cursor, conn, fund_code)
        else:
            print(f"\n[系统] 所有基金均已是最新，无需拉取")

        # ---- 6. 汇总 ----
        print(f"\n{'=' * 60}")
        print(f"  检查完成!")
        print(f"  {'检查基金':<10} {total:>8}")
        print(f"  {'已是最新':<10} {stats['skipped']:>8}")
        print(f"  {'已更新':<10} {stats['updated']:>8}")
        print(f"  {'错误':<10} {stats['error']:>8}")
        print(f"  {'新增记录':<10} {stats['new_records']:>8}")
        print(f"{'=' * 60}")

        clean_progress(cursor, conn)
        print("[系统] 进度表已清除")

    except KeyboardInterrupt:
        print(f"\n\n[系统] 用户中断! 进度已保存，下次运行从中断处继续")
    except Exception as e:
        print(f"\n[系统] 错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cursor.close()
        conn.close()
        print("[系统] 数据库连接已关闭")


if __name__ == "__main__":
    main()
