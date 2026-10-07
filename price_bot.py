import os
import re
import json
import time
from datetime import datetime, timezone, timedelta

import requests
import jdatetime
from bs4 import BeautifulSoup


# =========================
# CONFIG
# =========================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

ADMIN_IDS = {
    x.strip()
    for x in os.environ.get("ADMIN_ID", "").split(",")
    if x.strip()
}

USD_URL = "https://gem.tgju.org/profile/price_dollar_rl"
GOLD_URL = "https://gem.tgju.org/profile/geram18"

SCHEDULE_FILE = "schedule.json"
OFFSET_FILE = "telegram_offset.json"
HISTORY_FILE = "history.json"

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130 Safari/537.36"
    )
}


# =========================
# TELEGRAM
# =========================

def telegram(method, data=None, timeout=20):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        r = requests.post(
            url,
            data=data or {},
            timeout=timeout
        )

        if not r.ok:
            print("Telegram HTTP error:", r.status_code, r.text[:500])
            return None

        result = r.json()

        if not result.get("ok"):
            print("Telegram API error:", result)
            return None

        return result.get("result")

    except Exception as e:
        print("Telegram exception:", e)
        return None


def send_message(text, chat_id=None, reply_markup=None):
    target = chat_id or CHAT_ID

    data = {
        "chat_id": target,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

    return telegram("sendMessage", data)


def answer_callback(callback_id, text=""):
    telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False,
        },
    )


# =========================
# FILES
# =========================

def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =========================
# SCHEDULE
# =========================

def load_schedule():
    data = load_json(
        SCHEDULE_FILE,
        {
            "enabled": True,
            "interval_minutes": 240,
            "last_sent_at": None,
        },
    )

    if not isinstance(data, dict):
        data = {}

    data.setdefault("enabled", True)
    data.setdefault("interval_minutes", 240)
    data.setdefault("last_sent_at", None)

    try:
        data["interval_minutes"] = int(data["interval_minutes"])
    except Exception:
        data["interval_minutes"] = 240

    if data["interval_minutes"] < 5:
        data["interval_minutes"] = 5

    return data


def save_schedule(data):
    save_json(SCHEDULE_FILE, data)


def now_tehran():
    return datetime.now(TEHRAN_TZ)


def parse_datetime(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TEHRAN_TZ)

        return dt

    except Exception:
        return None


def should_send():
    schedule = load_schedule()

    if not schedule.get("enabled", True):
        return False

    last = parse_datetime(schedule.get("last_sent_at"))

    if last is None:
        return True

    elapsed = (now_tehran() - last).total_seconds()
    interval = int(schedule.get("interval_minutes", 240)) * 60

    return elapsed >= interval


# =========================
# PRICE DATA
# =========================

def clean_number(value):
    if value is None:
        return None

    value = str(value)

    value = (
        value.replace(",", "")
        .replace("٬", "")
        .replace(" ", "")
        .replace("ریال", "")
        .replace("تومان", "")
    )

    # Persian digits
    trans = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹",
        "0123456789"
    )

    value = value.translate(trans)

    try:
        return float(value)
    except Exception:
        return None


def market_data(url):
    try:
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        r.raise_for_status()

        html = r.text
        soup = BeautifulSoup(html, "html.parser")

        text = soup.get_text(" ", strip=True)

        # -------------------------
        # Current price
        # -------------------------

        current = None

        patterns = [
            r'"price"\s*:\s*"?(?:([\d,]+))',
            r'"current"\s*:\s*"?(?:([\d,]+))',
            r'قیمت[^0-9]{0,80}([\d,]{5,})',
        ]

        for pattern in patterns:
            m = re.search(pattern, html, re.I)

            if m:
                current = clean_number(m.group(1))

                if current:
                    break

        # Fallback:
        # look for large Rial numbers in page text
        if current is None:
            nums = re.findall(r'(?<!\d)(\d{6,12})(?!\d)', text)

            candidates = []

            for n in nums:
                try:
                    x = int(n)

                    if x >= 100000:
                        candidates.append(x)

                except Exception:
                    pass

            if candidates:
                current = float(candidates[0])

        # -------------------------
        # Daily percentage change
        # -------------------------

        daily_change = None

        change_patterns = [
            r'تغییر روزانه.{0,150}?([+-]?\d+(?:\.\d+)?)\s*%',
            r'روزانه.{0,150}?([+-]?\d+(?:\.\d+)?)\s*%',
            r'([+-]\d+(?:\.\d+)?)\s*%',
        ]

        for pattern in change_patterns:
            m = re.search(pattern, text, re.I)

            if m:
                try:
                    daily_change = float(m.group(1))
                    break
                except Exception:
                    pass

        if current is None:
            print("Could not find price:", url)
            return None, None

        # TGJU prices are Rial.
        # Convert to Toman.
        current_toman = current / 10

        return current_toman, daily_change

    except Exception as e:
        print("market_data error:", e)
        return None, None


