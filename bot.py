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
app = App(token=os.environ["SLACK_BOT_TOKEN"])

# ─────────────────────────────────────────────
# PERSISTENT TEAM STORAGE
# { user_id: {"team": "FRC", "full_name": "Ishaan Desai"} }
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
    if isinstance(entry, str):
        return entry
    return None

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
    creds_dict = json.loads(os.environ["GOOGLE_CREDS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client_gs = gspread.authorize(creds)
    sheet = client_gs.open("Westwood Finances").sheet1
    print("✅ Google Sheets connected")
except Exception as e:
    print("❌ Google Sheets FAILED:", e)

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
TEAMS      = ["FRC", "Kunai", "Hunga Munga", "Atlatl", "Slingshot"]
CATEGORIES = ["Hardware", "Software", "Outreach", "Food", "Miscellaneous"]

TEST_PARTS = [
    ("Test Servo Motor",      "ServoKing",     "https://example.com/servo",    12.99,  2, "hardware"),
    ("Test Limit Switch",     "ElectroSupply", "https://example.com/switch",    3.49,  5, "hardware"),
    ("Test Aluminum Bracket", "MetalDepot",    "https://example.com/bracket",   8.75,  4, "hardware"),
    ("Test Arduino Nano",     "RoboShop",      "https://example.com/arduino",  22.00,  1, "hardware"),
    ("Test Rubber Wheel",     "WheelWorld",    "https://example.com/wheel",    15.50,  3, "hardware"),
    ("Test Battery Pack",     "PowerCell",     "https://example.com/battery",  34.99,  1, "hardware"),
    ("Test Steel Bolt Set",   "BoltBarn",      "https://example.com/bolts",     6.25, 10, "hardware"),
    ("Test CAD License",      "AutodeskTest",  "https://example.com/cad",      49.99,  1, "software"),
    ("Test Zip Ties (100pk)", "FastenerPro",   "https://example.com/zipties",   4.99,  2, "miscellaneous"),
    ("Test Bearing Kit",      "SpinRight",     "https://example.com/bearings", 18.40,  2, "hardware"),
]

TEST_PASSWORD = "hi"

# ─────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────
def generate_order_id() -> str:
    """6-char uppercase alphanumeric, e.g. 2B5SWE"""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

def get_next_row(ws) -> int:
    col = ws.col_values(1)
    return len([x for x in col if x.strip() != ""]) + 1

def chicago_now() -> str:
    return datetime.now(ZoneInfo("America/Chicago")).strftime("%-m/%-d/%Y %H:%M:%S")

def write_order_to_sheet(ws, row, item, company, link, price, quantity,
                          notes, category, team, timestamp, display_name) -> tuple[float, str]:
    """
    Identical column layout to the Discord bot:
      A=item  B=company  C=link   D=price  E=quantity
      F=notes G=category H=team   I=timestamp  J=total(formula)
      K=Pending Review   L=(gap)  M=order UUID  N=count formula  O=full name
    """
    order_id      = generate_order_id()
    total_formula = f"=PRODUCT(D{row}:E{row})"
    count_formula = f'=IF(A{row}<>"", COUNTIF($A$3:A{row}, "<>"), "")'

    ws.update(
        f"A{row}:K{row}",
        [[
            item, company, link, price, quantity,
            notes,
            category.capitalize(),
            team,
            timestamp,
            total_formula,
            "Pending Review",
        ]],
        value_input_option="USER_ENTERED",
    )
    ws.update(
        f"M{row}:O{row}",
        [[order_id, count_formula, display_name]],
        value_input_option="USER_ENTERED",
    )
    return price * quantity, order_id

# ─────────────────────────────────────────────
# SUMMARY TEXT BUILDER
# ─────────────────────────────────────────────
def build_summary_text(rows: list[list], month: int, year: int) -> str:
    team_totals = {t: 0.0 for t in TEAMS}
    cat_totals  = {c.lower(): 0.0 for c in CATEGORIES}
    grand_total = 0.0
    order_count = 0

    for row in rows[1:]:
        if len(row) < 10:
            row += [""] * (10 - len(row))
        ts_str, total_str = row[8].strip(), row[9].strip()
        team, category    = row[7].strip(), row[6].strip().lower()

        if not ts_str or not total_str:
            continue
        try:
            dt = datetime.strptime(ts_str, "%m/%d/%Y %H:%M:%S")
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
        if category in cat_totals:
            cat_totals[category] += total

    month_label = datetime(year, month, 1).strftime("%B %Y")
    lines = [
        f"*📊 Spending Summary — {month_label}*",
        f"*Grand Total:* ${grand_total:.2f} across {order_count} order{'s' if order_count != 1 else ''}",
        "",
        "*By Team:*",
    ]
    team_rows = [f"  `{t:<12}` ${v:.2f}" for t, v in team_totals.items() if v > 0]
    lines += team_rows or ["  No orders this month."]
    lines += ["", "*By Category:*"]
    cat_rows = [f"  `{c.title():<14}` ${v:.2f}" for c, v in cat_totals.items() if v > 0]
    lines += cat_rows or ["  No orders this month."]
    lines.append("\n_Data pulled from Westwood Finances sheet_")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# MODAL BUILDERS
# ─────────────────────────────────────────────
def set_team_modal_view() -> dict:
    return {
        "type": "modal",
        "callback_id": "set_team_modal",
        "title":  {"type": "plain_text", "text": "Set Your Team"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close":  {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Select your team and enter your official name. *This only needs to be done once.*",
                },
            },
            {
                "type": "input",
                "block_id": "team_block",
                "label": {"type": "plain_text", "text": "Team"},
                "element": {
                    "type": "static_select",
                    "action_id": "team_select",
                    "placeholder": {"type": "plain_text", "text": "Select a team"},
                    "options": [
                        {"text": {"type": "plain_text", "text": t}, "value": t}
                        for t in TEAMS
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "name_block",
                "label": {"type": "plain_text", "text": "Official First and Last Name"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "name_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. Ishaan Desai"},
                    "min_length": 2,
                    "max_length": 80,
                },
            },
        ],
    }


def order_modal_view(channel_id: str) -> dict:
    return {
        "type": "modal",
        "callback_id": "order_modal",
        "title":  {"type": "plain_text", "text": "Place an Order"},
        "submit": {"type": "plain_text", "text": "Submit Order"},
        "close":  {"type": "plain_text", "text": "Cancel"},
        "private_metadata": json.dumps({"channel_id": channel_id}),
        "blocks": [
            {
                "type": "input",
                "block_id": "item_block",
                "label": {"type": "plain_text", "text": "Item"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "item_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. REV Smart Motor"},
                },
            },
            {
                "type": "input",
                "block_id": "company_block",
                "label": {"type": "plain_text", "text": "Company / Vendor"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "company_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. REV Robotics"},
                },
            },
            {
                "type": "input",
                "block_id": "link_block",
                "label": {"type": "plain_text", "text": "Product Link"},
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "link_input",
                    "placeholder": {"type": "plain_text", "text": "https://..."},
                },
            },
            {
                "type": "input",
                "block_id": "price_block",
                "label": {"type": "plain_text", "text": "Price per unit ($)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "price_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. 12.99"},
                },
            },
            {
                "type": "input",
                "block_id": "qty_block",
                "label": {"type": "plain_text", "text": "Quantity"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "qty_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. 2"},
                },
            },
            {
                "type": "input",
                "block_id": "category_block",
                "label": {"type": "plain_text", "text": "Category"},
                "element": {
                    "type": "static_select",
                    "action_id": "category_select",
                    "placeholder": {"type": "plain_text", "text": "Select a category"},
                    "options": [
                        {"text": {"type": "plain_text", "text": c}, "value": c.lower()}
                        for c in CATEGORIES
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "notes_block",
                "label": {"type": "plain_text", "text": "Notes (optional)"},
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "notes_input",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "Promo code, urgency, specs..."},
                },
            },
        ],
    }


