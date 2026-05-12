import sqlite3, json
from pathlib import Path
DB = Path(__file__).resolve().parent.parent / "data" / "graph.db"
c = sqlite3.connect(str(DB))
c.row_factory = sqlite3.Row

# Check relation from_names
rows = c.execute("SELECT from_name, COUNT(*) as cnt FROM relations GROUP BY from_name").fetchall()
print("=== from_name distribution ===")
for r in rows:
    print(f"  {r['from_name']}: {r['cnt']}")

# Check sample of root relations
rows2 = c.execute("SELECT from_name, to_name, rel_type FROM relations LIMIT 20").fetchall()
print("\n=== sample relations ===")
for r in rows2:
    print(f"  {r['from_name']} --[{r['rel_type']}]--> {r['to_name']}")

print("\n=== entity count ===")
print(c.execute("SELECT COUNT(*) FROM entities").fetchone()[0])

c.close()
