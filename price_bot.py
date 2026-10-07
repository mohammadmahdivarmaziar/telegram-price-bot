import os
import re
import json
import time
from datetime import datetime, timezone, timedelta

import requests
import jdatetime
from bs4 import BeautifulSoup


BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ADMIN_ID = str(os.environ["ADMIN_ID"]).strip()

USD_URL = "https://gem.tgju.org/profile/price_dollar_rl"
GOLD_URL = "https://gem.tgju.org/profile/geram18"

SCHEDULE_FILE = "schedule.json"
STATE_FILE = "price_state.json"
OFFSET_FILE = "telegram_offset.json"

TEHRAN = timezone(timedelta(hours=3, minutes=30))
POLL_SECONDS = 60

ALLOWED_INTERVALS = {
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
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
}


# =========================
# TELEGRAM
# =========================

def tg(method, payload=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    response = requests.post(
        url,
        json=payload or {},
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data}")

    return data.get("result")


def send_message(text):
    return tg(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


def pin_message(message_id):
    return tg(
        "pinChatMessage",
        {
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "disable_notification": True,
        },
    )


# =========================
# FILES / STATE
# =========================

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    temp_path = path + ".tmp"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(temp_path, path)


def load_state():
    state = load_json(STATE_FILE, {})

    return {
        "usd": state.get("usd"),
        "gold": state.get("gold"),
        "updated_at": state.get("updated_at"),
    }


def load_schedule():
    schedule = load_json(SCHEDULE_FILE, {})

    interval = int(
        schedule.get(
            "interval_minutes",
            15,
        )
    )

    if interval not in ALLOWED_INTERVALS:
        interval = 15

    return {
        "enabled": bool(
            schedule.get(
                "enabled",
                True,
            )
        ),
        "interval_minutes": interval,
        "last_sent_at": schedule.get(
            "last_sent_at"
        ),
    }


def save_schedule(schedule):
    save_json(
        SCHEDULE_FILE,
        schedule,
    )


# =========================
# TIME
# =========================

def now_tehran():
    return datetime.now(
        timezone.utc
    ).astimezone(TEHRAN)


def jalali_datetime():
    return jdatetime.datetime.fromgregorian(
        datetime=now_tehran().replace(
            tzinfo=None
        )
    )


# =========================
# NUMBER HELPERS
# =========================

def normalize_digits(value):
    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    )

    return str(value).translate(table)


def clean_number(value):
    value = normalize_digits(value)

    value = (
        value
        .replace(",", "")
        .replace("٬", "")
        .replace(" ", "")
        .replace("\u200c", "")
    )

    match = re.search(
        r"\d+(?:\.\d+)?",
        value,
    )

    if not match:
        return None

    try:
        return float(match.group())
    except Exception:
        return None


def valid_rial(value, kind):
    if value is None:
        return False

    if kind == "usd":
        return 500_000 <= value <= 20_000_000

    return 50_000_000 <= value <= 2_000_000_000


# =========================
# TGJU PRICE EXTRACTION
# =========================

def extract_price(html, kind):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    selectors = [
        '[data-field="price"]',
        '[data-field="current"]',
        '[data-field="last"]',
        '[data-field="value"]',
        ".price",
        ".price-value",
        ".current-price",
        ".profile-price",
        ".info .value",
    ]

    # Try common TGJU price elements first.
    for selector in selectors:
        for node in soup.select(selector):
            value = clean_number(
                node.get_text(
                    " ",
                    strip=True,
                )
            )

            if valid_rial(value, kind):
                return int(round(value))

    # Try visible text around current-price labels.
    text = soup.get_text(
        " ",
        strip=True,
    )

    text = normalize_digits(text)

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    patterns = [
        r"نرخ\s*فعلی.{0,100}?([0-9][0-9,\s٬]{5,})",
        r"قیمت\s*فعلی.{0,100}?([0-9][0-9,\s٬]{5,})",
        r"آخرین\s*قیمت.{0,100}?([0-9][0-9,\s٬]{5,})",
    ]

    for pattern in patterns:
        matches = re.finditer(
            pattern,
            text,
            re.I,
        )

        for match in matches:
            value = clean_number(
                match.group(1)
            )

            if valid_rial(value, kind):
                return int(round(value))

    # Try JSON-like values inside page source.
    raw = normalize_digits(html)

    patterns = [
        r'"(?:current|price|last|value)"\s*:\s*"?'
        r"(?:[^\d]*)([\d,٬]{6,})",

        r"'(?:current|price|last|value)'\s*:\s*'?"
        r"(?:[^\d]*)([\d,٬]{6,})",
    ]

    for pattern in patterns:
        matches = re.finditer(
            pattern,
            raw,
            re.I,
        )

        for match in matches:
            value = clean_number(
                match.group(1)
            )

            if valid_rial(value, kind):
                return int(round(value))

    raise ValueError(
        f"Could not extract {kind} price from TGJU"
    )


def get_price(kind):
    if kind == "usd":
        url = USD_URL
    else:
        url = GOLD_URL

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    rial_price = extract_price(
        response.text,
        kind,
    )

    # TGJU gives Rial.
    # We need Toman.
    return rial_price // 10


# =========================
# PRICE CALCULATION
# =========================

def calculate_change(previous, current):
    if previous is None:
        return None

    if previous <= 0:
        return None

    return (
        (current - previous)
        / previous
        * 100
    )


# =========================
# PRICE MESSAGE
# =========================

def price_message(
    title,
    price,
    change,
):
    j = jalali_datetime()

    if change is None:
        direction = "➡️"
        percent = "⚪ 0.00%"
        joke = (
            "⚪ "
            "<tg-spoiler>"
            "قیمت مرجع ثبت شد"
            "</tg-spoiler>"
        )

    elif change > 0:
        direction = "⬆️"
        percent = f"🟢 +{change:.2f}%"
        joke = (
            "🔴 "
            "<tg-spoiler>"
            "بگا رفتین"
            "</tg-spoiler>"
        )

    elif change < 0:
        direction = "⬇️"
        percent = f"🔴 {change:.2f}%"
        joke = (
            "🟢 "
            "<tg-spoiler>"
            "فکر کنم رفتن"
            "</tg-spoiler>"
        )

    else:
        direction = "➡️"
        percent = "⚪ 0.00%"
        joke = (
            "⚪ "
            "<tg-spoiler>"
            "بدون تغییر"
            "</tg-spoiler>"
        )

    return (
        f"{direction} "
        f"<b>{title}</b> 🪙\n\n"

        f"💰 "
        f"<b>{price:,} تومان</b>\n"

        f"📊 "
        f"تغییر نسبت به پیام قبلی: "
        f"{percent}\n"

        f"{joke}\n\n"

        f"🕐 "
        f"{j.strftime('%Y/%m/%d')} "
        f"| "
        f"{j.strftime('%H:%M:%S')}"
    )


# =========================
# SEND PRICES
# =========================

def send_prices():
    state = load_state()

    sent_any = False

    assets = [
        (
            "usd",
            "دلار آزاد 💵",
        ),
        (
            "gold",
            "طلای ۱۸ عیار",
        ),
    ]

    for kind, title in assets:

        try:
            current = get_price(kind)

            previous = state.get(kind)

            change = calculate_change(
                previous,
                current,
            )

            # Safety check:
            # If scraper suddenly returns a wildly
            # different number, don't send it.
            if (
                previous
                and abs(current - previous)
                / previous
                > 0.20
            ):
                print(
                    f"{kind}: suspicious value "
                    f"old={previous} "
                    f"new={current}"
                )

                continue

            text = price_message(
                title,
                current,
                change,
            )

            message = send_message(text)

            pin_message(
                message["message_id"]
            )

            # VERY IMPORTANT:
            # Save only the price that was actually sent.
            state[kind] = current

            sent_any = True

            print(
                f"{kind}: sent={current} "
                f"change={change}"
            )

        except Exception as error:
            print(
                f"{kind}: ERROR: {error}"
            )

    if sent_any:
        state["updated_at"] = (
            now_tehran().isoformat()
        )

        save_json(
            STATE_FILE,
            state,
        )

    return sent_any


# =========================
# AUTOMATIC SCHEDULE
# =========================

def schedule_due(schedule):
    if not schedule["enabled"]:
        return False

    if not schedule["last_sent_at"]:
        return True

    try:
        last = datetime.fromisoformat(
            schedule["last_sent_at"]
        )

        if last.tzinfo is None:
            last = last.replace(
                tzinfo=TEHRAN
            )

        elapsed = (
            datetime.now(timezone.utc)
            - last.astimezone(timezone.utc)
        ).total_seconds()

        return (
            elapsed
            >= schedule["interval_minutes"] * 60
        )

    except Exception:
        return True


def mark_sent(schedule):
    schedule["last_sent_at"] = (
        now_tehran().isoformat()
    )

    save_schedule(schedule)


# =========================
# ADMIN KEYBOARDS
# =========================

def keyboard_main():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📤 ارسال فوری",
                    "callback_data": "send_now",
                },
                {
                    "text": "📊 وضعیت",
                    "callback_data": "status",
                },
            ],
            [
                {
                    "text": "⏱ تنظیم فاصله",
                    "callback_data": "interval",
                },
            ],
            [
                {
                    "text": "⏸ توقف",
                    "callback_data": "pause",
                },
                {
                    "text": "▶️ ادامه",
                    "callback_data": "resume",
                },
            ],
        ]
    }