def test_modal_view(channel_id: str) -> dict:
    return {
        "type": "modal",
        "callback_id": "test_modal",
        "title":  {"type": "plain_text", "text": "Test Order"},
        "submit": {"type": "plain_text", "text": "Submit Test"},
        "close":  {"type": "plain_text", "text": "Cancel"},
        "private_metadata": json.dumps({"channel_id": channel_id}),
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Submits a random fake order to verify the bot and sheet are working.",
                },
            },
            {
                "type": "input",
                "block_id": "password_block",
                "label": {"type": "plain_text", "text": "Password"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "password_input",
                    "placeholder": {"type": "plain_text", "text": "Enter password"},
                },
            },
        ],
    }


# ─────────────────────────────────────────────
# SLASH COMMANDS
# ─────────────────────────────────────────────

@app.command("/set-team")
def cmd_set_team(ack, body, client):
    ack()
    client.views_open(trigger_id=body["trigger_id"], view=set_team_modal_view())


@app.command("/order")
def cmd_order(ack, body, client):
    ack()
    user_id    = body["user_id"]
    channel_id = body["channel_id"]

    if not get_team(user_id):
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="⚠️ You need to set your team first! Run `/set-team` once before placing orders.",
        )
        return

    client.views_open(
        trigger_id=body["trigger_id"],
        view=order_modal_view(channel_id),
    )


