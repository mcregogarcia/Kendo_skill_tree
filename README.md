# Kendo Skill Tree

An interactive Kendo skill tree visualizer built with Flask and PostgreSQL. Track techniques, ranks, and progress through RPG-style skill trees. Features Discord login, teams, training sessions, and responsive mobile support.

## Features

- **Discord OAuth2 login** – Users authenticate via Discord
- **Role-based access** – Admins can edit skill trees; members track their own progress
- **Teams** – Group users into teams and view aggregated progress heatmaps
- **Training Sessions** – Schedule and track training sessions for teams
- **Responsive** – Works on desktop, tablet, and mobile (touch pan/pinch zoom)
- **Persistent storage** – PostgreSQL (no more data loss on Render redeploys)

## Local Development

```bash
pip install -r requirements.txt

# Set environment variables (or use a .env file)
export DATABASE_URL="postgresql://user:pass@localhost:5432/kendo_skill_tree"
export SECRET_KEY="some-random-secret"
export DISCORD_CLIENT_ID="your-discord-app-client-id"
export DISCORD_CLIENT_SECRET="your-discord-app-client-secret"
export DISCORD_REDIRECT_URI="http://127.0.0.1:5000/callback/discord"
export ADMIN_DISCORD_IDS="your-discord-user-id"

python app.py
```

Then open http://127.0.0.1:5000

## Discord App Setup

1. Go to https://discord.com/developers/applications
2. Create a new application
3. Under **OAuth2**, add redirect URI: `https://your-domain.com/callback/discord`
4. Copy the **Client ID** and **Client Secret** into your env vars
5. Set your own Discord User ID in `ADMIN_DISCORD_IDS` (comma-separated for multiple admins)

To find your Discord User ID: Enable Developer Mode in Discord settings, then right-click your username and "Copy User ID".

## Deployment on Render

1. Create a **PostgreSQL** database on Render (free tier works)
2. Create a **Web Service** pointing to this repo
3. Set environment variables in the Render dashboard:
   - `DATABASE_URL` → from your Render PostgreSQL (Internal Database URL)
   - `SECRET_KEY` → generate a random string
   - `DISCORD_CLIENT_ID` → from Discord Developer Portal
   - `DISCORD_CLIENT_SECRET` → from Discord Developer Portal
   - `DISCORD_REDIRECT_URI` → `https://kendo-skill-tree.onrender.com/callback/discord`
   - `ADMIN_DISCORD_IDS` → your Discord User ID
4. Deploy! The app auto-creates tables and loads seed data on first run.

## Data Tools

Export/import data from the PostgreSQL database:

```bash
export DATABASE_URL="postgresql://..."
python data_tools.py export    # creates seed_data.sql from PostgreSQL
python data_tools.py import    # loads seed_data.sql into PostgreSQL
```

## Tech Stack

- **Backend**: Flask + psycopg2 + gunicorn
- **Database**: PostgreSQL
- **Auth**: Discord OAuth2
- **Frontend**: Vanilla JS, SVG skill tree with pan/zoom/touch