def keyboard_intervals():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "15m",
                    "callback_data": "int_15",
                },
                {
                    "text": "30m",
                    "callback_data": "int_30",
                },
                {
                    "text": "1h",
                    "callback_data": "int_60",
                },
                {
                    "text": "2h",
                    "callback_data": "int_120",
                },
            ],
            [
                {
                    "text": "4h",
                    "callback_data": "int_240",
                },
                {
                    "text": "6h",
                    "callback_data": "int_360",
                },
                {
                    "text": "12h",
                    "callback_data": "int_720",
                },
                {
                    "text": "24h",
                    "callback_data": "int_1440",
                },
            ],
            [
                {
                    "text": "🔙 برگشت",
                    "callback_data": "admin",
                }
            ],
        ]
    }


# =========================
# ADMIN STATUS
# =========================

def status_text():
    schedule = load_schedule()
    state = load_state()

    status = (
        "🟢 فعال"
        if schedule["enabled"]
        else "🔴 متوقف"
    )

    interval = ALLOWED_INTERVALS[
        schedule["interval_minutes"]
    ]

    if state.get("usd"):
        usd = f"{state['usd']:,} تومان"
    else:
        usd = "ثبت نشده"

    if state.get("gold"):
        gold = f"{state['gold']:,} تومان"
    else:
        gold = "ثبت نشده"

    last = (
        schedule.get("last_sent_at")
        or "هنوز ارسال نشده"
    )

    return (
        "⚙️ <b>پنل مدیریت ربات</b>\n\n"
        f"وضعیت: {status}\n"
        f"فاصله ارسال: {interval}\n"
        f"آخرین ارسال: {last}\n\n"
        f"💵 دلار: {usd}\n"
        f"🪙 طلا: {gold}"
    )


