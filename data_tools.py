"""Export / Import skill_tree.db data as a SQL script.

Usage:
    python data_tools.py export          # writes seed_data.sql
    python data_tools.py import          # loads seed_data.sql into a fresh DB
"""

import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill_tree.db")
SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_data.sql")

TABLES = ["users", "skillsets", "skills", "skill_dependencies", "user_skills"]


def export_data():
    if not os.path.exists(DB_PATH):
        print("No database found. Nothing to export.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    lines = ["-- Auto-generated seed data from skill_tree.db\n"]

    for table in TABLES:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            continue
        cols = rows[0].keys()
        lines.append(f"\n-- {table}")
        for row in rows:
            vals = []
            for v in row:
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, str):
                    vals.append("'" + v.replace("'", "''") + "'")
                else:
                    vals.append(str(v))
            lines.append(
                f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join(vals)});"
            )

    conn.close()

    with open(SEED_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Exported to {SEED_PATH}")


def import_data():
    if not os.path.exists(SEED_PATH):
        print(f"No {SEED_PATH} found. Nothing to import.")
        return

    # First, make sure tables exist by running init_db from app
    from app import init_db
    init_db()

    with open(SEED_PATH, "r", encoding="utf-8") as f:
        sql = f.read()

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(sql)
    conn.commit()
    conn.close()
    print("Imported seed data into skill_tree.db")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python data_tools.py [export|import]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "export":
        export_data()
    elif cmd == "import":
        import_data()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