# =========================
# FORMAT PRICE
# =========================

def toman(value):
    if value is None:
        return "نامشخص"

    return f"{int(round(value)):,}"


def percent_text(change):
    if change is None:
        return "نامشخص"

    if change > 0:
        return f"🟢+{change:.2f}%"

    if change < 0:
        return f"🔴{change:.2f}%"

    return "⚪0.00%"


def make_price_message(
    name,
    emoji,
    value,
    change,
):
    # Direction
    if change is not None and change < 0:
        direction = "⬇️"
        phrase = "🟢 <tg-spoiler>فکر کنم رفتن</tg-spoiler>"
    else:
        direction = "⬆️"
        phrase = "🔴 <tg-spoiler>بگا رفتین</tg-spoiler>"

    now = now_tehran()

    jalali = jdatetime.datetime.fromgregorian(
        datetime=now.replace(tzinfo=None)
    )

    date_text = jalali.strftime("%Y/%m/%d")
    time_text = now.strftime("%H:%M:%S")

    return (
        f"{direction} {name} {emoji}\n\n"
        f"💰 <b>{toman(value)} تومان</b>\n"
        f"📊 تغییر نسبت به دیروز: "
        f"<b>{percent_text(change)}</b>\n"
        f"{phrase}\n\n"
        f"🕐 {date_text} | {time_text}"
    )


# =========================
# SEND PRICES
# =========================

def send_prices(force=False):
    print("Getting market prices...")

    usd, usd_change = market_data(USD_URL)
    gold, gold_change = market_data(GOLD_URL)

    if usd is None and gold is None:
        print("No market data available.")
        return False

    messages = []

    if usd is not None:
        messages.append(
            make_price_message(
                "دلار آزاد",
                "💵",
                usd,
                usd_change,
            )
        )

    if gold is not None:
        messages.append(
            make_price_message(
                "طلای ۱۸ عیار",
                "🪙",
                gold,
                gold_change,
            )
        )

    for message in messages:
        send_message(message)

    # Save history
    history = load_json(HISTORY_FILE, [])

    if not isinstance(history, list):
        history = []

    history.append(
        {
            "updated_at": now_tehran().isoformat(),
            "usd": usd,
            "usd_change": usd_change,
            "gold": gold,
            "gold_change": gold_change,
        }
    )

    # Keep only latest 100 records
    history = history[-100:]

    save_json(HISTORY_FILE, history)

    # Update last sent time
    schedule = load_schedule()
    schedule["last_sent_at"] = now_tehran().isoformat()
    save_schedule(schedule)

    print("Prices sent successfully.")

    return True


# =========================
# ADMIN PANEL
# =========================

def admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "⏱ تغییر زمان",
                    "callback_data": "set_time",
                },
                {
                    "text": "▶️ ارسال فوری",
                    "callback_data": "send_now",
                },
            ],
            [
                {
                    "text": "⏸ توقف",
                    "callback_data": "pause",
                },
                {
                    "text": "▶️ فعال‌سازی",
                    "callback_data": "resume",
                },
            ],
            [
                {
                    "text": "📊 وضعیت",
                    "callback_data": "status",
                }
            ],
        ]
    }