def edit_panel(
    chat_id,
    message_id,
    text,
    keyboard,
):
    return tg(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
        },
    )


def answer_callback(
    callback_id,
    text="",
):
    try:
        tg(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text,
            },
        )
    except Exception as error:
        print(
            "callback answer error:",
            error,
        )


# =========================
# CALLBACK HANDLER
# =========================

def handle_callback(query):
    data = query.get(
        "data",
        "",
    )

    answer_callback(
        query["id"]
    )

    message = (
        query.get("message")
        or {}
    )

    chat = (
        message.get("chat")
        or {}
    )

    # Only ADMIN_ID.
    if str(chat.get("id")) != ADMIN_ID:
        return

    if data == "send_now":

        ok = send_prices()

        schedule = load_schedule()

        if ok:
            mark_sent(schedule)

        if ok:
            text = (
                "✅ ارسال شد و "
                "پیام‌های جدید پین شدند.\n\n"
                + status_text()
            )
        else:
            text = (
                "❌ دریافت یا ارسال قیمت "
                "ناموفق بود.\n\n"
                + status_text()
            )

        edit_panel(
            chat["id"],
            message["message_id"],
            text,
            keyboard_main(),
        )

    elif data == "status":

        edit_panel(
            chat["id"],
            message["message_id"],
            status_text(),
            keyboard_main(),
        )

    elif data == "interval":

        edit_panel(
            chat["id"],
            message["message_id"],
            "⏱ "
            "<b>فاصله ارسال را انتخاب کن:</b>",
            keyboard_intervals(),
        )

    elif data.startswith("int_"):

        value = int(
            data.split(
                "_",
                1,
            )[1]
        )

        schedule = load_schedule()

        schedule["interval_minutes"] = value

        save_schedule(schedule)

        edit_panel(
            chat["id"],
            message["message_id"],
            (
                f"✅ فاصله روی "
                f"<b>{ALLOWED_INTERVALS[value]}</b> "
                f"تنظیم شد.\n\n"
                + status_text()
            ),
            keyboard_main(),
        )

    elif data == "pause":

        schedule = load_schedule()

        schedule["enabled"] = False

        save_schedule(schedule)

        edit_panel(
            chat["id"],
            message["message_id"],
            status_text(),
            keyboard_main(),
        )

    elif data == "resume":

        schedule = load_schedule()

        schedule["enabled"] = True

        save_schedule(schedule)

        edit_panel(
            chat["id"],
            message["message_id"],
            status_text(),
            keyboard_main(),
        )

    elif data == "admin":

        edit_panel(
            chat["id"],
            message["message_id"],
            status_text(),
            keyboard_main(),
        )


