"""Kendo Skill Tree – Discord Bot

Slash-command interface to the same PostgreSQL database as the web app.
Users can view skill trees, mark achievements, and check progress
directly inside Discord.

Run standalone:  python bot.py
Deployed alongside the web app on Render.
"""

import os
import sys
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
import psycopg2
import psycopg2.extras

# ── Config ────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
ADMIN_DISCORD_IDS = set(
    x.strip()
    for x in os.environ.get("ADMIN_DISCORD_IDS", "").split(",")
    if x.strip()
)
WEB_URL = os.environ.get("WEB_APP_URL", "")

# ── Database helper ───────────────────────────────────────────────────


def _q(sql, params=None, *, fetch=False, fetchone=False, commit=False):
    """Run a query on a new connection and close it when done."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
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
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_user(discord_user) -> dict:
    """Get or create a DB user row from a Discord user/member."""
    discord_id = str(discord_user.id)
    avatar_url = str(discord_user.display_avatar.url) if discord_user.display_avatar else ""
    name = getattr(discord_user, "display_name", None) or discord_user.name

    row = _q("SELECT * FROM users WHERE discord_id = %s", (discord_id,), fetchone=True)
    if row:
        _q("UPDATE users SET name = %s, avatar_url = %s WHERE id = %s",
           (name, avatar_url, row["id"]), commit=True)
        return dict(row)

    is_admin = discord_id in ADMIN_DISCORD_IDS
    row = _q(
        "INSERT INTO users (discord_id, name, avatar_url, is_admin) "
        "VALUES (%s, %s, %s, %s) RETURNING *",
        (discord_id, name, avatar_url, is_admin),
        fetchone=True, commit=True,
    )
    return dict(row)


def progress_bar(current, total, length=12):
    """Unicode progress bar."""
    if total == 0:
        return "░" * length + " 0/0"
    filled = round(length * current / total)
    bar = "█" * filled + "░" * (length - filled)
    pct = round(100 * current / total)
    return f"{bar} {current}/{total} ({pct}%)"


# ── Bot setup ─────────────────────────────────────────────────────────

class SkillTreeBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

    async def on_ready(self):
        print(f"Bot ready: {self.user}  •  {len(self.tree.get_commands())} commands synced")


bot = SkillTreeBot()

# ── Autocomplete helpers ──────────────────────────────────────────────


async def skillset_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    rows = _q("SELECT name FROM skillsets ORDER BY name", fetch=True)
    return [
        app_commands.Choice(name=r["name"], value=r["name"])
        for r in rows
        if current.lower() in r["name"].lower()
    ][:25]


async def skill_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    rows = _q(
        "SELECT s.name, ss.name AS ss_name FROM skills s "
        "JOIN skillsets ss ON ss.id = s.skillset_id "
        "WHERE LOWER(s.name) LIKE %s ORDER BY s.name LIMIT 25",
        (f"%{current.lower()}%",),
        fetch=True,
    )
    return [
        app_commands.Choice(
            name=f"{r['name']}  ({r['ss_name']})"[:100],
            value=r["name"],
        )
        for r in rows
    ]


# ── Slash commands ────────────────────────────────────────────────────


@bot.tree.command(name="skilltrees", description="List all Kendo skill tree categories")
async def cmd_skilltrees(interaction: discord.Interaction):
    rows = _q(
        "SELECT s.id, s.name, s.description, COUNT(sk.id) AS skill_count "
        "FROM skillsets s LEFT JOIN skills sk ON sk.skillset_id = s.id "
        "GROUP BY s.id, s.name, s.description ORDER BY s.name",
        fetch=True,
    )
    if not rows:
        await interaction.response.send_message("No skill trees yet.", ephemeral=True)
        return

    embed = discord.Embed(title="🎋  Kendo Skill Trees", color=0x58A6FF)
    for r in rows:
        desc = (r["description"][:80] + "…") if r["description"] and len(r["description"]) > 80 else (r["description"] or "*No description*")
        embed.add_field(
            name=f"{r['name']}  •  {r['skill_count']} skills",
            value=desc,
            inline=False,
        )
    embed.set_footer(text="Use /skills <name> to see skills in a tree")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="skills", description="Show skills in a skill tree with your progress")
@app_commands.describe(skillset="Skill tree category name")
@app_commands.autocomplete(skillset=skillset_autocomplete)
async def cmd_skills(interaction: discord.Interaction, skillset: str):
    user = ensure_user(interaction.user)

    ss = _q("SELECT * FROM skillsets WHERE LOWER(name) = LOWER(%s)", (skillset,), fetchone=True)
    if not ss:
        await interaction.response.send_message(f"Skillset **{skillset}** not found.", ephemeral=True)
        return

    skills = _q("SELECT * FROM skills WHERE skillset_id = %s ORDER BY name", (ss["id"],), fetch=True)
    achieved_rows = _q(
        "SELECT skill_id FROM user_skills WHERE user_id = %s AND achieved = 1",
        (user["id"],), fetch=True,
    )
    achieved_ids = {r["skill_id"] for r in achieved_rows}

    lines = []
    for s in skills:
        icon = "✅" if s["id"] in achieved_ids else "⬜"
        lines.append(f"{icon} {s['name']}")

    count = sum(1 for s in skills if s["id"] in achieved_ids)

    embed = discord.Embed(
        title=f"🗡️  {ss['name']}",
        description=ss["description"] or None,
        color=0x58A6FF,
    )

    # Discord fields are max 1024 chars; split skills into chunks
    chunk_size = 20
    for i in range(0, len(lines), chunk_size):
        chunk = lines[i : i + chunk_size]
        label = "Skills" if i == 0 else "\u200b"
        embed.add_field(name=label, value="\n".join(chunk), inline=True)

    if not lines:
        embed.add_field(name="Skills", value="*No skills yet*", inline=False)

    embed.set_footer(text=f"Progress: {count}/{len(skills)}  •  /achieve <skill> to mark progress")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="achieve", description="Mark a skill as achieved")
@app_commands.describe(skill="Name of the skill to achieve")
@app_commands.autocomplete(skill=skill_autocomplete)
async def cmd_achieve(interaction: discord.Interaction, skill: str):
    user = ensure_user(interaction.user)

    sk = _q(
        "SELECT s.*, ss.name AS ss_name FROM skills s "
        "JOIN skillsets ss ON ss.id = s.skillset_id "
        "WHERE LOWER(s.name) = LOWER(%s)",
        (skill,), fetchone=True,
    )
    if not sk:
        await interaction.response.send_message(f"Skill **{skill}** not found.", ephemeral=True)
        return

    # Already achieved?
    existing = _q(
        "SELECT achieved FROM user_skills WHERE user_id = %s AND skill_id = %s",
        (user["id"], sk["id"]), fetchone=True,
    )
    if existing and existing["achieved"]:
        await interaction.response.send_message(
            f"You already achieved **{sk['name']}**! Use `/unachieve` to remove it.",
            ephemeral=True,
        )
        return

    now = datetime.now().isoformat()
    _q(
        "INSERT INTO user_skills (user_id, skill_id, achieved, achieved_date) "
        "VALUES (%s, %s, 1, %s) "
        "ON CONFLICT (user_id, skill_id) DO UPDATE SET achieved = 1, achieved_date = %s",
        (user["id"], sk["id"], now, now), commit=True,
    )

    # Progress in this skillset
    total = _q("SELECT COUNT(*) AS c FROM skills WHERE skillset_id = %s", (sk["skillset_id"],), fetchone=True)["c"]
    done = _q(
        "SELECT COUNT(*) AS c FROM user_skills us "
        "JOIN skills s ON s.id = us.skill_id "
        "WHERE us.user_id = %s AND s.skillset_id = %s AND us.achieved = 1",
        (user["id"], sk["skillset_id"]), fetchone=True,
    )["c"]

    embed = discord.Embed(
        title="🎯  Skill Achieved!",
        description=f"**{interaction.user.display_name}** achieved **{sk['name']}**!",
        color=0x00FF00,
    )
    embed.add_field(name=sk["ss_name"], value=progress_bar(done, total), inline=False)
    if sk["description"]:
        embed.add_field(name="Description", value=sk["description"][:200], inline=False)

    # Public message so the channel can see
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unachieve", description="Remove a skill achievement")
@app_commands.describe(skill="Name of the skill to remove")
@app_commands.autocomplete(skill=skill_autocomplete)
async def cmd_unachieve(interaction: discord.Interaction, skill: str):
    user = ensure_user(interaction.user)

    sk = _q("SELECT * FROM skills WHERE LOWER(name) = LOWER(%s)", (skill,), fetchone=True)
    if not sk:
        await interaction.response.send_message(f"Skill **{skill}** not found.", ephemeral=True)
        return

    _q(
        "UPDATE user_skills SET achieved = 0, achieved_date = NULL "
        "WHERE user_id = %s AND skill_id = %s",
        (user["id"], sk["id"]), commit=True,
    )
    await interaction.response.send_message(
        f"↩️ Removed **{sk['name']}** from your achievements.", ephemeral=True
    )


@bot.tree.command(name="progress", description="View skill tree progress")
@app_commands.describe(member="User to check (leave empty for yourself)")
async def cmd_progress(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    target = member or interaction.user
    user = ensure_user(target)

    skillsets = _q("SELECT * FROM skillsets ORDER BY name", fetch=True)

    embed = discord.Embed(
        title=f"📊  Progress – {target.display_name}",
        color=0x58A6FF,
    )
    if target.display_avatar:
        embed.set_thumbnail(url=target.display_avatar.url)

    total_achieved = 0
    total_skills = 0

    for ss in skillsets:
        total = _q("SELECT COUNT(*) AS c FROM skills WHERE skillset_id = %s", (ss["id"],), fetchone=True)["c"]
        if total == 0:
            continue
        done = _q(
            "SELECT COUNT(*) AS c FROM user_skills us "
            "JOIN skills s ON s.id = us.skill_id "
            "WHERE us.user_id = %s AND s.skillset_id = %s AND us.achieved = 1",
            (user["id"], ss["id"]), fetchone=True,
        )["c"]
        total_achieved += done
        total_skills += total
        embed.add_field(name=ss["name"], value=progress_bar(done, total), inline=False)

    embed.set_footer(text=f"Total: {total_achieved}/{total_skills} skills achieved")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leaderboard", description="Top kenshi by achievements")
async def cmd_leaderboard(interaction: discord.Interaction):
    rows = _q(
        "SELECT u.name, COUNT(us.skill_id) AS achieved "
        "FROM users u "
        "JOIN user_skills us ON us.user_id = u.id AND us.achieved = 1 "
        "GROUP BY u.id, u.name "
        "ORDER BY achieved DESC LIMIT 10",
        fetch=True,
    )
    if not rows:
        await interaction.response.send_message("No achievements yet!", ephemeral=True)
        return

    total_skills = _q("SELECT COUNT(*) AS c FROM skills", fetchone=True)["c"]
    medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 7

    lines = []
    for i, r in enumerate(rows):
        pct = round(100 * r["achieved"] / total_skills) if total_skills else 0
        lines.append(f"{medals[i]} **{r['name']}** — {r['achieved']} skills ({pct}%)")

    embed = discord.Embed(
        title="🏆  Kendo Skill Tree Leaderboard",
        description="\n".join(lines),
        color=0xFFD700,
    )
    embed.set_footer(text=f"Out of {total_skills} total skills")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="web", description="Get the link to the full visual skill tree")
async def cmd_web(interaction: discord.Interaction):
    url = WEB_URL or "https://kendo-skill-tree.onrender.com"
    await interaction.response.send_message(
        f"🌐 View the full interactive skill tree:\n{url}",
        ephemeral=True,
    )


# ── Entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("DISCORD_BOT_TOKEN not set — bot will not start.")
        sys.exit(0)
    if not DATABASE_URL:
        print("DATABASE_URL not set — bot cannot start.")
        sys.exit(1)
    bot.run(BOT_TOKEN)
