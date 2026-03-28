# Kendo Skill Tree

An interactive Kendo skill tree visualizer built with Flask and SQLite. Track techniques, ranks, and progress through RPG-style skill trees.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000

## Data Tools

Export your current data to a SQL seed file (so it can be shared/version-controlled):

```bash
python data_tools.py export    # creates seed_data.sql from your database
python data_tools.py import    # loads seed_data.sql into a fresh database
```

When cloning this repo on a new machine, run `python data_tools.py import` to restore the data, then `python app.py`.
