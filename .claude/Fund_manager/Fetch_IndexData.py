"""
Fetch_IndexData.py
获取基准指数日线行情（OHLCV）
使用 akshare 库（内部封装东方财富/新浪 API 反爬）
新表：index_data
"""

import argparse
import pymysql
import sys
import time

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'weilian.x',
    'port': 3306,
    'database': 'Fund_DATA'
}

# akshare 使用的指数代码符号（新浪格式）
INDEX_LIST = [
    {'symbol': 'sh000300', 'code': '000300', 'name': '沪深300'},
    {'symbol': 'sh000905', 'code': '000905', 'name': '中证500'},
    {'symbol': 'sh000852', 'code': '000852', 'name': '中证1000'},
    {'symbol': 'sz399006', 'code': '399006', 'name': '创业板指'},
    {'symbol': 'sh000001', 'code': '000001', 'name': '上证指数'},
    {'symbol': 'sz399001', 'code': '399001', 'name': '深证成指'},
]


def get_db_connection():
    return pymysql.connect(**DB_CONFIG)


def create_index_data_table(cursor):
    sql = """
    CREATE TABLE IF NOT EXISTS index_data (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        index_code  VARCHAR(20)    NOT NULL COMMENT '指数代码',
        index_name  VARCHAR(50)    DEFAULT NULL COMMENT '指数名称',
        trade_date  DATE           NOT NULL COMMENT '交易日',
        open_price  DECIMAL(12,4)  DEFAULT NULL COMMENT '开盘价',
        close_price DECIMAL(12,4)  DEFAULT NULL COMMENT '收盘价',
        high_price  DECIMAL(12,4)  DEFAULT NULL COMMENT '最高价',
        low_price   DECIMAL(12,4)  DEFAULT NULL COMMENT '最低价',
        volume      BIGINT         DEFAULT NULL COMMENT '成交量（手）',
        created_at  TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_index_date (index_code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    cursor.execute(sql)
    print("[OK] index_data 表创建/确认完成")


def get_existing_dates(cursor, index_code):
    cursor.execute("SELECT trade_date FROM index_data WHERE index_code = %s", (index_code,))
    return {row[0].strftime('%Y-%m-%d') if hasattr(row[0], 'strftime') else str(row[0]) for row in cursor.fetchall()}


def fetch_index_data(symbol):
    """使用 akshare 获取指数日线数据"""
    import akshare as ak
    try:
        df = ak.stock_zh_index_daily(symbol=symbol)
        if df is None or df.empty:
            print(f"  [WARN] 未获取到数据")
            return []
        result = []
        for _, row in df.iterrows():
            result.append({
                'trade_date': str(row['date']),
                'open_price': float(row['open']) if pd.notna(row['open']) else None,
                'close_price': float(row['close']) if pd.notna(row['close']) else None,
                'high_price': float(row['high']) if pd.notna(row['high']) else None,
                'low_price': float(row['low']) if pd.notna(row['low']) else None,
                'volume': int(row['volume']) if pd.notna(row['volume']) else 0,
            })
        return result
    except Exception as e:
        print(f"  [ERROR] 获取指数数据失败: {e}")
        return []


def save_index_data(cursor, conn, index_code, index_name, records):
    if not records:
        return 0
    count = 0
    for rec in records:
        try:
            sql = """
            INSERT IGNORE INTO index_data
            (index_code, index_name, trade_date, open_price, close_price, high_price, low_price, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql, (
                index_code, index_name,
                rec['trade_date'], rec['open_price'], rec['close_price'],
                rec['high_price'], rec['low_price'], rec['volume']
            ))
            if cursor.rowcount > 0:
                count += 1
        except Exception as e:
            print(f"  [ERROR] 写入数据失败: {e}")
    conn.commit()
    return count


def verify_data(cursor):
    cursor.execute("SELECT COUNT(*) FROM index_data")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT index_code) FROM index_data")
    idx_count = cursor.fetchone()[0]
    cursor.execute("""
        SELECT index_code, ANY_VALUE(index_name) as index_name, COUNT(*) as cnt,
               MIN(trade_date) as first_date, MAX(trade_date) as last_date
        FROM index_data
        GROUP BY index_code ORDER BY index_code
    """)
    print(f"\n{'=' * 60}")
    print(f"  index_data 表统计 (共 {total} 条, {idx_count} 个指数)")
    print(f"{'=' * 60}")
    for row in cursor.fetchall():
        print(f"  {row[0]} {row[1]}: {row[2]} 条 ({row[3]} ~ {row[4]})")


def parse_args():
    parser = argparse.ArgumentParser(description='获取基准指数日线行情数据')
    parser.add_argument('--limit', type=int, default=0,
                        help='最多处理 N 个指数（默认全部）')
    parser.add_argument('--index', type=str, default=None,
                        help='指定单个指数代码，如 000300')
    return parser.parse_args()


def main():
    args = parse_args()

    # 延迟导入 akshare，确保依赖已安装
    global pd
    import pandas as pd

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        create_index_data_table(cursor)

        indices = INDEX_LIST
        if args.index:
            indices = [i for i in indices if i['code'] == args.index]
            if not indices:
                print(f"[ERROR] 未找到指数代码: {args.index}")
                return
        if args.limit > 0:
            indices = indices[:args.limit]

        print(f"[Info] 待处理指数: {[i['name'] for i in indices]}")

        total_new = 0
        for idx_info in indices:
            print(f"\n[处理] {idx_info['name']} ({idx_info['symbol']})...")

            existing_dates = get_existing_dates(cursor, idx_info['code'])
            print(f"  已有 {len(existing_dates)} 条")

            records = fetch_index_data(idx_info['symbol'])
            if not records:
                continue
            print(f"  API 返回 {len(records)} 条")

            new_records = [r for r in records if r['trade_date'] not in existing_dates]
            if not new_records:
                print(f"  无需更新")
                continue

            saved = save_index_data(cursor, conn, idx_info['code'], idx_info['name'], new_records)
            total_new += saved
            print(f"  新增 {saved} 条（跳过 {len(records) - len(new_records)} 条已有）")
            print(f"  日期范围: {new_records[0]['trade_date']} ~ {new_records[-1]['trade_date']}")

            time.sleep(0.5)

        print(f"\n[完成] 共新增 {total_new} 条指数行情数据")
        verify_data(cursor)

    except KeyboardInterrupt:
        print("\n[中断] 用户中断")
        conn.rollback()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        print("数据库连接已关闭")


if __name__ == "__main__":
    main()
