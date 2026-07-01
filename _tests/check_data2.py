import sqlite3, json
from pathlib import Path
DB = Path(__file__).resolve().parent.parent / "data" / "graph.db"
c = sqlite3.connect(str(DB))
c.row_factory = sqlite3.Row

# All AXIS relations
print("=== AXIS → X ===")
rows = c.execute("SELECT to_name, rel_type FROM relations WHERE from_name='AXIS' ORDER BY rel_type, to_name").fetchall()
for r in rows:
    print(f"  AXIS --[{r['rel_type']}]--> {r['to_name']}")

print("\n=== Midnight → X ===")
rows = c.execute("SELECT to_name, rel_type FROM relations WHERE from_name='Midnight' ORDER BY rel_type, to_name").fetchall()
for r in rows:
    print(f"  Midnight --[{r['rel_type']}]--> {r['to_name']}")

print("\n=== AIChat → X ===")
rows = c.execute("SELECT to_name, rel_type FROM relations WHERE from_name='AIChat' ORDER BY rel_type, to_name").fetchall()
for r in rows:
    print(f"  AIChat --[{r['rel_type']}]--> {r['to_name']}")

c.close()
