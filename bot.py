"""
Westwood Finance Bot — Slack (slack_bolt + Socket Mode)
Mirrors Discord bot logic: same Google Sheet columns, formulas, order IDs, timestamps.
"""

import os
import json
import re
import random
import string
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


# ─────────────────────────────────────────────
# APP INIT
# ─────────────────────────────────────────────

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "").strip()

if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
    print(f"DEBUG: SLACK_BOT_TOKEN present: {bool(SLACK_BOT_TOKEN)}")
    print(f"DEBUG: SLACK_APP_TOKEN present: {bool(SLACK_APP_TOKEN)}")
    raise Exception("Missing SLACK_BOT_TOKEN or SLACK_APP_TOKEN env variables")

# Diagnostic info (safe to log)
print(f"DEBUG: SLACK_BOT_TOKEN length: {len(SLACK_BOT_TOKEN)}, prefix: {SLACK_BOT_TOKEN[:8]}...")
print(f"DEBUG: SLACK_APP_TOKEN length: {len(SLACK_APP_TOKEN)}, prefix: {SLACK_APP_TOKEN[:8]}...")

if not SLACK_BOT_TOKEN.startswith("xoxb-"):
    print("WARNING: SLACK_BOT_TOKEN does not start with 'xoxb-'. It should be a Bot User OAuth Token.")
if not SLACK_APP_TOKEN.startswith("xapp-"):
    print("WARNING: SLACK_APP_TOKEN does not start with 'xapp-'. It should be an App-Level Token for Socket Mode.")

app = App(token=SLACK_BOT_TOKEN)

# ─────────────────────────────────────────────
# PERSISTENT TEAM STORAGE
# ─────────────────────────────────────────────

TEAMS_FILE = "user_teams.json"

def load_teams() -> dict:
    if os.path.exists(TEAMS_FILE):
        with open(TEAMS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_teams() -> None:
    with open(TEAMS_FILE, "w") as f:
        json.dump(user_teams, f, indent=2)

user_teams: dict = load_teams()

def get_team(user_id: str) -> str | None:
    entry = user_teams.get(user_id)
    if isinstance(entry, dict):
        return entry.get("team")
    return entry

def get_display_name(user_id: str, fallback: str) -> str:
    entry = user_teams.get(user_id)
    if isinstance(entry, dict):
        return entry.get("full_name") or fallback
    return fallback

# ─────────────────────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────────────────────

sheet = None

try:
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    google_creds_json = os.getenv("GOOGLE_CREDS")
    if google_creds_json:
        print("Loading Google credentials from GOOGLE_CREDS env var...")
        creds_dict = json.loads(google_creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        print("Loading Google credentials from credentials.json...")
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            "credentials.json",
            scope
        )

    client_gs = gspread.authorize(creds)
    sheet = client_gs.open("Westwood Finances").sheet1
    print("Google Sheets connected")
except Exception as e:
    print(f"Google Sheets FAILED: {e}")
    if not google_creds_json and not os.path.exists("credentials.json"):
        print("TIP: For Railway, set the GOOGLE_CREDS environment variable with the contents of your credentials.json file.")

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

TEAMS = ["FRC", "Kunai", "Hunga Munga", "Atlatl", "Slingshot"]
CATEGORIES = ["Hardware", "Software", "Outreach", "Food", "Miscellaneous"]

TEST_PARTS = [
    ("Test Servo Motor", "ServoKing", "https://example.com/servo", 12.99, 2, "hardware"),
    ("Test Limit Switch", "ElectroSupply", "https://example.com/switch", 3.49, 5, "hardware"),
    ("Test Aluminum Bracket", "MetalDepot", "https://example.com/bracket", 8.75, 4, "hardware"),
]

TEST_PASSWORD = "hi"

# ─────────────────────────────────────────────
# UTILS
# ─────────────────────────────────────────────

def generate_order_id():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

def get_next_row(ws):
    return len(ws.col_values(1)) + 1

def now_ct():
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%-m/%-d/%Y %H:%M:%S")

def write_order(ws, row, item, company, link, price, qty, notes, category, team, time, name):
    order_id = generate_order_id()

    ws.update(
        f"A{row}:K{row}",
        [[item, company, link, price, qty, notes, category, team, time, f"=PRODUCT(D{row}:E{row})", "Pending"]],
        value_input_option="USER_ENTERED",
    )

    ws.update(
        f"M{row}:O{row}",
        [[order_id, f'=IF(A{row}<>"", COUNTIF($A$3:A{row}, "<>"), "")', name]],
        value_input_option="USER_ENTERED",
    )

    return price * qty, order_id

# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────

@app.command("/set-team")
def set_team(ack, body, client):
    ack()
    client.views_open(trigger_id=body["trigger_id"], view={
        "type": "modal",
        "callback_id": "set_team_modal",
        "title": {"type": "plain_text", "text": "Set Team"},
        "submit": {"type": "plain_text", "text": "Save"},
        "blocks": []
    })

@app.command("/order")
def order(ack, body, client):
    ack()
    if not get_team(body["user_id"]):
        client.chat_postEphemeral(
            channel=body["channel_id"],
            user=body["user_id"],
            text="Set your team first using /set-team"
        )
        return

    client.views_open(trigger_id=body["trigger_id"], view={
        "type": "modal",
        "callback_id": "order_modal",
        "title": {"type": "plain_text", "text": "Order"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "blocks": []
    })

@app.command("/test")
def test(ack, body, client):
    ack()

    if not get_team(body["user_id"]):
        client.chat_postEphemeral(
            channel=body["channel_id"],
            user=body["user_id"],
            text="Set team first"
        )
        return

    client.views_open(trigger_id=body["trigger_id"], view={
        "type": "modal",
        "callback_id": "test_modal",
        "title": {"type": "plain_text", "text": "Test"},
        "submit": {"type": "plain_text", "text": "Run"},
        "blocks": []
    })

@app.command("/summary")
def summary(ack, body, client):
    ack()
    if not sheet:
        return

    rows = sheet.get_all_values()
    client.chat_postMessage(channel=body["channel_id"], text=f"Rows: {len(rows)}")

# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("Starting bot...")
    try:
        handler = SocketModeHandler(app, SLACK_APP_TOKEN)
        handler.start()
    except Exception as e:
        print("\n" + "!" * 50)
        print(f"CRITICAL ERROR: Bot failed to start: {e}")
        print("!" * 50 + "\n")
        
        if "invalid_auth" in str(e):
            print("--- TROUBLESHOOTING SLACK AUTH ---")
            print("1. Ensure SLACK_APP_TOKEN is an 'App-Level Token' (starts with xapp-).")
            print("2. Ensure SLACK_BOT_TOKEN is a 'Bot User OAuth Token' (starts with xoxb-).")
            print("3. Check that 'Socket Mode' is ENABLED in your Slack App settings.")
            print("4. Verify that you haven't wrapped the tokens in quotes in your Railway environment variables.")
            print("----------------------------------")
        traceback.print_exc()