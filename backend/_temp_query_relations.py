import sqlite3

db = sqlite3.connect('G:/games/AIChat/backend/data/graph.db')
cursor = db.execute(
    'SELECT from_type, from_name, to_type, to_name, rel_type FROM relations ORDER BY from_name, to_name'
)
rows = cursor.fetchall()
for r in rows:
    print(f'{r[0]}|{r[1]} --[{r[4]}]--> {r[2]}|{r[3]}')
print(f'\n共 {len(rows)} 条关系')
db.close()
