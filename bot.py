"""
Westwood Finance Bot — Slack (slack_bolt + Socket Mode)
Mirrors Discord bot logic: same Google Sheet columns, formulas, order IDs, timestamps.
Runs locally 24/7 via Socket Mode (no public server needed).
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
    raise Exception("Missing SLACK_BOT_TOKEN or SLACK_APP_TOKEN env variables")

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
    return entry if isinstance(entry, str) else None


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

    # Get the directory where bot.py is located
    base_dir = os.path.dirname(os.path.abspath(__file__))
    creds_path = os.path.join(base_dir, "credentials.json")

    if os.path.exists(creds_path):
        print(f"Loading Google credentials from: {creds_path}")
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    elif os.getenv("GOOGLE_CREDS"):
        print("Loading Google credentials from GOOGLE_CREDS env var...")
        creds_json = os.getenv("GOOGLE_CREDS")
        creds_dict = json.loads(creds_json)
        
        if "private_key" in creds_dict:
            # Fix common newline escaping issues in environment variables
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        raise Exception(f"No credentials found at {creds_path} or in GOOGLE_CREDS env var.")

    client_gs = gspread.authorize(creds)
    sheet = client_gs.open("Westwood Finances").sheet1
    print("✅ Google Sheets connected")
except Exception as e:
    print(f"❌ Google Sheets FAILED: {e}")
    print("TIP: If using GOOGLE_CREDS env var, ensure the JSON is valid and the private_key has correct \\n characters.")

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

TEAMS = ["FRC", "Kunai", "Hunga Munga", "Atlatl", "Slingshot"]
CATEGORIES = ["Hardware", "Software", "Outreach", "Food", "Miscellaneous"]

LOG_CHANNEL = os.getenv("SLACK_LOG_CHANNEL", "")  # optional: channel ID to post order logs

TEST_PARTS = [
    ("Test Servo Motor",      "ServoKing",     "https://example.com/servo",   12.99, 2, "hardware"),
    ("Test Limit Switch",     "ElectroSupply", "https://example.com/switch",   3.49, 5, "hardware"),
    ("Test Aluminum Bracket", "MetalDepot",    "https://example.com/bracket",  8.75, 4, "hardware"),
    ("Test Arduino Nano",     "RoboShop",      "https://example.com/arduino", 22.00, 1, "hardware"),
    ("Test Rubber Wheel",     "WheelWorld",    "https://example.com/wheel",   15.50, 3, "hardware"),
    ("Test Battery Pack",     "PowerCell",     "https://example.com/battery", 34.99, 1, "hardware"),
    ("Test Steel Bolt Set",   "BoltBarn",      "https://example.com/bolts",    6.25, 10, "hardware"),
    ("Test CAD License",      "AutodeskTest",  "https://example.com/cad",     49.99, 1, "software"),
    ("Test Zip Ties (100pk)", "FastenerPro",   "https://example.com/zipties",  4.99, 2, "miscellaneous"),
    ("Test Bearing Kit",      "SpinRight",     "https://example.com/bearings",18.40, 2, "hardware"),
]

TEST_PASSWORD = "hi"

# ─────────────────────────────────────────────
# UTILS
# ─────────────────────────────────────────────


def generate_order_id() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def get_next_row(ws):
    col = ws.col_values(1)
    return len([x for x in col if x.strip() != ""]) + 1


def now_ct() -> str:
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%-m/%-d/%Y %H:%M:%S")


def write_order(ws, row, item, company, link, price, qty, notes, category, team, timestamp, name):
    """
    Column layout (mirrors Discord bot exactly):
    A=item, B=company, C=link, D=price, E=quantity,
    F=notes, G=category, H=team, I=timestamp,
    J=total (formula), K=Pending Review,
    L=(gap), M=order UUID, N=order count formula,
    O=full name
    """
    order_id = generate_order_id()
    total_formula = f"=PRODUCT(D{row}:E{row})"
    pending_review = "Pending Review"
    count_formula = f'=IF(A{row}<>"", COUNTIF($A$3:A{row}, "<>"), "")'

    ws.update(
        f"A{row}:K{row}",
        [[item, company, link, price, qty, notes, category.capitalize(), team, timestamp, total_formula, pending_review]],
        value_input_option="USER_ENTERED",
    )

    ws.update(
        f"M{row}:O{row}",
        [[order_id, count_formula, name]],
        value_input_option="USER_ENTERED",
    )

    return price * qty, order_id


# ─────────────────────────────────────────────
# /set-team  — modal with name + team dropdown
# ─────────────────────────────────────────────

@app.command("/set-team")
def cmd_set_team(ack, body, client):
    ack()
    client.views_open(trigger_id=body["trigger_id"], view={
        "type": "modal",
        "callback_id": "set_team_modal",
        "title": {"type": "plain_text", "text": "Set Your Team"},
        "submit": {"type": "plain_text", "text": "Save"},
        "blocks": [
            {
                "type": "input",
                "block_id": "name_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "name_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. Ishaan Desai"},
                },
                "label": {"type": "plain_text", "text": "Your Official Full Name"},
            },
            {
                "type": "input",
                "block_id": "team_block",
                "element": {
                    "type": "static_select",
                    "action_id": "team_select",
                    "placeholder": {"type": "plain_text", "text": "Select team"},
                    "options": [
                        {"text": {"type": "plain_text", "text": t}, "value": t}
                        for t in TEAMS
                    ],
                },
                "label": {"type": "plain_text", "text": "Team"},
            },
        ],
    })


@app.view("set_team_modal")
def handle_set_team(ack, body, view, client):
    ack()
    user_id = body["user"]["id"]
    vals = view["state"]["values"]

    full_name = vals["name_block"]["name_input"]["value"].strip()
    team = vals["team_block"]["team_select"]["selected_option"]["value"]

    user_teams[user_id] = {"full_name": full_name, "team": team}
    save_teams()

    client.chat_postMessage(
        channel=user_id,
        text=f"✅ You've been assigned to *{team}* as *{full_name}*! You can now use `/order`.",
    )


# ─────────────────────────────────────────────
# /order  — SINGLE modal with all fields
# ─────────────────────────────────────────────

@app.command("/order")
def cmd_order(ack, body, client):
    ack()
    user_id = body["user_id"]

    if not get_team(user_id):
        client.chat_postEphemeral(
            channel=body["channel_id"],
            user=user_id,
            text="⚠️ You need to set your team first! Run `/set-team` once before placing orders.",
        )
        return

    client.views_open(trigger_id=body["trigger_id"], view={
        "type": "modal",
        "callback_id": "order_modal",
        "private_metadata": body["channel_id"],
        "title": {"type": "plain_text", "text": "Place Order"},
        "submit": {"type": "plain_text", "text": "Submit Order"},
        "blocks": [
            {
                "type": "input",
                "block_id": "item_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "item_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. Servo Motor"},
                },
                "label": {"type": "plain_text", "text": "Item"},
            },
            {
                "type": "input",
                "block_id": "company_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "company_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. Amazon"},
                },
                "label": {"type": "plain_text", "text": "Company"},
            },
            {
                "type": "input",
                "block_id": "link_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "link_input",
                    "placeholder": {"type": "plain_text", "text": "https://..."},
                },
                "label": {"type": "plain_text", "text": "Link"},
            },
            {
                "type": "input",
                "block_id": "price_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "price_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. 12.99"},
                },
                "label": {"type": "plain_text", "text": "Price ($)"},
            },
            {
                "type": "input",
                "block_id": "qty_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "qty_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. 1"},
                },
                "label": {"type": "plain_text", "text": "Quantity"},
            },
            {
                "type": "input",
                "block_id": "category_block",
                "element": {
                    "type": "static_select",
                    "action_id": "category_select",
                    "placeholder": {"type": "plain_text", "text": "Select category"},
                    "options": [
                        {"text": {"type": "plain_text", "text": c}, "value": c.lower()}
                        for c in CATEGORIES
                    ],
                },
                "label": {"type": "plain_text", "text": "Category"},
            },
            {
                "type": "input",
                "block_id": "notes_block",
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "notes_input",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "Promo code, urgency, specs..."},
                },
                "label": {"type": "plain_text", "text": "Notes (optional)"},
            },
        ],
    })


@app.view("order_modal")
def handle_order(ack, body, view, client):
    vals = view["state"]["values"]
    user_id = body["user"]["id"]
    channel_id = view.get("private_metadata", "")

    # Parse inputs
    item = vals["item_block"]["item_input"]["value"].strip()
    company = vals["company_block"]["company_input"]["value"].strip()
    link = vals["link_block"]["link_input"]["value"].strip()
    price_raw = re.sub(r"[^0-9.]", "", vals["price_block"]["price_input"]["value"]) or "0"
    qty_raw = re.sub(r"[^0-9]", "", vals["qty_block"]["qty_input"]["value"]) or "1"
    category = vals["category_block"]["category_select"]["selected_option"]["value"]
    notes_val = vals["notes_block"]["notes_input"].get("value")
    notes = notes_val.strip() if notes_val else ""

    price = float(price_raw)
    qty = int(qty_raw)

    # Validate
    errors = {}
    if price <= 0:
        errors["price_block"] = "Price must be greater than 0."
    if qty <= 0:
        errors["qty_block"] = "Quantity must be at least 1."
    if errors:
        ack(response_action="errors", errors=errors)
        return

    ack()

    team = get_team(user_id) or "Unknown"
    display_name = get_display_name(user_id, body["user"].get("username", "Unknown"))
    timestamp = now_ct()

    # Write to Google Sheet
    if not sheet:
        client.chat_postMessage(channel=user_id, text="❌ Google Sheets is not connected. Order was NOT placed.")
        return

    try:
        row = get_next_row(sheet)
        total, order_id = write_order(
            sheet, row, item, company, link, price, qty,
            notes, category, team, timestamp, display_name,
        )
    except Exception:
        traceback.print_exc()
        client.chat_postMessage(channel=user_id, text="❌ Failed to write order to Google Sheet.")
        return

    # DM confirmation to the user
    client.chat_postMessage(
        channel=user_id,
        text=f"✅ Order placed: *{item} x{qty}* (Total: ${total:.2f}) — Order ID: `{order_id}`",
    )

    # Post to the channel (or log channel)
    post_channel = LOG_CHANNEL or channel_id
    if post_channel:
        link_display = f"<{link}|{item}>" if link else item
        client.chat_postMessage(
            channel=post_channel,
            text=(
                f"📦 *New Order Logged*\n"
                f"*Item:* {link_display}\n"
                f"*Company:* {company}\n"
                f"*Price:* ${price:.2f}\n"
                f"*Quantity:* {qty}\n"
                f"*Total:* ${total:.2f}\n"
                f"*Category:* {category.capitalize()}\n"
                f"*Notes:* {notes if notes else 'None'}\n"
                f"*Team:* {team}\n"
                f"*User:* <@{user_id}> ({display_name})\n"
                f"*Order ID:* `{order_id}`\n"
                f"*Time:* {timestamp}"
            ),
        )


# ─────────────────────────────────────────────
# /summary  — monthly spending by team & category
# ─────────────────────────────────────────────

@app.command("/summary")
def cmd_summary(ack, body, client):
    ack()

    if not sheet:
        client.chat_postEphemeral(
            channel=body["channel_id"], user=body["user_id"],
            text="❌ Google Sheets is not connected.",
        )
        return

    try:
        now = datetime.now(ZoneInfo("America/Chicago"))
        rows = sheet.get_all_values()
        text = build_summary_text(rows, now.month, now.year)
        client.chat_postMessage(channel=body["channel_id"], text=text)
    except Exception:
        traceback.print_exc()
        client.chat_postEphemeral(
            channel=body["channel_id"], user=body["user_id"],
            text="❌ Failed to generate summary.",
        )


def build_summary_text(rows: list[list], month: int, year: int) -> str:
    team_totals = {t: 0.0 for t in TEAMS}
    cat_totals = {c.lower(): 0.0 for c in CATEGORIES}
    grand_total = 0.0
    order_count = 0

    for row in rows[1:]:
        if len(row) < 10:
            row += [""] * (10 - len(row))

        timestamp_str = row[8].strip()
        total_str = row[9].strip()
        team = row[7].strip()
        category = row[6].strip()

        if not timestamp_str or not total_str:
            continue

        try:
            dt = datetime.strptime(timestamp_str, "%m/%d/%Y %H:%M:%S")
        except ValueError:
            continue

        if dt.month != month or dt.year != year:
            continue

        try:
            total = float(total_str)
        except ValueError:
            continue

        grand_total += total
        order_count += 1

        if team in team_totals:
            team_totals[team] += total
        if category.lower() in cat_totals:
            cat_totals[category.lower()] += total

    month_name = datetime(year, month, 1).strftime("%B %Y")

    lines = [
        f"📊 *Spending Summary for {month_name}*",
        f"*Grand Total:* ${grand_total:.2f} across {order_count} order{'s' if order_count != 1 else ''}",
        "",
        "*By Team:*",
    ]

    team_lines = [f"  `{t:<12}` ${v:.2f}" for t, v in team_totals.items() if v > 0]
    lines.extend(team_lines if team_lines else ["  No orders this month."])

    lines.append("")
    lines.append("*By Category:*")
    cat_lines = [f"  `{c.title():<14}` ${v:.2f}" for c, v in cat_totals.items() if v > 0]
    lines.extend(cat_lines if cat_lines else ["  No orders this month."])

    lines.append("")
    lines.append("_Data pulled from Westwood Finances sheet_")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# /test  — submit random test order (password-gated)
# ─────────────────────────────────────────────

@app.command("/test")
def cmd_test(ack, body, client):
    ack()
    user_id = body["user_id"]

    if not get_team(user_id):
        client.chat_postEphemeral(
            channel=body["channel_id"], user=user_id,
            text="⚠️ Set your team first using `/set-team`.",
        )
        return

    if not sheet:
        client.chat_postEphemeral(
            channel=body["channel_id"], user=user_id,
            text="❌ Google Sheets is not connected.",
        )
        return

    client.views_open(trigger_id=body["trigger_id"], view={
        "type": "modal",
        "callback_id": "test_modal",
        "private_metadata": body["channel_id"],
        "title": {"type": "plain_text", "text": "Test Order Auth"},
        "submit": {"type": "plain_text", "text": "Run Test"},
        "blocks": [
            {
                "type": "input",
                "block_id": "pw_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "pw_input",
                    "placeholder": {"type": "plain_text", "text": "Enter password"},
                },
                "label": {"type": "plain_text", "text": "Password"},
            }
        ],
    })


@app.view("test_modal")
def handle_test(ack, body, view, client):
    vals = view["state"]["values"]
    password = vals["pw_block"]["pw_input"]["value"].strip()

    if password != TEST_PASSWORD:
        ack(response_action="errors", errors={"pw_block": "Incorrect password."})
        return

    ack()

    user_id = body["user"]["id"]
    channel_id = view.get("private_metadata", "")
    team = get_team(user_id) or "Unknown"
    display_name = get_display_name(user_id, body["user"].get("username", "Unknown"))

    try:
        item, company, link, price, qty, category = random.choice(TEST_PARTS)
        notes = "SLACK TEST"
        timestamp = now_ct()
        row = get_next_row(sheet)

        total, order_id = write_order(
            sheet, row, item, company, link, price, qty,
            notes, category, team, timestamp, display_name,
        )

        client.chat_postMessage(
            channel=user_id,
            text=(
                f"🧪 *Test order submitted!*\n"
                f"*Item:* {item}  |  *Company:* {company}\n"
                f"*Price:* ${price:.2f} x{qty} = *${total:.2f}*\n"
                f"*Category:* {category.capitalize()}  |  *Team:* {team}\n"
                f"*Order ID:* `{order_id}`  |  *Row:* {row}"
            ),
        )

        post_channel = LOG_CHANNEL or channel_id
        if post_channel:
            client.chat_postMessage(
                channel=post_channel,
                text=(
                    f"🧪 *Test Order Logged*\n"
                    f"*Item:* <{link}|{item}>\n"
                    f"*Company:* {company}\n"
                    f"*Price:* ${price:.2f}  |  *Qty:* {qty}  |  *Total:* ${total:.2f}\n"
                    f"*Category:* {category.capitalize()}\n"
                    f"*Notes:* {notes}\n"
                    f"*Team:* {team}\n"
                    f"*User:* <@{user_id}> ({display_name})\n"
                    f"*Order ID:* `{order_id}`  |  *Time:* {timestamp}"
                ),
            )

    except Exception:
        traceback.print_exc()
        client.chat_postMessage(channel=user_id, text="❌ Test order failed. Check logs.")


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("🤖 Starting Westwood Finance Bot (Socket Mode)...")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()