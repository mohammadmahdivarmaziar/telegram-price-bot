import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jdatetime
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
CHAT_ID = os.environ["CHAT_ID"].strip()
ADMIN_ID = os.environ["ADMIN_ID"].strip()
FORCE_SEND = os.getenv("FORCE_SEND", "false").strip().lower() == "true"

USD_URL = "https://gem.tgju.org/profile/price_dollar_rl"
GOLD_URL = "https://gem.tgju.org/profile/geram18"

SCHEDULE_FILE = "schedule.json"
STATE_FILE = "price_state.json"
OFFSET_FILE = "telegram_offset.json"

# GitHub Actions runs every 5 minutes. Each run keeps Telegram polling alive
# for 4 minutes, leaving a small safety gap before the next scheduled run.
POLL_SECONDS = 240
REQUEST_TIMEOUT = 30
MAX_ACCEPTABLE_MOVE = 0.20  # 20% sanity guard against a broken scrape

TEHRAN = timezone(timedelta(hours=3, minutes=30))

INTERVALS = {
    15: "15 دقیقه",
    30: "30 دقیقه",
    60: "1 ساعت",
    120: "2 ساعت",
    240: "4 ساعت",
    360: "6 ساعت",
    720: "12 ساعت",
    1440: "24 ساعت",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.7,en;q=0.6",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return default


def save_json(path: str, data: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def normalize_digits(value: str) -> str:
    return str(value).translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
    )


def parse_int(value: str) -> Optional[int]:
    value = normalize_digits(value)
    value = (
        value.replace(",", "")
        .replace("٬", "")
        .replace(" ", "")
        .replace("\u200c", "")
    )
    match = re.search(r"\d+", value)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def valid_rial_price(value: Optional[int], kind: str) -> bool:
    if value is None:
        return False
    if kind == "usd":
        return 500_000 <= value <= 20_000_000
    if kind == "gold":
        return 50_000_000 <= value <= 2_000_000_000
    return False


def extract_current_rial(html: str, kind: str) -> int:
    """Extract TGJU's current price in Rial.

    TGJU currently renders the target value near the literal label
    'نرخ فعلی'. We prioritize that exact label and only then fall back
    to DOM elements / table rows to tolerate minor layout changes.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = normalize_digits(soup.get_text(" ", strip=True))
    text = re.sub(r"\s+", " ", text)

    # Most reliable current TGJU pattern:
    # نرخ فعلی:: 2,633,950 2.06
    # The first number is the live price; the second number is daily %.
    exact_patterns = [
        r"نرخ\s*فعلی\s*::?\s*([0-9][0-9,٬]*)(?![0-9,٬])",
        r"نرخ\s*فعلی\s*:\s*([0-9][0-9,٬]*)(?![0-9,٬])",
    ]
    for pattern in exact_patterns:
        for match in re.finditer(pattern, text):
            value = parse_int(match.group(1))
            if valid_rial_price(value, kind):
                return int(value)

    # Fallback: inspect rows / elements whose text contains "نرخ فعلی".
    for node in soup.find_all(["tr", "div", "span", "li", "td", "p"]):
        node_text = normalize_digits(node.get_text(" ", strip=True))
        if "نرخ فعلی" not in node_text:
            continue
        numbers = re.findall(r"[0-9][0-9,٬]{5,}", node_text)
        for raw in numbers:
            value = parse_int(raw)
            if valid_rial_price(value, kind):
                return int(value)

    # Last fallback: common price-related classes.
    selectors = [
        '[data-field="price"]',
        '[data-field="current"]',
        '[data-field="last"]',
        ".price",
        ".price-value",
        ".current-price",
        ".profile-price",
    ]
    for selector in selectors:
        for node in soup.select(selector):
            value = parse_int(node.get_text(" ", strip=True))
            if valid_rial_price(value, kind):
                return int(value)

    raise ValueError(f"TGJU current {kind} price could not be extracted")


def get_market_price(kind: str) -> int:
    url = USD_URL if kind == "usd" else GOLD_URL
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    rial = extract_current_rial(response.text, kind)
    return rial // 10  # TGJU's page unit is Rial; bot displays Toman.


def now_tehran() -> datetime:
    return datetime.now(timezone.utc).astimezone(TEHRAN)


def jalali_now() -> jdatetime.datetime:
    return jdatetime.datetime.fromgregorian(
        datetime=now_tehran().replace(tzinfo=None)
    )


def calculate_change(previous: Optional[int], current: int) -> Optional[float]:
    if previous is None or previous <= 0:
        return None
    return ((current - previous) / previous) * 100.0


def suspicious_change(previous: Optional[int], current: int) -> bool:
    if previous is None or previous <= 0:
        return False
    return abs(current - previous) / previous > MAX_ACCEPTABLE_MOVE


def telegram(method: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = requests.post(
        url,
        json=payload or {},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data.get("result")


def send_message(text: str) -> Dict[str, Any]:
    return telegram(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


def pin_message(message_id: int) -> None:
    telegram(
        "pinChatMessage",
        {
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "disable_notification": True,
        },
    )


def build_price_message(title: str, price: int, change: Optional[float]) -> str:
    jdt = jalali_now()
    date_text = jdt.strftime("%Y/%m/%d")
    time_text = jdt.strftime("%H:%M:%S")

    if change is None:
        direction = "➡️"
        percent = "⚪ 0.00%"
        comment = "⚪ <tg-spoiler>قیمت مرجع ثبت شد</tg-spoiler>"
    elif change > 0:
        direction = "⬆️"
        percent = f"🟢 +{change:.2f}%"
        comment = "🔴 <tg-spoiler>بگا رفتین</tg-spoiler>"
    elif change < 0:
        direction = "⬇️"
        percent = f"🔴 {change:.2f}%"
        comment = "🟢 <tg-spoiler>فکر کنم رفتن</tg-spoiler>"
    else:
        direction = "➡️"
        percent = "⚪ 0.00%"
        comment = "⚪ <tg-spoiler>بدون تغییر</tg-spoiler>"

    return (
        f"{direction} <b>{title}</b> 🪙\n\n"
        f"💰 <b>{price:,} تومان</b>\n"
        f"📊 تغییر نسبت به پیام قبلی: {percent}\n"
        f"{comment}\n\n"
        f"🕐 {date_text} | {time_text}"
    )


def default_state() -> Dict[str, Any]:
    return {"usd": None, "gold": None, "updated_at": None}


def load_state() -> Dict[str, Any]:
    raw = load_json(STATE_FILE, {})
    return {
        "usd": raw.get("usd"),
        "gold": raw.get("gold"),
        "updated_at": raw.get("updated_at"),
    }


def default_schedule() -> Dict[str, Any]:
    return {"enabled": True, "interval_minutes": 15, "last_sent_at": None}


def load_schedule() -> Dict[str, Any]:
    raw = load_json(SCHEDULE_FILE, default_schedule())
    try:
        interval = int(raw.get("interval_minutes", 15))
    except (TypeError, ValueError):
        interval = 15
    if interval not in INTERVALS:
        interval = 15
    return {
        "enabled": bool(raw.get("enabled", True)),
        "interval_minutes": interval,
        "last_sent_at": raw.get("last_sent_at"),
    }


def save_schedule(schedule: Dict[str, Any]) -> None:
    save_json(SCHEDULE_FILE, schedule)


def send_prices() -> bool:
    state = load_state()
    sent_any = False

    assets = [
        ("usd", "دلار آزاد"),
        ("gold", "طلای ۱۸ عیار"),
    ]

    for kind, title in assets:
        try:
            current = get_market_price(kind)
            previous = state.get(kind)

            if suspicious_change(previous, current):
                print(
                    f"SKIP {kind}: suspicious jump "
                    f"previous={previous} current={current}"
                )
                continue

            change = calculate_change(previous, current)
            text = build_price_message(title, current, change)
            message = send_message(text)

            try:
                pin_message(int(message["message_id"]))
            except Exception as pin_error:
                # The message is still a valid sent price; don't discard it
                # just because a pin failed temporarily.
                print(f"PIN WARNING {kind}: {pin_error}")

            state[kind] = current
            sent_any = True

            print(
                f"SENT {kind}: current={current} previous={previous} "
                f"change={change}"
            )
        except Exception as error:
            print(f"PRICE ERROR {kind}: {error}")

    if sent_any:
        state["updated_at"] = now_tehran().isoformat()
        save_json(STATE_FILE, state)

    return sent_any


def schedule_due(schedule: Dict[str, Any]) -> bool:
    if not schedule["enabled"]:
        return False
    if not schedule["last_sent_at"]:
        return True

    try:
        last = datetime.fromisoformat(schedule["last_sent_at"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=TEHRAN)
        elapsed = (
            datetime.now(timezone.utc) - last.astimezone(timezone.utc)
        ).total_seconds()
        return elapsed >= schedule["interval_minutes"] * 60
    except (TypeError, ValueError):
        return True


def mark_sent(schedule: Dict[str, Any]) -> None:
    schedule["last_sent_at"] = now_tehran().isoformat()
    save_schedule(schedule)


def admin_keyboard() -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "📤 ارسال فوری", "callback_data": "send_now"},
                {"text": "📊 وضعیت", "callback_data": "status"},
            ],
            [
                {"text": "⏱ تنظیم فاصله", "callback_data": "interval"},
            ],
            [
                {"text": "⏸ توقف", "callback_data": "pause"},
                {"text": "▶️ ادامه", "callback_data": "resume"},
            ],
        ]
    }


def interval_keyboard() -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "15m", "callback_data": "int_15"},
                {"text": "30m", "callback_data": "int_30"},
                {"text": "1h", "callback_data": "int_60"},
                {"text": "2h", "callback_data": "int_120"},
            ],
            [
                {"text": "4h", "callback_data": "int_240"},
                {"text": "6h", "callback_data": "int_360"},
                {"text": "12h", "callback_data": "int_720"},
                {"text": "24h", "callback_data": "int_1440"},
            ],
            [{"text": "🔙 برگشت", "callback_data": "admin"}],
        ]
    }


def status_text() -> str:
    schedule = load_schedule()
    state = load_state()

    enabled = "🟢 فعال" if schedule["enabled"] else "🔴 متوقف"
    interval = INTERVALS[schedule["interval_minutes"]]
    last = schedule["last_sent_at"] or "هنوز ارسال نشده"
    usd = f"{state['usd']:,} تومان" if state.get("usd") else "ثبت نشده"
    gold = f"{state['gold']:,} تومان" if state.get("gold") else "ثبت نشده"

    return (
        "⚙️ <b>پنل مدیریت ربات</b>\n\n"
        f"وضعیت: {enabled}\n"
        f"فاصله ارسال: {interval}\n"
        f"آخرین ارسال: {last}\n\n"
        f"💵 دلار: {usd}\n"
        f"🪙 طلا: {gold}"
    )


def edit_panel(chat_id: int, message_id: int, text: str, keyboard: Dict[str, Any]) -> None:
    telegram(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
        },
    )


def answer_callback(callback_id: str, text: str = "") -> None:
    try:
        telegram(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text},
        )
    except Exception as error:
        print(f"CALLBACK ANSWER WARNING: {error}")


def handle_callback(query: Dict[str, Any]) -> None:
    data = query.get("data", "")
    answer_callback(query["id"])

    message = query.get("message") or {}
    chat = message.get("chat") or {}

    if str(chat.get("id")) != ADMIN_ID:
        return

    chat_id = int(chat["id"])
    message_id = int(message["message_id"])

    if data == "send_now":
        ok = send_prices()
        schedule = load_schedule()
        if ok:
            mark_sent(schedule)
        text = (
            "✅ ارسال انجام شد و پیام‌های جدید پین شدند.\n\n"
            if ok
            else "❌ دریافت/ارسال قیمت ناموفق بود.\n\n"
        ) + status_text()
        edit_panel(chat_id, message_id, text, admin_keyboard())

    elif data == "status":
        edit_panel(chat_id, message_id, status_text(), admin_keyboard())

    elif data == "interval":
        edit_panel(
            chat_id,
            message_id,
            "⏱ <b>فاصله ارسال را انتخاب کن:</b>",
            interval_keyboard(),
        )

    elif data.startswith("int_"):
        value = int(data.split("_", 1)[1])
        schedule = load_schedule()
        schedule["interval_minutes"] = value
        save_schedule(schedule)
        edit_panel(
            chat_id,
            message_id,
            f"✅ فاصله روی <b>{INTERVALS[value]}</b> تنظیم شد.\n\n{status_text()}",
            admin_keyboard(),
        )

    elif data == "pause":
        schedule = load_schedule()
        schedule["enabled"] = False
        save_schedule(schedule)
        edit_panel(chat_id, message_id, status_text(), admin_keyboard())

    elif data == "resume":
        schedule = load_schedule()
        schedule["enabled"] = True
        save_schedule(schedule)
        edit_panel(chat_id, message_id, status_text(), admin_keyboard())

    elif data == "admin":
        edit_panel(chat_id, message_id, status_text(), admin_keyboard())


def handle_message(message: Dict[str, Any]) -> None:
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()

    if str(sender.get("id")) != ADMIN_ID:
        return

    if text == "/admin" and chat.get("type") == "private":
        telegram(
            "sendMessage",
            {
                "chat_id": chat["id"],
                "text": status_text(),
                "parse_mode": "HTML",
                "reply_markup": admin_keyboard(),
            },
        )


def poll_updates() -> None:
    stored = load_json(OFFSET_FILE, {"offset": 0})
    offset = int(stored.get("offset", 0) or 0)
    deadline = time.time() + POLL_SECONDS

    while time.time() < deadline:
        try:
            remaining = max(1, int(deadline - time.time()))
            timeout = min(20, remaining)
            updates = telegram(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": timeout,
                    "allowed_updates": ["message", "callback_query"],
                },
            )

            for update in updates:
                offset = max(offset, int(update["update_id"]) + 1)
                save_json(OFFSET_FILE, {"offset": offset})

                if "message" in update:
                    handle_message(update["message"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])

        except requests.RequestException as error:
            print(f"POLL NETWORK ERROR: {error}")
            time.sleep(3)
        except Exception as error:
            print(f"POLL ERROR: {error}")
            time.sleep(3)


def main() -> None:
    print("BOT START")
    print(f"FORCE_SEND={FORCE_SEND}")

    # Long polling and webhook mode conflict. Telegram ignores getUpdates
    # while a webhook is active, so remove it at every start.
    try:
        telegram(
            "deleteWebhook",
            {"drop_pending_updates": False},
        )
    except Exception as error:
        print(f"WEBHOOK WARNING: {error}")

    schedule = load_schedule()

    # A manual GitHub Actions run always sends immediately. Scheduled runs
    # respect the selected interval.
    should_send = FORCE_SEND or schedule_due(schedule)

    if should_send:
        if send_prices():
            mark_sent(schedule)

    poll_updates()
    print("BOT END")


if __name__ == "__main__":
    main()