@app.command("/summary")
def cmd_summary(ack, body, client):
    ack()
    channel_id = body["channel_id"]
    user_id    = body["user_id"]

    if not sheet:
        client.chat_postEphemeral(
            channel=channel_id, user=user_id,
            text="❌ Google Sheets is not connected.",
        )
        return

    try:
        now  = datetime.now(ZoneInfo("America/Chicago"))
        rows = sheet.get_all_values()
        text = build_summary_text(rows, now.month, now.year)
        client.chat_postMessage(channel=channel_id, text=text)
    except Exception:
        traceback.print_exc()
        client.chat_postEphemeral(
            channel=channel_id, user=user_id,
            text="❌ Failed to generate summary.",
        )


@app.command("/test")
def cmd_test(ack, body, client):
    ack()
    user_id    = body["user_id"]
    channel_id = body["channel_id"]

    if not get_team(user_id):
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="⚠️ You need to set your team first! Run `/set-team` once before using `/test`.",
        )
        return

    if not sheet:
        client.chat_postEphemeral(
            channel=channel_id, user=user_id,
            text="❌ Google Sheets is not connected.",
        )
        return

    client.views_open(
        trigger_id=body["trigger_id"],
        view=test_modal_view(channel_id),
    )


# ─────────────────────────────────────────────
# MODAL SUBMISSION HANDLERS
# ─────────────────────────────────────────────

@app.view("set_team_modal")
def handle_set_team(ack, body, view, client):
    ack()
    user_id   = body["user"]["id"]
    vals      = view["state"]["values"]
    team      = vals["team_block"]["team_select"]["selected_option"]["value"]
    full_name = vals["name_block"]["name_input"]["value"].strip()

    user_teams[user_id] = {"team": team, "full_name": full_name}
    save_teams()

    client.chat_postMessage(
        channel=user_id,
        text=f"✅ You've been assigned to *{team}* as *{full_name}*! You can now use `/order`.",
    )


