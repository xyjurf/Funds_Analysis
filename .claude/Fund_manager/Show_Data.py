import pymysql

conn = pymysql.connect(
    host='localhost',
    user='root',
    password='weilian.x',
    port=3306,
    database='Fund_DATA'
)

cursor = conn.cursor()

cursor.execute("SELECT * FROM fund_values LIMIT 100000")
data = cursor.fetchall()

cursor.execute("DESCRIBE fund_values")
columns = [col[0] for col in cursor.fetchall()]

print("列名:", "\t".join(columns))
print("-" * 200)

for row in data:
    row_str = "\t".join(str(cell) if cell is not None else "NULL" for cell in row)
    print(row_str)

print(f"\n共 {len(data)} 条数据")

cursor.close()
conn.close()