def time_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "۱۵ دقیقه",
                    "callback_data": "time_15",
                },
                {
                    "text": "۳۰ دقیقه",
                    "callback_data": "time_30",
                },
            ],
            [
                {
                    "text": "۱ ساعت",
                    "callback_data": "time_60",
                },
                {
                    "text": "۲ ساعت",
                    "callback_data": "time_120",
                },
            ],
            [
                {
                    "text": "۴ ساعت",
                    "callback_data": "time_240",
                },
                {
                    "text": "۶ ساعت",
                    "callback_data": "time_360",
                },
            ],
            [
                {
                    "text": "۱۲ ساعت",
                    "callback_data": "time_720",
                },
                {
                    "text": "۲۴ ساعت",
                    "callback_data": "time_1440",
                },
            ],
        ]
    }


def is_admin(user_id):
    return str(user_id) in ADMIN_IDS


def admin_panel(chat_id):
    schedule = load_schedule()

    enabled = "فعال ✅" if schedule["enabled"] else "متوقف ⏸"

    interval = schedule["interval_minutes"]

    if interval < 60:
        interval_text = f"{interval} دقیقه"
    elif interval % 60 == 0:
        hours = interval // 60
        interval_text = f"{hours} ساعت"
    else:
        interval_text = f"{interval} دقیقه"

    text = (
        "🛠 <b>پنل مدیریت ربات</b>\n\n"
        f"وضعیت: <b>{enabled}</b>\n"
        f"فاصله ارسال: <b>{interval_text}</b>\n\n"
        "از دکمه‌های زیر استفاده کن:"
    )

    send_message(
        text,
        chat_id=chat_id,
        reply_markup=admin_keyboard(),
    )


def status_message(chat_id):
    schedule = load_schedule()

    enabled = "فعال ✅" if schedule["enabled"] else "متوقف ⏸"

    interval = schedule["interval_minutes"]

    if interval % 60 == 0:
        interval_text = f"{interval // 60} ساعت"
    else:
        interval_text = f"{interval} دقیقه"

    last = schedule.get("last_sent_at")

    if last:
        try:
            dt = parse_datetime(last)
            last_text = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            last_text = str(last)
    else:
        last_text = "هنوز ارسال نشده"

    text = (
        "📊 <b>وضعیت ربات</b>\n\n"
        f"وضعیت: <b>{enabled}</b>\n"
        f"فاصله ارسال: <b>{interval_text}</b>\n"
        f"آخرین ارسال: <b>{last_text}</b>"
    )

    send_message(text, chat_id=chat_id)


def set_interval(minutes, chat_id):
    minutes = int(minutes)

    # Minimum 5 minutes
    if minutes < 5:
        minutes = 5

    # Maximum 7 days
    if minutes > 10080:
        minutes = 10080

    schedule = load_schedule()
    schedule["interval_minutes"] = minutes
    schedule["enabled"] = True

    save_schedule(schedule)

    if minutes % 60 == 0:
        label = f"{minutes // 60} ساعت"
    else:
        label = f"{minutes} دقیقه"

    send_message(
        f"✅ فاصله ارسال روی <b>{label}</b> تنظیم شد.",
        chat_id=chat_id,
    )


# =========================
# TELEGRAM UPDATES
# =========================

def load_offset():
    data = load_json(
        OFFSET_FILE,
        {"offset": 0}
    )

    try:
        return int(data.get("offset", 0))
    except Exception:
        return 0


def save_offset(offset):
    save_json(
        OFFSET_FILE,
        {"offset": int(offset)}
    )


def get_updates():
    offset = load_offset()

    # Make sure webhook doesn't block getUpdates
    telegram("deleteWebhook")

    result = telegram(
        "getUpdates",
        {
            "offset": offset,
            "timeout": 5,
            "allowed_updates": json.dumps(
                ["message", "callback_query"]
            ),
        },
        timeout=15,
    )

    return result or []


