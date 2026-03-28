"""Export / Import skill_tree data as a SQL script (PostgreSQL version).

Usage:
    python data_tools.py export          # writes seed_data.sql from PostgreSQL
    python data_tools.py import          # loads seed_data.sql into PostgreSQL
"""

import os
import sys

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_data.sql")

TABLES = ["users", "skillsets", "skills", "skill_dependencies", "user_skills"]


def export_data():
    if not DATABASE_URL:
        print("Set DATABASE_URL environment variable first.")
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    lines = ["-- Auto-generated seed data from PostgreSQL\n"]

    for table in TABLES:
        cur.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()
        if not rows:
            continue
        cols = list(rows[0].keys())
        lines.append(f"\n-- {table}")
        for row in rows:
            vals = []
            for c in cols:
                v = row[c]
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, str):
                    vals.append("'" + v.replace("'", "''") + "'")
                elif isinstance(v, bool):
                    vals.append("TRUE" if v else "FALSE")
                else:
                    vals.append(str(v))
            lines.append(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(vals)}) ON CONFLICT DO NOTHING;"
            )

    # Reset sequences
    lines.append("\n-- Reset sequences")
    lines.append("SELECT setval('users_id_seq', (SELECT COALESCE(MAX(id),0) FROM users));")
    lines.append("SELECT setval('skillsets_id_seq', (SELECT COALESCE(MAX(id),0) FROM skillsets));")
    lines.append("SELECT setval('skills_id_seq', (SELECT COALESCE(MAX(id),0) FROM skills));")

    conn.close()

    with open(SEED_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Exported to {SEED_PATH}")


def import_data():
    if not DATABASE_URL:
        print("Set DATABASE_URL environment variable first.")
        return

    if not os.path.exists(SEED_PATH):
        print(f"No {SEED_PATH} found. Nothing to import.")
        return

    # Make sure tables exist
    from app import init_db
    init_db()

    with open(SEED_PATH, "r", encoding="utf-8") as f:
        sql = f.read()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()
    conn.close()
    print("Imported seed data into PostgreSQL")


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