# =========================
# MESSAGE HANDLER
# =========================

def handle_message(message):

    user = (
        message.get("from")
        or {}
    )

    if str(user.get("id")) != ADMIN_ID:
        return

    text = (
        message.get("text")
        or ""
    ).strip()

    chat = (
        message.get("chat")
        or {}
    )

    if (
        text == "/admin"
        and chat.get("type") == "private"
    ):

        tg(
            "sendMessage",
            {
                "chat_id": chat["id"],
                "text": status_text(),
                "parse_mode": "HTML",
                "reply_markup": keyboard_main(),
            },
        )


# =========================
# TELEGRAM POLLING
# =========================

def poll_updates():

    offset_data = load_json(
        OFFSET_FILE,
        {
            "offset": 0
        },
    )

    offset = offset_data.get(
        "offset",
        0,
    )

    deadline = (
        time.time()
        + POLL_SECONDS
    )

    while time.time() < deadline:

        try:

            remaining = int(
                deadline
                - time.time()
            )

            timeout = min(
                10,
                max(1, remaining),
            )

            updates = tg(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": timeout,
                    "allowed_updates": [
                        "message",
                        "callback_query",
                    ],
                },
            )

            for update in updates:

                offset = max(
                    offset,
                    update["update_id"] + 1,
                )

                save_json(
                    OFFSET_FILE,
                    {
                        "offset": offset
                    },
                )

                if "message" in update:

                    handle_message(
                        update["message"]
                    )

                elif "callback_query" in update:

                    handle_callback(
                        update["callback_query"]
                    )

        except Exception as error:

            print(
                "poll error:",
                error,
            )

            time.sleep(2)


# =========================
# MAIN
# =========================

def main():

    print("BOT START")

    try:

        tg(
            "deleteWebhook",
            {
                "drop_pending_updates": False
            },
        )

    except Exception as error:

        print(
            "deleteWebhook error:",
            error,
        )

    schedule = load_schedule()

    # Automatic sending.
    if schedule_due(schedule):

        if send_prices():

            mark_sent(schedule)

    # Keep admin panel alive for 60 seconds.
    poll_updates()

    print("BOT END")


if __name__ == "__main__":
    main()
