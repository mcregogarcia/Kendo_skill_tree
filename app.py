"""Kendo Skill Tree – RPG-style ability tracker with Flask + PostgreSQL.

Features:
  - Discord OAuth2 login
  - Admin / regular user roles
  - Teams & training sessions
  - Persistent PostgreSQL storage
"""

import json as _json
import os
import secrets
from datetime import datetime
from functools import wraps
from urllib.parse import urlencode

import psycopg2
import psycopg2.extras
import requests as http_requests
from flask import (
    Flask, render_template, jsonify, request,
    redirect, session, url_for, g,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# ── Config ────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "")
# Comma-separated list of Discord user IDs that get admin on first login
ADMIN_DISCORD_IDS = set(
    x.strip() for x in os.environ.get("ADMIN_DISCORD_IDS", "").split(",") if x.strip()
)

# ── Database helpers ──────────────────────────────────────────────────


def get_db():
    """Return a psycopg2 connection (one per request via flask.g)."""
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL)
        g.db.autocommit = False
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        if exc:
            db.rollback()
        db.close()


def _exec(sql, params=None, *, fetch=False, fetchone=False, commit=False):
    """Small helper to run SQL and optionally fetch / commit."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    result = None
    if fetchone:
        result = cur.fetchone()
    elif fetch:
        result = cur.fetchall()
    if commit:
        conn.commit()
    return result


# ── Schema creation ───────────────────────────────────────────────────


def init_db():
    """Create all tables if they don't exist (idempotent)."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            discord_id TEXT UNIQUE,
            name TEXT NOT NULL,
            avatar_url TEXT DEFAULT '',
            is_admin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS skillsets (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS skills (
            id SERIAL PRIMARY KEY,
            skillset_id INTEGER NOT NULL REFERENCES skillsets(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            x_pos REAL DEFAULT 400,
            y_pos REAL DEFAULT 300,
            color TEXT DEFAULT '#58a6ff'
        );

        CREATE TABLE IF NOT EXISTS skill_dependencies (
            skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
            required_skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
            PRIMARY KEY (skill_id, required_skill_id)
        );

        CREATE TABLE IF NOT EXISTS user_skills (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
            achieved INTEGER DEFAULT 0,
            achieved_date TEXT,
            PRIMARY KEY (user_id, skill_id)
        );

        CREATE TABLE IF NOT EXISTS teams (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS team_members (
            team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT DEFAULT 'member',
            joined_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (team_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS training_sessions (
            id SERIAL PRIMARY KEY,
            team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            scheduled_date TIMESTAMP,
            target_skills TEXT DEFAULT '[]',
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS session_attendance (
            session_id INTEGER NOT NULL REFERENCES training_sessions(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            attended BOOLEAN DEFAULT FALSE,
            notes TEXT DEFAULT '',
            PRIMARY KEY (session_id, user_id)
        );
    """)
    conn.commit()
    conn.close()


def seed_sample_data():
    """Load seed_data.sql into an empty database (if available)."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM skillsets")
    count = cur.fetchone()[0]
    if count > 0:
        conn.close()
        return

    seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_data.sql")
    if os.path.exists(seed_path):
        with open(seed_path, "r", encoding="utf-8") as f:
            sql = f.read()
        cur.execute(sql)
        conn.commit()
        print("Loaded seed data from seed_data.sql")
    conn.close()


# ── Auth helpers ──────────────────────────────────────────────────────


def current_user():
    """Return the logged-in user dict, or None."""
    uid = session.get("user_id")
    if not uid:
        return None
    return _exec("SELECT * FROM users WHERE id = %s", (uid,), fetchone=True)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Login required"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user or not user["is_admin"]:
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


# ── Discord OAuth2 ────────────────────────────────────────────────────

DISCORD_API = "https://discord.com/api/v10"
DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"


@app.route("/login/discord")
def discord_login():
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    }
    return redirect(f"{DISCORD_AUTH_URL}?{urlencode(params)}")


@app.route("/callback/discord")
def discord_callback():
    # Validate state
    if request.args.get("state") != session.pop("oauth_state", None):
        return "Invalid state parameter", 400

    code = request.args.get("code")
    if not code:
        return "Authorization failed", 400

    # Exchange code for token
    token_resp = http_requests.post(DISCORD_TOKEN_URL, data={
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)

    if token_resp.status_code != 200:
        app.logger.error("Discord token exchange failed: %s %s", token_resp.status_code, token_resp.text)
        return f"Token exchange failed: {token_resp.json().get('error_description', token_resp.text)}", 400

    access_token = token_resp.json().get("access_token")

    # Fetch Discord user info
    user_resp = http_requests.get(f"{DISCORD_API}/users/@me", headers={
        "Authorization": f"Bearer {access_token}",
    }, timeout=10)

    if user_resp.status_code != 200:
        return "Failed to fetch user info", 400

    d = user_resp.json()
    discord_id = d["id"]
    username = d.get("global_name") or d.get("username", "Unknown")
    avatar_hash = d.get("avatar", "")

    if avatar_hash:
        avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png?size=128"
    else:
        avatar_url = ""

    is_admin = discord_id in ADMIN_DISCORD_IDS

    # Upsert user
    row = _exec("SELECT * FROM users WHERE discord_id = %s", (discord_id,), fetchone=True)
    if row:
        _exec(
            "UPDATE users SET name = %s, avatar_url = %s WHERE discord_id = %s",
            (username, avatar_url, discord_id), commit=True,
        )
        uid = row["id"]
        # Promote to admin if in list and not already
        if is_admin and not row["is_admin"]:
            _exec("UPDATE users SET is_admin = TRUE WHERE id = %s", (uid,), commit=True)
    else:
        row = _exec(
            "INSERT INTO users (discord_id, name, avatar_url, is_admin) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (discord_id, username, avatar_url, is_admin),
            fetchone=True, commit=True,
        )
        uid = row["id"]

    session["user_id"] = uid
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/api/me")
def me():
    user = current_user()
    if not user:
        return jsonify(None)
    return jsonify({
        "id": user["id"],
        "name": user["name"],
        "avatar_url": user["avatar_url"],
        "is_admin": user["is_admin"],
    })


# ── Page ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Users ─────────────────────────────────────────────────────────────

@app.route("/api/users")
def get_users():
    rows = _exec("SELECT id, name, avatar_url, is_admin FROM users ORDER BY name", fetch=True)
    return jsonify([dict(r) for r in rows])


@app.route("/api/users", methods=["POST"])
@admin_required
def create_user():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    try:
        row = _exec(
            "INSERT INTO users (name) VALUES (%s) RETURNING id",
            (name,), fetchone=True, commit=True,
        )
    except psycopg2.IntegrityError:
        get_db().rollback()
        return jsonify({"error": "User already exists"}), 409
    return jsonify({"id": row["id"], "name": name}), 201


@app.route("/api/users/<int:uid>", methods=["DELETE"])
@admin_required
def delete_user(uid):
    _exec("DELETE FROM users WHERE id = %s", (uid,), commit=True)
    return "", 204


# ── Skillsets ─────────────────────────────────────────────────────────

@app.route("/api/skillsets")
def get_skillsets():
    rows = _exec("SELECT * FROM skillsets ORDER BY id", fetch=True)
    return jsonify([dict(r) for r in rows])


@app.route("/api/skillsets", methods=["POST"])
@admin_required
def create_skillset():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    try:
        row = _exec(
            "INSERT INTO skillsets (name, description) VALUES (%s, %s) RETURNING id",
            (name, data.get("description", "")),
            fetchone=True, commit=True,
        )
    except psycopg2.IntegrityError:
        get_db().rollback()
        return jsonify({"error": "Skillset already exists"}), 409
    return jsonify({"id": row["id"], "name": name}), 201


@app.route("/api/skillsets/<int:sid>", methods=["DELETE"])
@admin_required
def delete_skillset(sid):
    _exec("DELETE FROM skillsets WHERE id = %s", (sid,), commit=True)
    return "", 204


# ── Skills ────────────────────────────────────────────────────────────

@app.route("/api/skillsets/<int:sid>/skills")
def get_skills(sid):
    skills = _exec("SELECT * FROM skills WHERE skillset_id = %s", (sid,), fetch=True)
    deps = _exec(
        "SELECT d.skill_id, d.required_skill_id "
        "FROM skill_dependencies d "
        "JOIN skills s ON s.id = d.skill_id "
        "WHERE s.skillset_id = %s",
        (sid,), fetch=True,
    )
    return jsonify({
        "skills": [dict(r) for r in skills],
        "dependencies": [dict(r) for r in deps],
    })


@app.route("/api/skills", methods=["POST"])
@admin_required
def create_skill():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    ssid = data.get("skillset_id")
    if not name or not ssid:
        return jsonify({"error": "name and skillset_id required"}), 400
    row = _exec(
        "INSERT INTO skills (skillset_id, name, description, x_pos, y_pos, color) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (
            ssid, name, data.get("description", ""),
            data.get("x_pos", 400), data.get("y_pos", 300),
            data.get("color", "#58a6ff"),
        ),
        fetchone=True, commit=True,
    )
    return jsonify({"id": row["id"]}), 201


@app.route("/api/skills/<int:skid>", methods=["PUT"])
@admin_required
def update_skill(skid):
    data = request.get_json(force=True)
    allowed = ("name", "description", "x_pos", "y_pos", "color")
    fields, vals = [], []
    for col in allowed:
        if col in data:
            fields.append(f"{col} = %s")
            vals.append(data[col])
    if not fields:
        return jsonify({"error": "Nothing to update"}), 400
    vals.append(skid)
    _exec(f"UPDATE skills SET {', '.join(fields)} WHERE id = %s", vals, commit=True)
    return "", 204


@app.route("/api/skills/<int:skid>", methods=["DELETE"])
@admin_required
def delete_skill(skid):
    _exec("DELETE FROM skills WHERE id = %s", (skid,), commit=True)
    return "", 204


@app.route("/api/skills/positions", methods=["PUT"])
@admin_required
def update_positions():
    items = request.get_json(force=True)
    conn = get_db()
    cur = conn.cursor()
    for it in items:
        cur.execute(
            "UPDATE skills SET x_pos = %s, y_pos = %s WHERE id = %s",
            (it["x_pos"], it["y_pos"], it["id"]),
        )
    conn.commit()
    return "", 204


# ── Dependencies ──────────────────────────────────────────────────────

@app.route("/api/dependencies", methods=["POST"])
@admin_required
def add_dependency():
    data = request.get_json(force=True)
    sid = data.get("skill_id")
    rid = data.get("required_skill_id")
    if not sid or not rid or sid == rid:
        return jsonify({"error": "Invalid dependency"}), 400
    try:
        _exec(
            "INSERT INTO skill_dependencies VALUES (%s, %s)",
            (sid, rid), commit=True,
        )
    except psycopg2.IntegrityError:
        get_db().rollback()
        return jsonify({"error": "Dependency already exists"}), 409
    return "", 201


@app.route("/api/dependencies", methods=["DELETE"])
@admin_required
def remove_dependency():
    data = request.get_json(force=True)
    _exec(
        "DELETE FROM skill_dependencies WHERE skill_id = %s AND required_skill_id = %s",
        (data["skill_id"], data["required_skill_id"]),
        commit=True,
    )
    return "", 204


# ── User Progress ─────────────────────────────────────────────────────

@app.route("/api/users/<int:uid>/progress")
def get_progress(uid):
    rows = _exec(
        "SELECT skill_id, achieved_date FROM user_skills "
        "WHERE user_id = %s AND achieved = 1",
        (uid,), fetch=True,
    )
    return jsonify({str(r["skill_id"]): r["achieved_date"] for r in rows})


@app.route("/api/users/<int:uid>/toggle/<int:skid>", methods=["POST"])
@login_required
def toggle_skill(uid, skid):
    user = current_user()
    # Users can only toggle their own skills (admins can toggle anyone's)
    if user["id"] != uid and not user["is_admin"]:
        return jsonify({"error": "Forbidden"}), 403

    row = _exec(
        "SELECT achieved FROM user_skills WHERE user_id = %s AND skill_id = %s",
        (uid, skid), fetchone=True,
    )
    if row and row["achieved"]:
        _exec(
            "UPDATE user_skills SET achieved = 0, achieved_date = NULL "
            "WHERE user_id = %s AND skill_id = %s",
            (uid, skid), commit=True,
        )
        new_state = False
    else:
        now = datetime.now().isoformat()
        _exec(
            "INSERT INTO user_skills (user_id, skill_id, achieved, achieved_date) "
            "VALUES (%s, %s, 1, %s) "
            "ON CONFLICT (user_id, skill_id) DO UPDATE SET achieved = 1, achieved_date = %s",
            (uid, skid, now, now), commit=True,
        )
        new_state = True
    return jsonify({"achieved": new_state})


# ── Teams ─────────────────────────────────────────────────────────────

@app.route("/api/teams")
@login_required
def get_teams():
    rows = _exec(
        "SELECT t.*, u.name AS creator_name FROM teams t "
        "LEFT JOIN users u ON u.id = t.created_by ORDER BY t.name",
        fetch=True,
    )
    return jsonify([dict(r) for r in rows])


@app.route("/api/teams", methods=["POST"])
@login_required
def create_team():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    user = current_user()
    row = _exec(
        "INSERT INTO teams (name, description, created_by) "
        "VALUES (%s, %s, %s) RETURNING id",
        (name, data.get("description", ""), user["id"]),
        fetchone=True, commit=True,
    )
    # Auto-add creator as leader
    _exec(
        "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, 'leader')",
        (row["id"], user["id"]), commit=True,
    )
    return jsonify({"id": row["id"], "name": name}), 201


@app.route("/api/teams/<int:tid>", methods=["PUT"])
@login_required
def update_team(tid):
    data = request.get_json(force=True)
    user = current_user()
    leader = _exec(
        "SELECT * FROM team_members WHERE team_id = %s AND user_id = %s AND role = 'leader'",
        (tid, user["id"]), fetchone=True,
    )
    if not leader and not user["is_admin"]:
        return jsonify({"error": "Forbidden"}), 403
    _exec(
        "UPDATE teams SET name = %s, description = %s WHERE id = %s",
        (data.get("name", ""), data.get("description", ""), tid),
        commit=True,
    )
    return "", 204


@app.route("/api/teams/<int:tid>", methods=["DELETE"])
@login_required
def delete_team(tid):
    user = current_user()
    leader = _exec(
        "SELECT * FROM team_members WHERE team_id = %s AND user_id = %s AND role = 'leader'",
        (tid, user["id"]), fetchone=True,
    )
    if not leader and not user["is_admin"]:
        return jsonify({"error": "Forbidden"}), 403
    _exec("DELETE FROM teams WHERE id = %s", (tid,), commit=True)
    return "", 204


@app.route("/api/teams/<int:tid>/members")
@login_required
def get_team_members(tid):
    rows = _exec(
        "SELECT tm.*, u.name, u.avatar_url FROM team_members tm "
        "JOIN users u ON u.id = tm.user_id WHERE tm.team_id = %s ORDER BY u.name",
        (tid,), fetch=True,
    )
    return jsonify([dict(r) for r in rows])


@app.route("/api/teams/<int:tid>/members", methods=["POST"])
@login_required
def add_team_member(tid):
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    role = data.get("role", "member")
    if role not in ("member", "leader"):
        role = "member"
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    user = current_user()
    leader = _exec(
        "SELECT * FROM team_members WHERE team_id = %s AND user_id = %s AND role = 'leader'",
        (tid, user["id"]), fetchone=True,
    )
    if not leader and not user["is_admin"]:
        return jsonify({"error": "Forbidden"}), 403
    try:
        _exec(
            "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, %s)",
            (tid, user_id, role), commit=True,
        )
    except psycopg2.IntegrityError:
        get_db().rollback()
        return jsonify({"error": "Already a member"}), 409
    return "", 201


@app.route("/api/teams/<int:tid>/members/<int:uid>", methods=["DELETE"])
@login_required
def remove_team_member(tid, uid):
    user = current_user()
    leader = _exec(
        "SELECT * FROM team_members WHERE team_id = %s AND user_id = %s AND role = 'leader'",
        (tid, user["id"]), fetchone=True,
    )
    if not leader and not user["is_admin"]:
        return jsonify({"error": "Forbidden"}), 403
    _exec(
        "DELETE FROM team_members WHERE team_id = %s AND user_id = %s",
        (tid, uid), commit=True,
    )
    return "", 204


@app.route("/api/teams/<int:tid>/progress")
@login_required
def get_team_progress(tid):
    """Get aggregated progress for all team members across all skillsets."""
    members = _exec(
        "SELECT u.id, u.name, u.avatar_url FROM team_members tm "
        "JOIN users u ON u.id = tm.user_id WHERE tm.team_id = %s",
        (tid,), fetch=True,
    )
    result = []
    for m in members:
        skills = _exec(
            "SELECT skill_id, achieved_date FROM user_skills "
            "WHERE user_id = %s AND achieved = 1",
            (m["id"],), fetch=True,
        )
        result.append({
            "user_id": m["id"],
            "name": m["name"],
            "avatar_url": m["avatar_url"],
            "achieved": {str(s["skill_id"]): s["achieved_date"] for s in skills},
        })
    return jsonify(result)


# ── Training Sessions ─────────────────────────────────────────────────

@app.route("/api/training-sessions")
@login_required
def get_sessions():
    rows = _exec(
        "SELECT ts.*, t.name AS team_name, u.name AS creator_name "
        "FROM training_sessions ts "
        "LEFT JOIN teams t ON t.id = ts.team_id "
        "LEFT JOIN users u ON u.id = ts.created_by "
        "ORDER BY ts.scheduled_date DESC NULLS LAST",
        fetch=True,
    )
    return jsonify([dict(r) for r in rows])


@app.route("/api/training-sessions", methods=["POST"])
@login_required
def create_session():
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title required"}), 400
    user = current_user()
    target_skills = _json.dumps(data.get("target_skills", []))
    row = _exec(
        "INSERT INTO training_sessions (team_id, title, description, scheduled_date, target_skills, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (
            data.get("team_id"), title, data.get("description", ""),
            data.get("scheduled_date"), target_skills, user["id"],
        ),
        fetchone=True, commit=True,
    )
    return jsonify({"id": row["id"]}), 201


@app.route("/api/training-sessions/<int:sid>", methods=["PUT"])
@login_required
def update_session(sid):
    data = request.get_json(force=True)
    target_skills = _json.dumps(data.get("target_skills", []))
    _exec(
        "UPDATE training_sessions SET title = %s, description = %s, "
        "scheduled_date = %s, target_skills = %s WHERE id = %s",
        (
            data.get("title", ""), data.get("description", ""),
            data.get("scheduled_date"), target_skills, sid,
        ),
        commit=True,
    )
    return "", 204


@app.route("/api/training-sessions/<int:sid>", methods=["DELETE"])
@login_required
def delete_session(sid):
    _exec("DELETE FROM training_sessions WHERE id = %s", (sid,), commit=True)
    return "", 204


@app.route("/api/training-sessions/<int:sid>/attendance")
@login_required
def get_attendance(sid):
    rows = _exec(
        "SELECT sa.*, u.name FROM session_attendance sa "
        "JOIN users u ON u.id = sa.user_id WHERE sa.session_id = %s",
        (sid,), fetch=True,
    )
    return jsonify([dict(r) for r in rows])


@app.route("/api/training-sessions/<int:sid>/attendance", methods=["POST"])
@login_required
def mark_attendance(sid):
    data = request.get_json(force=True)
    user_id = data.get("user_id")
    attended = data.get("attended", False)
    notes = data.get("notes", "")
    _exec(
        "INSERT INTO session_attendance (session_id, user_id, attended, notes) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (session_id, user_id) DO UPDATE SET attended = %s, notes = %s",
        (sid, user_id, attended, notes, attended, notes),
        commit=True,
    )
    return "", 204


# ── Initialization ────────────────────────────────────────────────────

if DATABASE_URL:
    init_db()
    seed_sample_data()

if __name__ == "__main__":
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    print("Skill Tree running at http://127.0.0.1:5000")
    app.run(debug=False, port=5000)
