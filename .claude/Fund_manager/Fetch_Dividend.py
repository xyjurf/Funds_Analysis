"""
Fetch_Dividend.py
从天天基金 pingzhongdata JS 中提取基金分红记录。
数据源：https://fund.eastmoney.com/pingzhongdata/{code}.js
新表：fund_dividends
"""

import argparse
import requests
import pymysql
import re
import json
import sys
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'weilian.x',
    'port': 3306,
    'database': 'Fund_DATA'
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Referer': 'https://fund.eastmoney.com/'
}

PINGZHONG_URL = 'https://fund.eastmoney.com/pingzhongdata/{}.js'

COMMIT_INTERVAL = 200


def get_db_connection():
    return pymysql.connect(**DB_CONFIG)


def create_dividend_table(cursor):
    sql = """
    CREATE TABLE IF NOT EXISTS fund_dividends (
        id             INT AUTO_INCREMENT PRIMARY KEY,
        fund_code      VARCHAR(20)    NOT NULL COMMENT '基金代码',
        dividend_date  DATE           NOT NULL COMMENT '除权日',
        unit_dividend  DECIMAL(10,6)  DEFAULT NULL COMMENT '每份分红金额（元）',
        nav_on_date    DECIMAL(10,4)  DEFAULT NULL COMMENT '除权日单位净值',
        created_at     TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_fund_date (fund_code, dividend_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    cursor.execute(sql)
    print("[OK] fund_dividends 表创建/确认完成")


def get_all_fund_codes(cursor):
    cursor.execute("SELECT DISTINCT fund_code FROM fund_data ORDER BY fund_code")
    codes = []
    for row in cursor.fetchall():
        code = row[0]
        code_str = str(code).zfill(6) if isinstance(code, int) else str(code)
        codes.append(code_str)
    return codes


def get_processed_fund_codes(cursor):
    cursor.execute("SELECT DISTINCT fund_code FROM fund_dividends")
    return {row[0] for row in cursor.fetchall()}


def parse_dividends_from_js(js_text, fund_code):
    """从 pingzhongdata JS 中提取分红记录"""
    match = re.search(r'var Data_netWorthTrend\s*=\s*(\[.*?\]);', js_text, re.DOTALL)
    if not match:
        return []

    try:
        trend_data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    dividend_pattern = re.compile(r'分红[：:]\s*每份派现金([\d.]+)元')
    dividends = []

    for item in trend_data:
        unit_money = item.get('unitMoney', '')
        if not unit_money or unit_money == '':
            continue

        m = dividend_pattern.search(str(unit_money))
        if m:
            div_amount = float(m.group(1))
            timestamp_ms = item.get('x')
            nav = item.get('y')

            if timestamp_ms:
                div_date = datetime.fromtimestamp(timestamp_ms / 1000).strftime('%Y-%m-%d')
                dividends.append({
                    'fund_code': fund_code,
                    'dividend_date': div_date,
                    'unit_dividend': div_amount,
                    'nav_on_date': nav,
                })

    return dividends


def fetch_and_parse(fund_code):
    """下载 JS 并提取分红"""
    url = PINGZHONG_URL.format(fund_code)
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.encoding = 'utf-8'
        if r.status_code != 200 or len(r.text) < 100:
            return fund_code, None, "请求失败或返回为空"
        dividends = parse_dividends_from_js(r.text, fund_code)
        return fund_code, dividends, None
    except Exception as e:
        return fund_code, None, str(e)


_process_counter = 0


def process_fund(fund_code, delay=0.3):
    """线程工作函数：下载 + 解析 + 速率控制"""
    global _process_counter
    _process_counter += 1

    _, dividends, err = fetch_and_parse(fund_code)

    if delay > 0:
        time.sleep(delay + random.uniform(0, delay * 0.5))

    if err:
        return fund_code, 0, err
    return fund_code, dividends, None


def save_dividends(conn, dividends):
    """保存分红记录（独立连接，即时提交）"""
    if not dividends:
        return 0
    cursor = conn.cursor()
    count = 0
    for d in dividends:
        try:
            sql = """
            INSERT IGNORE INTO fund_dividends
            (fund_code, dividend_date, unit_dividend, nav_on_date)
            VALUES (%s, %s, %s, %s)
            """
            cursor.execute(sql, (
                d['fund_code'], d['dividend_date'],
                d['unit_dividend'], d['nav_on_date']
            ))
            if cursor.rowcount > 0:
                count += 1
        except Exception:
            continue
    conn.commit()
    cursor.close()
    return count


def verify_data(cursor):
    cursor.execute("SELECT COUNT(*) FROM fund_dividends")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT fund_code) FROM fund_dividends")
    funds = cursor.fetchone()[0]
    cursor.execute("SELECT MIN(dividend_date), MAX(dividend_date) FROM fund_dividends")
    daterange = cursor.fetchone()
    cursor.execute("""
        SELECT fund_code, COUNT(*) as cnt
        FROM fund_dividends GROUP BY fund_code
        ORDER BY cnt DESC LIMIT 10
    """)
    top = cursor.fetchall()

    print(f"\n{'=' * 55}")
    print(f"  fund_dividends 表统计")
    print(f"{'=' * 55}")
    print(f"  总分红记录:     {total}")
    print(f"  有分红的基金:   {funds}")
    print(f"  日期范围:       {daterange[0]} ~ {daterange[1]}")
    if top:
        print(f"  分红次数最多的基金:")
        for r in top:
            print(f"    {r[0]}: {r[1]} 次")


def parse_args():
    parser = argparse.ArgumentParser(description='提取基金分红数据')
    parser.add_argument('--limit', type=int, default=0,
                        help='最多处理 N 只基金（默认全部）')
    parser.add_argument('--workers', type=int, default=10,
                        help='并发线程数（默认 10）')
    parser.add_argument('--delay', type=float, default=0.3,
                        help='每线程请求间隔秒数（默认 0.3）')
    return parser.parse_args()


def main():
    args = parse_args()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        create_dividend_table(cursor)

        all_codes = get_all_fund_codes(cursor)
        processed = get_processed_fund_codes(cursor)
        print(f"[Info] fund_data 基金总数: {len(all_codes)}")
        print(f"[Info] 已有分红数据的基金: {len(processed)}")

        pending = [c for c in all_codes if c not in processed]
        if args.limit > 0:
            pending = pending[:args.limit]

        print(f"[Info] 本次需处理: {len(pending)} 只基金")

        if not pending:
            print("所有基金分红数据已完整，无需处理")
            verify_data(cursor)
            return

        total_funds = len(pending)
        success = error = no_dividend = dividend_records = 0
        start_time = time.time()

        print(f"[系统] 启动 {args.workers} 线程处理 {total_funds} 只基金...\n")

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {}
            for code in pending:
                futures[executor.submit(process_fund, code, args.delay)] = code

            done_count = 0
            for future in as_completed(futures):
                done_count += 1
                code, dividends, err = future.result()

                if err:
                    error += 1
                    if error <= 5:
                        print(f"  [ERR] {code}: {err}", flush=True)
                    continue

                if not dividends:
                    no_dividend += 1
                    continue

                # 每个线程独立连接写入，即时提交
                thread_conn = get_db_connection()
                try:
                    saved = save_dividends(thread_conn, dividends)
                    if saved > 0:
                        dividend_records += saved
                        success += 1
                        print(f"  [OK] {code}: {len(dividends)} 笔分红", flush=True)
                    else:
                        no_dividend += 1
                except Exception as e:
                    error += 1
                    print(f"  [ERR] {code}: 写入失败 - {e}", flush=True)
                finally:
                    thread_conn.close()

                # 进度报告
                if done_count % COMMIT_INTERVAL == 0:
                    elapsed = time.time() - start_time
                    rate = done_count / elapsed if elapsed > 0 else 0
                    remaining = total_funds - done_count
                    eta = remaining / rate if rate > 0 else 0
                    print(f"  [进度] {done_count}/{total_funds} | "
                          f"有分红 {success} | 无分红 {no_dividend} | 错误 {error} | "
                          f"{rate:.1f}条/秒 | 剩余 ~{eta/60:.1f}分钟", flush=True)

        elapsed = time.time() - start_time
        print(f"\n{'=' * 55}")
        print(f"  分红数据采集完成!")
        print(f"  耗时: {elapsed/60:.1f} 分钟")
        print(f"  处理基金: {total_funds}")
        print(f"  有分红:   {success}")
        print(f"  无分红:   {no_dividend}")
        print(f"  错误:     {error}")
        print(f"  新增记录: {dividend_records}")
        print(f"{'=' * 55}")

        verify_data(cursor)

    except KeyboardInterrupt:
        print("\n[中断] 用户中断，已保存的数据不会丢失")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        cursor.close()
        conn.close()
        print("数据库连接已关闭")


if __name__ == "__main__":
    main()
