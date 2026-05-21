import requests
import re
import time
import pymysql

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://fund.eastmoney.com/data/fundranking.html'
}

conn = pymysql.connect(
    host='localhost',
    user='root',
    password='weilian.x',
    port=3306
)
cursor = conn.cursor()

cursor.execute("CREATE DATABASE IF NOT EXISTS Fund_DATA")
print("数据库 Fund_DATA 创建成功")

cursor.execute("USE Fund_DATA")
cursor.execute("DROP TABLE IF EXISTS fund_data")

columns = [
    'INT',
    'VARCHAR(100) CHARACTER SET utf8mb4',
    'VARCHAR(20)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'DECIMAL(10,4)',
    'VARCHAR(20)',
    'DECIMAL(10,4)'
]

column_names = [
    'fund_code', 'fund_name', 'date', 'unit_nav', 'cumulative_nav',
    'daily_growth', 'week_1', 'month_1', 'month_3', 'month_6',
    'year_1', 'year_2', 'year_3', 'this_year', 'since_inception',
    'custom', 'fee'
]

create_table_sql = f"CREATE TABLE IF NOT EXISTS fund_data ({', '.join([f'`{name}` {col}' for name, col in zip(column_names, columns)])}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
print("创建表的SQL:", create_table_sql)

cursor.execute(create_table_sql)
conn.commit()
print("表创建成功")

page = 1
total_count = 0

while True:
    print(f"\n正在获取第 {page} 页...")
    
    url = f'https://fund.eastmoney.com/data/rankhandler.aspx?op=ph&dt=kf&ft=all&rs=&gs=0&sc=1nzf&st=desc&sd=2025-04-28&ed=2026-04-28&qdii=&zq=&gg=&gzbd=&gzfs=&bbzt=&sfbb=&sp=1&pn=50&pi={page}'
    response = requests.get(url, headers=headers)
    response.encoding = 'utf-8-sig'
    
    match = re.search(r'var rankData = \{datas:(\[.*?\])', response.text)
    if not match:
        print(f"第 {page} 页没有找到数据，结束获取")
        break
    
    datas_str = match.group(1)
    datas = re.findall(r'"([^"]+)"', datas_str)
    
    if not datas:
        print(f"第 {page} 页没有数据，结束获取")
        break
    
    page_count = 0
    for data in datas:
        try:
            fields = data.split(',')
            
            if len(fields) < 18:
                continue
            
            fund_code = int(fields[0]) if fields[0] else None
            fund_name = fields[1] if fields[1] else None
            date = fields[3] if fields[3] else None
            unit_nav = float(fields[4]) if fields[4] else None
            cumulative_nav = float(fields[5]) if fields[5] else None
            daily_growth = float(fields[6]) / 100 if fields[6] else None
            week_1 = float(fields[7]) / 100 if fields[7] else None
            month_1 = float(fields[8]) / 100 if fields[8] else None
            month_3 = float(fields[9]) / 100 if fields[9] else None
            month_6 = float(fields[10]) / 100 if fields[10] else None
            year_1 = float(fields[11]) / 100 if fields[11] else None
            year_2 = float(fields[12]) / 100 if fields[12] else None
            year_3 = float(fields[13]) / 100 if fields[13] else None
            this_year = float(fields[14]) / 100 if fields[14] else None
            since_inception = float(fields[15]) / 100 if fields[15] else None
            custom = fields[16] if fields[16] else None
            fee = float(fields[17]) if fields[17] else None
            
            row_data = [fund_code, fund_name, date, unit_nav, cumulative_nav,
                       daily_growth, week_1, month_1, month_3, month_6,
                       year_1, year_2, year_3, this_year, since_inception, custom, fee]
            
            print(f"  第 {page_count + 1} 行: 基金代码={fund_code}, 基金简称={fund_name}")
            
            placeholders = ', '.join(['%s'] * len(row_data))
            insert_sql = f"INSERT INTO fund_data ({', '.join([f'`{name}`' for name in column_names])}) VALUES ({placeholders})"
            
            cursor.execute(insert_sql, row_data)
            page_count += 1
            total_count += 1
            
        except Exception as e:
            print(f"解析数据时出错: {e}")
            continue
    
    conn.commit()
    print(f"第 {page} 页完成，本次 {page_count} 条，累计 {total_count} 条")
    
    if page_count == 0 or len(datas) < 50:
        print(f"第 {page} 页数据不足50条，结束获取")
        break
    
    page += 1
    time.sleep(1)

print(f"\n数据存储完成！共 {total_count} 条数据")

cursor.close()
conn.close()