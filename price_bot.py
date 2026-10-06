import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
HISTORY_FILE = os.getenv("HISTORY_FILE", "history.json")
STATE_FILE = os.getenv("STATE_FILE", "bot_state.json")
MAX_POINTS = int(os.getenv("MAX_POINTS", "120"))

USD_URL = "https://www.tgju.org/profile/price_dollar_rl"
GOLD_URL = "https://www.tgju.org/profile/geram18"
TEHRAN = ZoneInfo("Asia/Tehran")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def fmt_price(value):
    return f"{round(value):,}"


def fmt_pct(value):
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def get_tgju_price(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/140 Safari/537.36"
        )
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    selectors = [
        "span.value",
        '[data-field="price"]',
        "[data-price]",
        ".price",
        ".value",
    ]
    for selector in selectors:
        el = soup.select_one(selector)
        if not el:
            continue
        raw = el.get("data-price") or el.get_text(" ", strip=True)
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            # TGJU exposes these prices in Rial.
            return float(digits) / 10.0

    raise RuntimeError(f"Could not find price on TGJU: {url}")


def fetch_prices():
    usd = get_tgju_price(USD_URL)
    gold = get_tgju_price(GOLD_URL)
    print(f"USD={usd:,.0f} Toman | GOLD18={gold:,.0f} Toman")
    return usd, gold


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_history():
    data = load_json(HISTORY_FILE, {"usd": [], "gold": []})
    if not isinstance(data, dict):
        data = {"usd": [], "gold": []}
    data.setdefault("usd", [])
    data.setdefault("gold", [])
    return data


def save_history(data):
    save_json(HISTORY_FILE, data)


def add_history(data, key, price, now_iso):
    arr = data.setdefault(key, [])
    arr.append({"ts": now_iso, "price": price})
    data[key] = arr[-MAX_POINTS:]


def history_prices(data, key):
    out = []
    for item in data.get(key, []) or []:
        try:
            value = item.get("price") if isinstance(item, dict) else item
            if value is not None:
                out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def previous_price(data, key, current):
    vals = history_prices(data, key)
    return vals[-1] if vals else float(current)


def pct_change(current, previous):
    if not previous:
        return 0.0
    return ((current - previous) / previous) * 100.0


def build_message(title, emoji, price, change, now, first_run=False):
    if change > 0:
        direction = "⬆️"
        change_icon = "🟢"
        phrase = "بگا رفتین"
        phrase_icon = "🔴"
    elif change < 0:
        direction = "⬇️"
        change_icon = "🔴"
        phrase = "فکر کنم رفتن"
        phrase_icon = "🟢"
    else:
        direction = "➡️"
        change_icon = "⚪"
        phrase = "بدون تغییر" if first_run else "بدون تغییر"
        phrase_icon = "⚪"

    return (
        f"<b>{direction} {title} {emoji}</b>\n\n"
        f"💰 <b>{fmt_price(price)} تومان</b>\n"
        f"📊 تغییر: <b>{change_icon}{fmt_pct(change)}</b>\n"
        f"{phrase_icon} <tg-spoiler>{phrase}</tg-spoiler>\n\n"
        f"🕐 {now.strftime('%Y/%m/%d')} | {now.strftime('%H:%M:%S')}"
    )


def telegram(method, payload):
    r = requests.post(f"{TG_API}/{method}", json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error in {method}: {data}")
    return data["result"]


def send_message(text):
    result = telegram(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )
    return int(result["message_id"])


def pin_message(message_id):
    telegram(
        "pinChatMessage",
        {
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "disable_notification": True,
        },
    )


def unpin_our_previous_messages(state):
    """Only unpin message IDs saved by this bot; never touch other group pins."""
    previous = state.get("pinned_message_ids", [])
    if not isinstance(previous, list):
        previous = []

    for message_id in previous:
        try:
            telegram(
                "unpinChatMessage",
                {
                    "chat_id": CHAT_ID,
                    "message_id": int(message_id),
                },
            )
            print(f"Unpinned our previous message: {message_id}")
        except Exception as e:
            # If the message is already unpinned/deleted, continue normally.
            print(f"Could not unpin previous bot message {message_id}: {e}")


def main():
    now = datetime.now(TEHRAN)
    now_iso = now.isoformat()

    usd, gold = fetch_prices()
    history = load_history()
    state = load_json(STATE_FILE, {"pinned_message_ids": []})
    if not isinstance(state, dict):
        state = {"pinned_message_ids": []}

    usd_previous = previous_price(history, "usd", usd)
    gold_previous = previous_price(history, "gold", gold)
    usd_change = pct_change(usd, usd_previous)
    gold_change = pct_change(gold, gold_previous)

    # Build and send the two text-only messages.
    usd_text = build_message("دلار آزاد", "💵", usd, usd_change, now, not history_prices(history, "usd"))
    gold_text = build_message("طلای ۱۸ عیار", "🪙", gold, gold_change, now, not history_prices(history, "gold"))

    # Important: remove ONLY the pins previously created by this bot.
    unpin_our_previous_messages(state)

    new_ids = []
    for text in (usd_text, gold_text):
        message_id = send_message(text)
        pin_message(message_id)
        new_ids.append(message_id)
        print(f"Sent and pinned bot message: {message_id}")

    # Save the two message IDs so the next run knows exactly which pins belong to it.
    state["pinned_message_ids"] = new_ids
    save_json(STATE_FILE, state)

    # Save history after calculating the change, so the next run compares against this run.
    add_history(history, "usd", usd, now_iso)
    add_history(history, "gold", gold, now_iso)
    save_history(history)


if __name__ == "__main__":
    main()
