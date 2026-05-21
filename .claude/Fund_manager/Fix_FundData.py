"""
Fix_FundData.py
补充基金分析所需的核心缺失数据：
  1. 基金分类（类型、主类别）— 从 fundcode_search.js 批量获取
  2. 基金公司、基金经理、成立日期、规模 — 从天天基金 F10 页面抓取

新表：fund_info（基金信息补充表）
"""

import argparse
import requests
import pymysql
import re
import json
import sys
import time
import random
from bs4 import BeautifulSoup
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
    'Referer': 'https://fundf10.eastmoney.com/'
}

# 主类别映射
TYPE_MAIN_MAP = {
    '股票型': '股票型',
    '指数型-股票': '指数型',
    '指数型-固收': '指数型',
    '指数型-海外股票': '指数型',
    '指数型-其他': '指数型',
    '混合型-偏股': '混合型',
    '混合型-灵活': '混合型',
    '混合型-平衡': '混合型',
    '混合型-偏债': '混合型',
    '混合型-绝对收益': '混合型',
    '债券型-长债': '债券型',
    '债券型-中短债': '债券型',
    '债券型-混合一级': '债券型',
    '债券型-混合二级': '债券型',
    '货币型-普通货币': '货币型',
    '货币型-浮动净值': '货币型',
    'QDII-普通股票': 'QDII',
    'QDII-混合偏股': 'QDII',
    'QDII-混合平衡': 'QDII',
    'QDII-混合灵活': 'QDII',
    'QDII-纯债': 'QDII',
    'QDII-混合债': 'QDII',
    'QDII-商品': 'QDII',
    'QDII-REITs': 'QDII',
    'QDII-FOF': 'QDII',
    'FOF-均衡型': 'FOF',
    'FOF-稳健型': 'FOF',
    'FOF-进取型': 'FOF',
    '商品': '商品',
    'Reits': 'Reits'
}

# ==================== 数据库操作 ====================

def get_db_connection():
    return pymysql.connect(**DB_CONFIG)


