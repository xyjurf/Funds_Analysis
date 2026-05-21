import requests
import pymysql
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# 数据库连接配置
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'weilian.x',
    'port': 3306,
    'database': 'Fund_DATA'
}

# 创建 fund_values 表
def create_fund_values_table(cursor):
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS fund_values (
        id INT AUTO_INCREMENT PRIMARY KEY,
        fund_code VARCHAR(20) NOT NULL,
        net_value_date DATE NOT NULL,
        unit_net_value DECIMAL(10,4),
        cumulative_net_value DECIMAL(10,4),
        daily_growth_rate VARCHAR(20),
        purchase_status VARCHAR(20),
        redemption_status VARCHAR(20),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_fund_date (fund_code, net_value_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    cursor.execute(create_table_sql)
    print("fund_values 表创建成功")

# 从 fund_data 表获取所有基金代码（并处理int类型转换为6位字符串）
def get_all_fund_codes(cursor):
    cursor.execute("SELECT DISTINCT fund_code FROM fund_data ORDER BY fund_code")
    fund_codes = []
    for row in cursor.fetchall():
        code = row[0]
        # 将int类型转换为字符串，并补前导0到6位
        if isinstance(code, int):
            code_str = str(code).zfill(6)
        else:
            code_str = str(code)
        fund_codes.append(code_str)
    print(f"从 fund_data 表获取到 {len(fund_codes)} 个基金代码")
    return fund_codes

# 从 fund_values 表获取所有已有数据的基金代码
def get_existing_fund_codes(cursor):
    cursor.execute("SELECT DISTINCT fund_code FROM fund_values")
    existing = {row[0] for row in cursor.fetchall()}
    print(f"从 fund_values 表查看到 {len(existing)} 只基金已有数据")
    return existing

# 获取单个基金的历史净值数据（完整获取所有数据）
def get_fund_values(fund_code):
    base_url = 'https://fund.eastmoney.com/f10/F10DataApi.aspx'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Referer': 'https://fundf10.eastmoney.com/'
    }

    all_data = []
    page = 1
    has_more = True

    print(f"  正在获取基金 {fund_code} 的历史净值...")

    while has_more:
        url = f'{base_url}?type=lsjz&code={fund_code}&page={page}&per=20'

        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.encoding = 'utf-8'

            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find('table')

            if not table:
                print(f"  第{page}页没有找到表格，停止获取")
                break

            rows = table.find_all('tr')
            if len(rows) <= 1:
                print(f"  第{page}页没有数据，停止获取")
                break

            new_rows = 0
            for row in rows[1:]:
                cells = row.find_all('td')
                if len(cells) >= 6:
                    date = cells[0].get_text(strip=True)
                    unit_value = cells[1].get_text(strip=True) if cells[1].get_text(strip=True) else None
                    cumulative_value = cells[2].get_text(strip=True) if cells[2].get_text(strip=True) else None
                    daily_growth = cells[3].get_text(strip=True) if cells[3].get_text(strip=True) else None
                    purchase_status = cells[4].get_text(strip=True) if cells[4].get_text(strip=True) else None
                    redemption_status = cells[5].get_text(strip=True) if cells[5].get_text(strip=True) else None

                    data_item = {
                        'fund_code': fund_code,
                        'net_value_date': date,
                        'unit_net_value': unit_value,
                        'cumulative_net_value': cumulative_value,
                        'daily_growth_rate': daily_growth,
                        'purchase_status': purchase_status,
                        'redemption_status': redemption_status
                    }
                    all_data.append(data_item)
                    new_rows += 1

            print(f"  第{page}页获取到 {new_rows} 条数据")

            # 只有当数据量少于20且page>3时才停止，确保获取完整
            if new_rows == 0:
                break
            elif new_rows < 20 and page > 3:
                has_more = False
            else:
                page += 1

        except Exception as e:
            print(f"  获取基金 {fund_code} 第 {page} 页数据时出错: {e}")
            break

    print(f"  基金 {fund_code} 获取完成，共 {len(all_data)} 条记录")
    if len(all_data) > 0:
        print(f"    日期范围: {all_data[-1]['net_value_date']} 至 {all_data[0]['net_value_date']}")
    return all_data

# 保存基金数据到数据库
def save_fund_values_to_db(cursor, conn, fund_values):
    if not fund_values:
        return 0

    insert_count = 0
    for value_data in fund_values:
        try:
            insert_sql = """
            INSERT IGNORE INTO fund_values
            (fund_code, net_value_date, unit_net_value, cumulative_net_value,
             daily_growth_rate, purchase_status, redemption_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(insert_sql, (
                value_data['fund_code'],
                value_data['net_value_date'],
                value_data['unit_net_value'],
                value_data['cumulative_net_value'],
                value_data['daily_growth_rate'],
                value_data['purchase_status'],
                value_data['redemption_status']
            ))
            insert_count += 1
        except Exception as e:
            print(f"  保存数据时出错: {e}")
            continue

    conn.commit()
    return insert_count

FETCH_WORKERS = 10


def process_fund(fund_code):
    """线程工作函数：全量拉取 + 写入数据库，每个线程独立数据库连接"""
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()
    try:
        fund_values = get_fund_values(fund_code)
        saved = 0
        if fund_values:
            saved = save_fund_values_to_db(cursor, conn, fund_values)
        return fund_code, saved, None
    except Exception as e:
        return fund_code, 0, str(e)
    finally:
        cursor.close()
        conn.close()


def main():
    # 连接数据库
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    try:
        # 1. 创建 fund_values 表
        create_fund_values_table(cursor)

        # 2. 获取基金代码
        fund_data_codes = get_all_fund_codes(cursor)
        existing_codes = get_existing_fund_codes(cursor)

        # 3. 计算差异：只在 fund_data 中有、fund_values 中没有的基金
        missing_codes = [code for code in fund_data_codes if code not in existing_codes]
        total_missing = len(missing_codes)

        print(f"\n{'=' * 50}")
        print(f"  fund_data 基金总数: {len(fund_data_codes)}")
        print(f"  fund_values 已有:   {len(existing_codes)}")
        print(f"  需补充基金数:       {total_missing}")
        print(f"{'=' * 50}")

        if total_missing == 0:
            print("\n所有基金数据已完整，无需补充！")
            return

        # 4. 多线程并发补采
        total_records = 0
        success_count = 0
        error_count = 0

        print(f"\n[系统] 启动 {FETCH_WORKERS} 线程并发补采 {total_missing} 只基金...\n")

        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
            futures = {executor.submit(process_fund, code): code for code in missing_codes}

            for future in as_completed(futures):
                fund_code, saved, error = future.result()
                if error:
                    print(f"  ✗ {fund_code}: {error}", flush=True)
                    error_count += 1
                elif saved > 0:
                    print(f"  ✓ {fund_code}: +{saved} 条", flush=True)
                    total_records += saved
                    success_count += 1
                else:
                    print(f"  - {fund_code}: 无净值数据", flush=True)

        # 5. 汇总
        print(f"\n{'=' * 50}")
        print(f"  补充采集完成!")
        print(f"  处理基金: {total_missing} 只")
        print(f"  成功:     {success_count} 只")
        print(f"  新增记录: {total_records} 条")
        if error_count:
            print(f"  错误:     {error_count} 只")
        print(f"{'=' * 50}")

    except KeyboardInterrupt:
        print(f"\n\n[中断] 用户中断，进度已保留。下次运行自动跳过已完成的基金。")
    except Exception as e:
        print(f"\n[错误] {e}")
        import traceback
        traceback.print_exc()
    finally:
        cursor.close()
        conn.close()
        print("数据库连接已关闭")

if __name__ == "__main__":
    main()
