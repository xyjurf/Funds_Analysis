import pymysql

conn = pymysql.connect(
    host='localhost',
    user='root',
    password='weilian.x',
    port=3306,
    database='Fund_DATA'
)
cursor = conn.cursor()

# 1. 创建空表（相同结构）
cursor.execute("DROP TABLE IF EXISTS fund_values_copy")
cursor.execute("CREATE TABLE fund_values_copy LIKE fund_values")
conn.commit()
print("表结构创建完成")

# 2. 分批复制数据
batch_size = 100000
offset = 0
total = 0

while True:
    sql = f"INSERT INTO fund_values_copy SELECT * FROM fund_values LIMIT {batch_size} OFFSET {offset}"
    affected = cursor.execute(sql)
    conn.commit()
    if affected == 0:
        break
    total += affected
    offset += batch_size
    print(f"已复制 {total} 行...")

print(f"复制完成，共 {total} 行")

cursor.close()
conn.close()
