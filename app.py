"""Skill Tree – RPG-style ability tracker with Flask + SQLite."""

from flask import Flask, render_template, jsonify, request
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill_tree.db")


# ── Database helpers ──────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skillsets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skillset_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            x_pos REAL DEFAULT 400,
            y_pos REAL DEFAULT 300,
            color TEXT DEFAULT '#58a6ff',
            FOREIGN KEY (skillset_id) REFERENCES skillsets(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS skill_dependencies (
            skill_id INTEGER NOT NULL,
            required_skill_id INTEGER NOT NULL,
            PRIMARY KEY (skill_id, required_skill_id),
            FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE,
            FOREIGN KEY (required_skill_id) REFERENCES skills(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS user_skills (
            user_id INTEGER NOT NULL,
            skill_id INTEGER NOT NULL,
            achieved INTEGER DEFAULT 0,
            achieved_date TEXT,
            PRIMARY KEY (user_id, skill_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


def seed_sample_data():
    """Load seed_data.sql into an empty database (if available)."""
    conn = get_db()
    if conn.execute("SELECT COUNT(*) FROM skillsets").fetchone()[0] > 0:
        conn.close()
        return

    seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_data.sql")
    if os.path.exists(seed_path):
        with open(seed_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        print("Loaded seed data from seed_data.sql")
    conn.close()


# ── Page ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Users ─────────────────────────────────────────────────────────────

@app.route("/api/users")
def get_users():
    conn = get_db()
    rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/users", methods=["POST"])
def create_user():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (name) VALUES (?)", (name,))
        uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "User already exists"}), 409
    conn.close()
    return jsonify({"id": uid, "name": name}), 201


@app.route("/api/users/<int:uid>", methods=["DELETE"])
def delete_user(uid):
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return "", 204


# ── Skillsets ─────────────────────────────────────────────────────────

@app.route("/api/skillsets")
def get_skillsets():
    conn = get_db()
    rows = conn.execute("SELECT * FROM skillsets ORDER BY id").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/skillsets", methods=["POST"])
def create_skillset():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO skillsets (name, description) VALUES (?,?)",
            (name, data.get("description", "")),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Skillset already exists"}), 409
    conn.close()
    return jsonify({"id": sid, "name": name}), 201


@app.route("/api/skillsets/<int:sid>", methods=["DELETE"])
def delete_skillset(sid):
    conn = get_db()
    conn.execute("DELETE FROM skillsets WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return "", 204


# ── Skills ────────────────────────────────────────────────────────────

@app.route("/api/skillsets/<int:sid>/skills")
def get_skills(sid):
    conn = get_db()
    skills = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM skills WHERE skillset_id=?", (sid,)
        ).fetchall()
    ]
    deps = [
        dict(r)
        for r in conn.execute(
            "SELECT d.skill_id, d.required_skill_id "
            "FROM skill_dependencies d "
            "JOIN skills s ON s.id = d.skill_id "
            "WHERE s.skillset_id=?",
            (sid,),
        ).fetchall()
    ]
    conn.close()
    return jsonify({"skills": skills, "dependencies": deps})


@app.route("/api/skills", methods=["POST"])
def create_skill():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    ssid = data.get("skillset_id")
    if not name or not ssid:
        return jsonify({"error": "name and skillset_id required"}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO skills (skillset_id,name,description,x_pos,y_pos,color) "
        "VALUES (?,?,?,?,?,?)",
        (
            ssid,
            name,
            data.get("description", ""),
            data.get("x_pos", 400),
            data.get("y_pos", 300),
            data.get("color", "#58a6ff"),
        ),
    )
    skid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return jsonify({"id": skid}), 201


@app.route("/api/skills/<int:skid>", methods=["PUT"])
def update_skill(skid):
    data = request.get_json(force=True)
    allowed = ("name", "description", "x_pos", "y_pos", "color")
    fields, vals = [], []
    for col in allowed:
        if col in data:
            fields.append(f"{col}=?")
            vals.append(data[col])
    if not fields:
        return jsonify({"error": "Nothing to update"}), 400
    vals.append(skid)
    conn = get_db()
    conn.execute(f"UPDATE skills SET {','.join(fields)} WHERE id=?", vals)
    conn.commit()
    conn.close()
    return "", 204


@app.route("/api/skills/<int:skid>", methods=["DELETE"])
def delete_skill(skid):
    conn = get_db()
    conn.execute("DELETE FROM skills WHERE id=?", (skid,))
    conn.commit()
    conn.close()
    return "", 204


@app.route("/api/skills/positions", methods=["PUT"])
def update_positions():
    items = request.get_json(force=True)
    conn = get_db()
    for it in items:
        conn.execute(
            "UPDATE skills SET x_pos=?, y_pos=? WHERE id=?",
            (it["x_pos"], it["y_pos"], it["id"]),
        )
    conn.commit()
    conn.close()
    return "", 204


# ── Dependencies ──────────────────────────────────────────────────────

@app.route("/api/dependencies", methods=["POST"])
def add_dependency():
    data = request.get_json(force=True)
    sid = data.get("skill_id")
    rid = data.get("required_skill_id")
    if not sid or not rid or sid == rid:
        return jsonify({"error": "Invalid dependency"}), 400
    conn = get_db()
    try:
        conn.execute("INSERT INTO skill_dependencies VALUES (?,?)", (sid, rid))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Dependency already exists"}), 409
    conn.close()
    return "", 201


@app.route("/api/dependencies", methods=["DELETE"])
def remove_dependency():
    data = request.get_json(force=True)
    conn = get_db()
    conn.execute(
        "DELETE FROM skill_dependencies WHERE skill_id=? AND required_skill_id=?",
        (data["skill_id"], data["required_skill_id"]),
    )
    conn.commit()
    conn.close()
    return "", 204


# ── User Progress ─────────────────────────────────────────────────────

@app.route("/api/users/<int:uid>/progress")
def get_progress(uid):
    conn = get_db()
    rows = conn.execute(
        "SELECT skill_id, achieved_date FROM user_skills "
        "WHERE user_id=? AND achieved=1",
        (uid,),
    ).fetchall()
    conn.close()
    return jsonify({str(r["skill_id"]): r["achieved_date"] for r in rows})


@app.route("/api/users/<int:uid>/toggle/<int:skid>", methods=["POST"])
def toggle_skill(uid, skid):
    conn = get_db()
    row = conn.execute(
        "SELECT achieved FROM user_skills WHERE user_id=? AND skill_id=?",
        (uid, skid),
    ).fetchone()
    if row and row["achieved"]:
        conn.execute(
            "UPDATE user_skills SET achieved=0, achieved_date=NULL "
            "WHERE user_id=? AND skill_id=?",
            (uid, skid),
        )
        new_state = False
    else:
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO user_skills (user_id,skill_id,achieved,achieved_date) "
            "VALUES (?,?,1,?) "
            "ON CONFLICT(user_id,skill_id) DO UPDATE SET achieved=1, achieved_date=?",
            (uid, skid, now, now),
        )
        new_state = True
    conn.commit()
    conn.close()
    return jsonify({"achieved": new_state})


# ── Initialization (runs for both local and production) ───────────────

init_db()
seed_sample_data()

if __name__ == "__main__":
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    print("Skill Tree running at http://127.0.0.1:5000")
    app.run(debug=False, port=5000)