@app.view("order_modal")
def handle_order_modal(ack, body, view, client, logger):
    ack()
    try:
        user_id  = body["user"]["id"]
        username = body["user"]["username"]
        vals     = view["state"]["values"]

        item     = vals["item_block"]["item_input"]["value"].strip()
        company  = vals["company_block"]["company_input"]["value"].strip()
        link     = (vals["link_block"]["link_input"].get("value") or "").strip()
        category = vals["category_block"]["category_select"]["selected_option"]["value"]
        notes    = (vals["notes_block"]["notes_input"].get("value") or "").strip()

        price_raw = re.sub(r"[^0-9.]", "", vals["price_block"]["price_input"]["value"]) or "0"
        qty_raw   = re.sub(r"[^0-9]",  "", vals["qty_block"]["qty_input"]["value"])     or "1"
        price     = float(price_raw)
        quantity  = int(qty_raw)

        team         = get_team(user_id) or "Unknown"
        display_name = get_display_name(user_id, username)
        timestamp    = chicago_now()

        if sheet:
            row             = get_next_row(sheet)
            total, order_id = write_order_to_sheet(
                sheet, row, item, company, link, price, quantity,
                notes, category, team, timestamp, display_name,
            )
        else:
            total    = price * quantity
            order_id = generate_order_id()

        try:
            meta       = json.loads(view.get("private_metadata") or "{}")
            channel_id = meta.get("channel_id", user_id)
        except Exception:
            channel_id = user_id

        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=(
                f"✅ Order placed: *{item} x{quantity}* "
                f"(Total: ${total:.2f}) — Order ID: `{order_id}`"
            ),
        )

        item_text = f"<{link}|{item}>" if link else item
        client.chat_postMessage(
            channel=channel_id,
            text=(
                f"📦 *New Order Logged*\n"
                f"*Item:* {item_text}\n"
                f"*Company:* {company}\n"
                f"*Price:* ${price:.2f}\n"
                f"*Quantity:* {quantity}\n"
                f"*Total:* ${total:.2f}\n"
                f"*Category:* {category.capitalize()}\n"
                f"*Notes:* {notes or 'None'}\n"
                f"*Team:* {team}\n"
                f"*User:* <@{user_id}> ({display_name})\n"
                f"*Order ID:* `{order_id}`\n"
                f"*Time:* {timestamp}"
            ),
        )

    except Exception:
        logger.error(traceback.format_exc())
        try:
            client.chat_postMessage(
                channel=body["user"]["id"],
                text="❌ Failed to process your order. Please try again or contact an admin.",
            )
        except Exception:
            pass


@app.view("test_modal")
def handle_test_modal(ack, body, view, client, logger):
    user_id  = body["user"]["id"]
    username = body["user"]["username"]
    vals     = view["state"]["values"]
    password = vals["password_block"]["password_input"]["value"].strip()

    # Validate password before closing modal
    if password != TEST_PASSWORD:
        ack(
            response_action="errors",
            errors={"password_block": "Incorrect password."},
        )
        return

    ack()

    try:
        item, company, link, price, quantity, category = random.choice(TEST_PARTS)
        notes        = "SLACK TEST"
        team         = get_team(user_id) or "Unknown"
        display_name = get_display_name(user_id, username)
        timestamp    = chicago_now()

        row             = get_next_row(sheet)
        total, order_id = write_order_to_sheet(
            sheet, row, item, company, link, price, quantity,
            notes, category, team, timestamp, display_name,
        )

        try:
            meta       = json.loads(view.get("private_metadata") or "{}")
            channel_id = meta.get("channel_id", user_id)
        except Exception:
            channel_id = user_id

        # Ephemeral confirmation
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=(
                f"🧪 *Test order submitted successfully!*\n"
                f"*Item:* {item}\n"
                f"*Company:* {company}\n"
                f"*Price:* ${price:.2f} x{quantity} = *${total:.2f}*\n"
                f"*Category:* {category.capitalize()}\n"
                f"*Team:* {team}\n"
                f"*Name on sheet:* {display_name}\n"
                f"*Order ID:* `{order_id}`\n"
                f"*Row written:* {row}"
            ),
        )

        # Public channel log
        item_text = f"<{link}|{item}>" if link else item
        client.chat_postMessage(
            channel=channel_id,
            text=(
                f"🧪 *Test Order Logged*\n"
                f"*Item:* {item_text}\n"
                f"*Company:* {company}\n"
                f"*Price:* ${price:.2f}\n"
                f"*Quantity:* {quantity}\n"
                f"*Total:* ${total:.2f}\n"
                f"*Category:* {category.capitalize()}\n"
                f"*Notes:* {notes}\n"
                f"*Team:* {team}\n"
                f"*User:* <@{user_id}> ({display_name})\n"
                f"*Order ID:* `{order_id}`\n"
                f"*Time:* {timestamp}"
            ),
        )

    except Exception:
        logger.error(traceback.format_exc())
        try:
            client.chat_postMessage(
                channel=body["user"]["id"],
                text="❌ Test order failed. Check logs.",
            )
        except Exception:
            pass


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("🤖 Starting Westwood Finance Bot (Socket Mode)...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()