def handle_message(message):
    chat = message.get("chat", {})
    user = message.get("from", {})

    chat_id = chat.get("id")
    user_id = user.get("id")

    if not chat_id or not user_id:
        return

    if not is_admin(user_id):
        return

    text = (message.get("text") or "").strip()

    if not text:
        return

    command = text.split()[0].lower()

    # Remove @BotName from command
    command = command.split("@")[0]

    if command == "/admin":
        admin_panel(chat_id)
        return

    if command == "/status":
        status_message(chat_id)
        return

    if command == "/send":
        send_prices(force=True)
        send_message(
            "✅ ارسال فوری انجام شد.",
            chat_id=chat_id,
        )
        return

    if command == "/pause":
        schedule = load_schedule()
        schedule["enabled"] = False
        save_schedule(schedule)

        send_message(
            "⏸ ارسال خودکار متوقف شد.",
            chat_id=chat_id,
        )
        return

    if command == "/resume":
        schedule = load_schedule()
        schedule["enabled"] = True
        save_schedule(schedule)

        send_message(
            "▶️ ارسال خودکار فعال شد.",
            chat_id=chat_id,
        )
        return

    # /settime 240
    if command == "/settime":
        parts = text.split()

        if len(parts) != 2:
            send_message(
                "❌ مثال:\n"
                "<code>/settime 240</code>\n\n"
                "عدد بر حسب دقیقه است.\n"
                "مثلاً 240 یعنی ۴ ساعت.",
                chat_id=chat_id,
            )
            return

        try:
            minutes = int(parts[1])
        except Exception:
            send_message(
                "❌ عدد واردشده صحیح نیست.",
                chat_id=chat_id,
            )
            return

        set_interval(minutes, chat_id)
        return


def handle_callback(callback):
    callback_id = callback.get("id")
    data = callback.get("data", "")

    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    user = callback.get("from") or {}
    user_id = user.get("id")

    if not is_admin(user_id):
        answer_callback(
            callback_id,
            "⛔ دسترسی ندارید."
        )
        return

    answer_callback(callback_id)

    if data == "set_time":
        send_message(
            "⏱ <b>فاصله ارسال را انتخاب کن:</b>",
            chat_id=chat_id,
            reply_markup=time_keyboard(),
        )
        return

    if data == "send_now":
        send_prices(force=True)

        send_message(
            "✅ ارسال فوری انجام شد.",
            chat_id=chat_id,
        )
        return

    if data == "pause":
        schedule = load_schedule()
        schedule["enabled"] = False
        save_schedule(schedule)

        send_message(
            "⏸ ارسال خودکار متوقف شد.",
            chat_id=chat_id,
        )
        return

    if data == "resume":
        schedule = load_schedule()
        schedule["enabled"] = True
        save_schedule(schedule)

        send_message(
            "▶️ ارسال خودکار فعال شد.",
            chat_id=chat_id,
        )
        return

    if data == "status":
        status_message(chat_id)
        return

    if data.startswith("time_"):
        try:
            minutes = int(data.replace("time_", ""))
            set_interval(minutes, chat_id)
        except Exception:
            pass


def process_updates():
    updates = get_updates()

    if not updates:
        return

    print("Updates:", len(updates))

    max_update_id = None

    for update in updates:
        update_id = update.get("update_id")

        if update_id is not None:
            max_update_id = update_id

        try:
            if "message" in update:
                handle_message(update["message"])

            elif "callback_query" in update:
                handle_callback(update["callback_query"])

        except Exception as e:
            print("Update handling error:", e)

    if max_update_id is not None:
        save_offset(max_update_id + 1)


# =========================
# MAIN
# =========================

def main():
    print("================================")
    print("Telegram Price Bot")
    print("================================")

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing.")
        return

    if not CHAT_ID:
        print("ERROR: CHAT_ID is missing.")
        return

    if not ADMIN_IDS:
        print("WARNING: ADMIN_ID is missing.")

    # First process admin commands/buttons
    process_updates()

    # Then check automatic sending
    if should_send():
        print("It is time to send prices.")
        send_prices()
    else:
        schedule = load_schedule()

        print(
            "Not time yet. "
            f"Interval={schedule['interval_minutes']} minutes, "
            f"Last={schedule.get('last_sent_at')}"
        )


if __name__ == "__main__":
    main()