def create_fund_info_table(cursor):
    sql = """
    CREATE TABLE IF NOT EXISTS fund_info (
        fund_code          VARCHAR(20)    NOT NULL PRIMARY KEY,
        fund_name          VARCHAR(100)   DEFAULT NULL,
        fund_type          VARCHAR(50)    DEFAULT NULL COMMENT '详细分类，如 混合型-灵活',
        fund_type_main     VARCHAR(20)    DEFAULT NULL COMMENT '主类别，如 混合型',
        fund_company       VARCHAR(100)   DEFAULT NULL COMMENT '基金管理人',
        fund_manager       VARCHAR(200)   DEFAULT NULL COMMENT '基金经理',
        establishment_date DATE           DEFAULT NULL COMMENT '成立日期',
        fund_size          DECIMAL(14,2)  DEFAULT NULL COMMENT '净资产规模（亿元）',
        fund_size_date     DATE           DEFAULT NULL COMMENT '规模截止日期',
        purchase_rate      DECIMAL(6,4)   DEFAULT NULL COMMENT '原始申购费率',
        discount_rate      DECIMAL(6,4)   DEFAULT NULL COMMENT '优惠申购费率',
        min_purchase_amount DECIMAL(12,2) DEFAULT NULL COMMENT '最低申购金额（元）',
        created_at         TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
        updated_at         TIMESTAMP      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    cursor.execute(sql)
    print("[OK] fund_info 表创建/确认完成")


# ==================== Phase 1: 从 fundcode_search.js 批量获取基金分类 ====================

def fetch_fund_types_from_js():
    """
    从 fundcode_search.js 获取全市场基金分类数据。
    返回 dict: {fund_code_6digits: {name, type, type_main}}
    """
    url = 'https://fund.eastmoney.com/js/fundcode_search.js'
    print(f"[Phase 1] 正在下载基金分类数据: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.encoding = 'utf-8'
    except Exception as e:
        print(f"[ERROR] 下载 fundcode_search.js 失败: {e}")
        return {}

    match = re.search(r'var r = (\[.*?\]);', r.text, re.DOTALL)
    if not match:
        print("[ERROR] 无法解析 fundcode_search.js 数据")
        return {}

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 解析失败: {e}")
        return {}

    result = {}
    for item in data:
        code = item[0]   # 6位字符串基金代码
        name = item[2]   # 基金名称
        ftype = item[3]  # 基金类型
        type_main = TYPE_MAIN_MAP.get(ftype, '其他')
        result[code] = {'name': name, 'type': ftype, 'type_main': type_main}

    print(f"[Phase 1] 从 fundcode_search.js 获取到 {len(result)} 只基金分类数据")
    return result


# ==================== Phase 2: 从 F10 页面获取详细基金信息 ====================

def parse_f10_page(fund_code):
    """
    抓取并解析某只基金的 F10 类型页面，返回详细信息 dict。
    失败返回 None。
    """
    url = f'https://fundf10.eastmoney.com/jjfl_{fund_code}.html'
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')
        text = soup.get_text(separator='\n')
    except Exception as e:
        return None

    info = {'fund_code': fund_code}

    # 基金类型（页面中类型更完整，覆盖 fundcode_search.js）
    m = re.search(r'类型[：:]\s*([^\n]+)', text)
    if m:
        ftype = m.group(1).strip()
        info['fund_type'] = ftype
        info['fund_type_main'] = TYPE_MAIN_MAP.get(ftype, '其他')

    # 基金经理
    m = re.search(r'基金经理[：:]\s*([^\n]+)', text)
    if m:
        info['fund_manager'] = m.group(1).strip().rstrip(',')

    # 基金公司
    m = re.search(r'管理人[：:]\s*([^\n]+)', text)
    if m:
        info['fund_company'] = m.group(1).strip()

    # 成立日期
    m = re.search(r'成立日期[：:]\s*([^\n]+)', text)
    if m:
        date_str = m.group(1).strip()
        try:
            info['establishment_date'] = str(datetime.strptime(date_str, '%Y-%m-%d').date())
        except ValueError:
            pass

    # 净资产规模（如 "26.44亿元（截止至：2026-03-31）"）
    m = re.search(r'净资产规模[：:]\s*([\d.]+)\s*亿元', text)
    if m:
        info['fund_size'] = float(m.group(1))
        # 提取截止日期
        date_m = re.search(r'截止至[：:]\s*([\d-]+)', text)
        if date_m:
            info['fund_size_date'] = date_m.group(1).strip()

    # 申购费率（从费率表上部取原费率/优惠费率）
    m = re.search(r'购买手续费[：:]\s*([\d.]+)%', text)
    if m:
        info['purchase_rate'] = float(m.group(1)) / 100
    m = re.search(r'购买手续费[：:][\d.]+%\s*\|?\s*([\d.]+)%', text)
    if m:
        info['discount_rate'] = float(m.group(1)) / 100

    # 最低申购金额
    m = re.search(r'申购起点[：:]\s*([\d.]+)\s*元', text)
    if m:
        info['min_purchase_amount'] = float(m.group(1))

    return info


_fetch_counter = 0

def fetch_fund_detail(fund_code, delay=0.3):
    """供多线程调用的包装函数，自带速率控制"""
    global _fetch_counter
    _fetch_counter += 1
    try:
        info = parse_f10_page(fund_code)
        # 请求间隔，避免触发风控
        if delay > 0:
            time.sleep(delay + random.uniform(0, delay * 0.5))
        if info:
            return fund_code, info, None
        return fund_code, None, "解析失败"
    except Exception as e:
        return fund_code, None, str(e)


# ==================== 主流程 ====================

def parse_args():
    parser = argparse.ArgumentParser(description='补充基金分类和详情数据')
    parser.add_argument('--phase1-only', action='store_true',
                        help='仅执行 Phase 1（基金分类，单请求批量获取）')
    parser.add_argument('--phase2-only', action='store_true',
                        help='仅执行 Phase 2（基金详情，多线程抓取）')
    parser.add_argument('--workers', type=int, default=8,
                        help='Phase 2 线程数（默认 8）')
    parser.add_argument('--limit', type=int, default=0,
                        help='Phase 2 最多处理 N 只基金（0=不限制，用于调试）')
    parser.add_argument('--delay', type=float, default=0.3,
                        help='Phase 2 请求间隔秒数（默认 0.3，防封）')
    return parser.parse_args()


def main():
    args = parse_args()
    run_phase1 = not args.phase2_only
    run_phase2 = not args.phase1_only

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. 创建 fund_info 表
        create_fund_info_table(cursor)

        # 2. 获取 fund_data 中已有的基金代码（仅处理这些基金）
        cursor.execute("SELECT DISTINCT fund_code FROM fund_data ORDER BY fund_code")
        fund_data_codes = []
        for row in cursor.fetchall():
            code_int = row[0]
            code_str = str(code_int).zfill(6) if isinstance(code_int, int) else str(code_int)
            fund_data_codes.append(code_str)
        print(f"[Info] fund_data 表共有 {len(fund_data_codes)} 只基金")

        # 3. 获取 fund_info 表已有数据的基金
        cursor.execute("SELECT fund_code FROM fund_info WHERE fund_type IS NOT NULL")
        existing = {row[0] for row in cursor.fetchall()}
        print(f"[Info] fund_info 已有分类数据的基金: {len(existing)} 只")

        # ---------- Phase 1: 批量获取基金分类 ----------
        if not run_phase1:
            print("[Phase 1] 已跳过")
            all_fund_types = {}  # Phase 2 不需要分类数据也能运行
        else:
            all_fund_types = fetch_fund_types_from_js()
            if not all_fund_types:
                print("[ERROR] 无法获取基金分类数据，终止")
                return

        if run_phase1:
            # 仅保留 fund_data 中存在的基金
            fund_types_filtered = {}
            for code in fund_data_codes:
                if code in all_fund_types:
                    fund_types_filtered[code] = all_fund_types[code]
            print(f"[Phase 1] 匹配到 {len(fund_types_filtered)} 只基金分类数据")

            # 批量写入 fund_type（仅补缺）
            type_insert_count = 0
            for code, info in fund_types_filtered.items():
                if code in existing:
                    continue
                try:
                    sql = """
                    INSERT INTO fund_info (fund_code, fund_name, fund_type, fund_type_main)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE fund_name=VALUES(fund_name),
                                            fund_type=VALUES(fund_type),
                                            fund_type_main=VALUES(fund_type_main)
                    """
                    cursor.execute(sql, (code, info['name'], info['type'], info['type_main']))
                    type_insert_count += 1
                except Exception as e:
                    print(f"  [ERROR] 写入 {code} 分类数据失败: {e}")

            conn.commit()
            print(f"[Phase 1] 新增/更新 {type_insert_count} 条分类数据")
        else:
            print("[Phase 1] 已跳过 Phase 1 写入")

        # ---------- Phase 2: 从 F10 页面补充详情（公司、经理、规模）----------
        if not run_phase2:
            print("[Phase 2] 已跳过")
        else:
            # 找出缺少详情的基金
            cursor.execute("""
                SELECT fund_code FROM fund_info
                WHERE fund_company IS NULL OR fund_manager IS NULL
            """)
            missing_detail = [row[0] for row in cursor.fetchall()]
            print(f"\n[Phase 2] 缺少详情数据的基金: {len(missing_detail)} 只")

            if not missing_detail:
                print("[Phase 2] 所有基金详情已完整，跳过")
            else:
                # 以 fund_data 为基准，只处理 fund_data 中存在的基金
                missing_detail = [c for c in missing_detail if c in fund_data_codes]
                # 应用 limit
                if args.limit > 0:
                    missing_detail = missing_detail[:args.limit]
                print(f"[Phase 2] 需抓取: {len(missing_detail)} 只基金详情（线程={args.workers}, 延迟={args.delay}s）")

                FETCH_WORKERS = args.workers
                total_tasks = len(missing_detail)
                success = 0
                error = 0
                COMMIT_INTERVAL = 100
                phase2_start = time.time()

                print(f"[Phase 2] 启动 {FETCH_WORKERS} 线程抓取 {total_tasks} 只基金详情...\n", flush=True)

                with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
                    futures = {}
                    for code in missing_detail:
                        futures[executor.submit(fetch_fund_detail, code, args.delay)] = code

                    done_count = 0
                    for future in as_completed(futures):
                        code, info, err = future.result()
                        done_count += 1
                        if err or not info:
                            error += 1
                            if error <= 5:
                                print(f"  [ERR] {code}: {err}", flush=True)
                            continue

                        try:
                            sql = """
                            UPDATE fund_info SET
                                fund_type = COALESCE(%s, fund_type),
                                fund_type_main = COALESCE(%s, fund_type_main),
                                fund_company = %s,
                                fund_manager = %s,
                                establishment_date = %s,
                                fund_size = %s,
                                fund_size_date = %s,
                                purchase_rate = %s,
                                discount_rate = %s,
                                min_purchase_amount = %s
                            WHERE fund_code = %s
                            """
                            cursor.execute(sql, (
                                info.get('fund_type'),
                                info.get('fund_type_main'),
                                info.get('fund_company'),
                                info.get('fund_manager'),
                                info.get('establishment_date'),
                                info.get('fund_size'),
                                info.get('fund_size_date'),
                                info.get('purchase_rate'),
                                info.get('discount_rate'),
                                info.get('min_purchase_amount'),
                                code
                            ))
                            success += 1

                            if success % COMMIT_INTERVAL == 0:
                                conn.commit()
                                elapsed = time.time() - phase2_start
                                rate = done_count / elapsed if elapsed > 0 else 0
                                eta = (total_tasks - done_count) / rate if rate > 0 else 0
                                print(f"  [进度] {done_count}/{total_tasks} | 成功 {success} | 失败 {error} | "
                                      f"{rate:.1f}条/秒 | 预计剩余 {eta/60:.1f}分钟", flush=True)

                            print(f"  [OK] {code}: {info.get('fund_company','')} | "
                                  f"{info.get('fund_manager','')} | {info.get('fund_size','')}亿", flush=True)

                        except Exception as e:
                            error += 1
                            print(f"  [ERR] {code}: 写入失败 - {e}", flush=True)

                conn.commit()
                elapsed = time.time() - phase2_start
                print(f"\n[Phase 2] 完成! 耗时 {elapsed/60:.1f}分钟 | "
                      f"成功 {success} 只 / 失败 {error} 只", flush=True)

        # ---------- 最终统计 ----------
        print(f"\n{'=' * 55}")
        print(f"  fund_info 表数据统计")
        print(f"{'=' * 55}")

        cursor.execute("SELECT COUNT(*) FROM fund_info")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM fund_info WHERE fund_type IS NOT NULL")
        with_type = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM fund_info WHERE fund_company IS NOT NULL")
        with_company = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM fund_info WHERE fund_manager IS NOT NULL")
        with_manager = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM fund_info WHERE fund_size IS NOT NULL")
        with_size = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM fund_info WHERE establishment_date IS NOT NULL")
        with_date = cursor.fetchone()[0]

        print(f"  总记录:           {total}")
        print(f"  有分类数据:       {with_type}")
        print(f"  有基金公司:       {with_company}")
        print(f"  有基金经理:       {with_manager}")
        print(f"  有规模数据:       {with_size}")
        print(f"  有成立日期:       {with_date}")

        cursor.execute("""
            SELECT fund_type_main, COUNT(*) as cnt
            FROM fund_info WHERE fund_type_main IS NOT NULL
            GROUP BY fund_type_main ORDER BY cnt DESC
        """)
        print(f"\n  基金主类别分布:")
        for row in cursor.fetchall():
            print(f"    {row[0]}: {row[1]}")

        print(f"\n{'=' * 55}")
        print("  补充完成！")
        print(f"{'=' * 55}")

    except KeyboardInterrupt:
        print("\n[中断] 用户中断，已保存的数据不会丢失")
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
