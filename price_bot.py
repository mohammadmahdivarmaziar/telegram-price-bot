import os
import re
import json
import time
from datetime import datetime, timezone, timedelta

import requests
import jdatetime
from bs4 import BeautifulSoup


# =========================================================
# CONFIG
# =========================================================

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

# هر اجرای GitHub Actions حدود 45 ثانیه Updates را بررسی می‌کند.
POLL_SECONDS = 45


# =========================================================
# TELEGRAM API
# =========================================================

def telegram(method, data=None, timeout=20):
    if not BOT_TOKEN:
        print("BOT_TOKEN is missing")
        return None

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        response = requests.post(
            url,
            data=data or {},
            timeout=timeout
        )

        if not response.ok:
            print("Telegram HTTP error:", response.status_code)
            print(response.text[:500])
            return None

        result = response.json()

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

    if reply_markup is not None:
        data["reply_markup"] = json.dumps(
            reply_markup,
            ensure_ascii=False
        )

    return telegram("sendMessage", data)


def answer_callback(callback_id, text=""):
    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False,
        }
    )


# =========================================================
# JSON FILES
# =========================================================

def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# TIME / SCHEDULE
# =========================================================

def now_tehran():
    return datetime.now(TEHRAN_TZ)


def load_schedule():
    default = {
        "enabled": True,
        "interval_minutes": 240,
        "last_sent_at": None
    }

    data = load_json(SCHEDULE_FILE, default)

    if not isinstance(data, dict):
        data = default.copy()

    data.setdefault("enabled", True)
    data.setdefault("interval_minutes", 240)
    data.setdefault("last_sent_at", None)

    try:
        data["interval_minutes"] = int(
            data["interval_minutes"]
        )
    except Exception:
        data["interval_minutes"] = 240

    if data["interval_minutes"] < 5:
        data["interval_minutes"] = 5

    if data["interval_minutes"] > 10080:
        data["interval_minutes"] = 10080

    return data


def save_schedule(data):
    save_json(SCHEDULE_FILE, data)


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


def should_send_price():
    schedule = load_schedule()

    if not schedule.get("enabled", True):
        return False

    last = parse_datetime(
        schedule.get("last_sent_at")
    )

    if last is None:
        return True

    elapsed = (
        now_tehran() - last
    ).total_seconds()

    interval_seconds = (
        int(schedule["interval_minutes"]) * 60
    )

    return elapsed >= interval_seconds


# =========================================================
# PRICE SCRAPER
# =========================================================

def clean_number(value):
    if value is None:
        return None

    value = str(value)

    value = (
        value
        .replace(",", "")
        .replace("٬", "")
        .replace(" ", "")
        .replace("ریال", "")
        .replace("تومان", "")
    )

    value = value.translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹",
            "0123456789"
        )
    )

    try:
        return float(value)
    except Exception:
        return None


def market_data(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        response.raise_for_status()

        html = response.text

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        text = soup.get_text(
            " ",
            strip=True
        )

        # -------------------------
        # CURRENT PRICE
        # -------------------------

        current = None

        patterns = [
            r'"price"\s*:\s*"?(?:([\d,]+))',
            r'"current"\s*:\s*"?(?:([\d,]+))',
            r'قیمت[^0-9]{0,100}([\d,]{6,})',
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                html,
                re.IGNORECASE
            )

            if match:
                current = clean_number(
                    match.group(1)
                )

                if current:
                    break

        # Fallback
        if current is None:
            numbers = re.findall(
                r'(?<!\d)(\d{6,12})(?!\d)',
                text
            )

            candidates = []

            for number in numbers:
                try:
                    value = int(number)

                    if value >= 100000:
                        candidates.append(value)

                except Exception:
                    pass

            if candidates:
                current = float(
                    candidates[0]
                )

        # -------------------------
        # DAILY CHANGE
        # -------------------------

        daily_change = None

        patterns = [
            r'تغییر روزانه.{0,150}?([+-]?\d+(?:\.\d+)?)\s*%',
            r'روزانه.{0,150}?([+-]?\d+(?:\.\d+)?)\s*%',
            r'([+-]\d+(?:\.\d+)?)\s*%',
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                re.IGNORECASE
            )

            if match:
                try:
                    daily_change = float(
                        match.group(1)
                    )
                    break
                except Exception:
                    pass

        if current is None:
            print("Price not found:", url)
            return None, None

        # TGJU = Rial
        # Telegram message = Toman
        current_toman = current / 10

        return current_toman, daily_change

    except Exception as e:
        print("market_data error:", e)
        return None, None


# =========================================================
# MESSAGE FORMAT
# =========================================================

def format_price(value):
    if value is None:
        return "نامشخص"

    return f"{int(round(value)):,}"


def format_change(change):
    if change is None:
        return "⚪ نامشخص"

    if change > 0:
        return f"🟢+{change:.2f}%"

    if change < 0:
        return f"🔴{change:.2f}%"

    return "⚪0.00%"


def make_price_message(
    title,
    emoji,
    value,
    change
):
    if change is not None and change < 0:
        direction = "⬇️"
        phrase = (
            "🟢 "
            "<tg-spoiler>فکر کنم رفتن</tg-spoiler>"
        )
    else:
        direction = "⬆️"
        phrase = (
            "🔴 "
            "<tg-spoiler>بگا رفتین</tg-spoiler>"
        )

    now = now_tehran()

    jalali = jdatetime.datetime.fromgregorian(
        datetime=now.replace(tzinfo=None)
    )

    date_text = jalali.strftime(
        "%Y/%m/%d"
    )

    time_text = now.strftime(
        "%H:%M:%S"
    )

    return (
        f"{direction} {title} {emoji}\n\n"
        f"💰 <b>{format_price(value)} تومان</b>\n"
        f"📊 تغییر نسبت به دیروز: "
        f"<b>{format_change(change)}</b>\n"
        f"{phrase}\n\n"
        f"🕐 {date_text} | {time_text}"
    )


# =========================================================
# SEND PRICES
# =========================================================

def send_prices():
    print("Getting prices...")

    usd, usd_change = market_data(
        USD_URL
    )

    gold, gold_change = market_data(
        GOLD_URL
    )

    if usd is None and gold is None:
        print("Both prices failed.")
        return False

    sent_any = False

    if usd is not None:
        message = make_price_message(
            "دلار آزاد",
            "💵",
            usd,
            usd_change
        )

        result = send_message(message)

        if result:
            sent_any = True

    if gold is not None:
        message = make_price_message(
            "طلای ۱۸ عیار",
            "🪙",
            gold,
            gold_change
        )

        result = send_message(message)

        if result:
            sent_any = True

    if not sent_any:
        print("Telegram send failed.")
        return False

    # ذخیره تاریخ آخرین ارسال فقط وقتی
    # حداقل یک پیام با موفقیت ارسال شده.
    schedule = load_schedule()

    schedule["last_sent_at"] = (
        now_tehran().isoformat()
    )

    save_schedule(schedule)

    # History
    history = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(history, list):
        history = []

    history.append(
        {
            "updated_at": now_tehran().isoformat(),
            "usd": usd,
            "usd_change": usd_change,
            "gold": gold,
            "gold_change": gold_change
        }
    )

    history = history[-100:]

    save_json(
        HISTORY_FILE,
        history
    )

    print("Prices sent successfully.")

    return True


# =========================================================
# ADMIN KEYBOARDS
# =========================================================

def admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "⏱ تغییر زمان",
                    "callback_data": "set_time"
                },
                {
                    "text": "▶️ ارسال فوری",
                    "callback_data": "send_now"
                }
            ],
            [
                {
                    "text": "⏸ توقف",
                    "callback_data": "pause"
                },
                {
                    "text": "▶️ فعال‌سازی",
                    "callback_data": "resume"
                }
            ],
            [
                {
                    "text": "📊 وضعیت",
                    "callback_data": "status"
                }
            ]
        ]
    }


def time_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "۱۵ دقیقه",
                    "callback_data": "time_15"
                },
                {
                    "text": "۳۰ دقیقه",
                    "callback_data": "time_30"
                }
            ],
            [
                {
                    "text": "۱ ساعت",
                    "callback_data": "time_60"
                },
                {
                    "text": "۲ ساعت",
                    "callback_data": "time_120"
                }
            ],
            [
                {
                    "text": "۴ ساعت",
                    "callback_data": "time_240"
                },
                {
                    "text": "۶ ساعت",
                    "callback_data": "time_360"
                }
            ],
            [
                {
                    "text": "۱۲ ساعت",
                    "callback_data": "time_720"
                },
                {
                    "text": "۲۴ ساعت",
                    "callback_data": "time_1440"
                }
            ]
        ]
    }


# =========================================================
# ADMIN
# =========================================================

def is_admin(user_id):
    return str(user_id) in ADMIN_IDS


def interval_label(minutes):
    minutes = int(minutes)

    if minutes < 60:
        return f"{minutes} دقیقه"

    if minutes % 60 == 0:
        return f"{minutes // 60} ساعت"

    return f"{minutes} دقیقه"


def show_admin_panel(chat_id):
    schedule = load_schedule()

    enabled = (
        "فعال ✅"
        if schedule["enabled"]
        else "متوقف ⏸"
    )

    text = (
        "🛠 <b>پنل مدیریت ربات</b>\n\n"
        f"وضعیت: <b>{enabled}</b>\n"
        f"فاصله ارسال: "
        f"<b>{interval_label(schedule['interval_minutes'])}</b>\n\n"
        "دستور یا دکمه موردنظر را انتخاب کن:"
    )

    send_message(
        text,
        chat_id=chat_id,
        reply_markup=admin_keyboard()
    )


def show_status(chat_id):
    schedule = load_schedule()

    enabled = (
        "فعال ✅"
        if schedule["enabled"]
        else "متوقف ⏸"
    )

    last = schedule.get(
        "last_sent_at"
    )

    if last:
        dt = parse_datetime(last)

        if dt:
            last_text = dt.strftime(
                "%Y/%m/%d %H:%M:%S"
            )
        else:
            last_text = str(last)
    else:
        last_text = "هنوز ارسال نشده"

    text = (
        "📊 <b>وضعیت ربات</b>\n\n"
        f"وضعیت: <b>{enabled}</b>\n"
        f"فاصله ارسال: "
        f"<b>{interval_label(schedule['interval_minutes'])}</b>\n"
        f"آخرین ارسال: <b>{last_text}</b>"
    )

    send_message(
        text,
        chat_id=chat_id
    )


def set_interval(minutes, chat_id):
    try:
        minutes = int(minutes)
    except Exception:
        return

    if minutes < 5:
        minutes = 5

    if minutes > 10080:
        minutes = 10080

    schedule = load_schedule()

    schedule["interval_minutes"] = minutes
    schedule["enabled"] = True

    save_schedule(schedule)

    send_message(
        "✅ فاصله ارسال روی "
        f"<b>{interval_label(minutes)}</b> "
        "تنظیم شد.",
        chat_id=chat_id
    )


# =========================================================
# COMMAND HANDLER
# =========================================================

def handle_message(message):
    chat = message.get(
        "chat",
        {}
    )

    user = message.get(
        "from",
        {}
    )

    chat_id = chat.get("id")
    user_id = user.get("id")

    if not chat_id or not user_id:
        return

    if not is_admin(user_id):
        print(
            "Unauthorized user:",
            user_id
        )
        return

    text = (
        message.get("text") or ""
    ).strip()

    if not text:
        return

    command = (
        text.split()[0]
        .lower()
        .split("@")[0]
    )

    if command == "/admin":
        show_admin_panel(chat_id)
        return

    if command == "/status":
        show_status(chat_id)
        return

    if command == "/send":
        ok = send_prices()

        send_message(
            "✅ ارسال انجام شد."
            if ok
            else "❌ ارسال قیمت ناموفق بود.",
            chat_id=chat_id
        )
        return

    if command == "/pause":
        schedule = load_schedule()

        schedule["enabled"] = False

        save_schedule(schedule)

        send_message(
            "⏸ ارسال خودکار متوقف شد.",
            chat_id=chat_id
        )
        return

    if command == "/resume":
        schedule = load_schedule()

        schedule["enabled"] = True

        save_schedule(schedule)

        send_message(
            "▶️ ارسال خودکار فعال شد.",
            chat_id=chat_id
        )
        return

    if command == "/settime":
        parts = text.split()

        if len(parts) != 2:
            send_message(
                "❌ مثال:\n"
                "<code>/settime 240</code>\n\n"
                "240 یعنی ۴ ساعت.",
                chat_id=chat_id
            )
            return

        try:
            minutes = int(parts[1])
        except Exception:
            send_message(
                "❌ عدد واردشده صحیح نیست.",
                chat_id=chat_id
            )
            return

        set_interval(
            minutes,
            chat_id
        )


# =========================================================
# CALLBACK HANDLER
# =========================================================

def handle_callback(callback):
    callback_id = callback.get("id")

    user = callback.get(
        "from",
        {}
    )

    user_id = user.get("id")

    message = callback.get(
        "message",
        {}
    )

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get("id")

    if not is_admin(user_id):
        answer_callback(
            callback_id,
            "⛔ دسترسی ندارید."
        )
        return

    answer_callback(
        callback_id,
        "در حال انجام..."
    )

    data = callback.get(
        "data",
        ""
    )

    if data == "set_time":
        send_message(
            "⏱ <b>فاصله ارسال را انتخاب کن:</b>",
            chat_id=chat_id,
            reply_markup=time_keyboard()
        )
        return

    if data == "send_now":
        ok = send_prices()

        send_message(
            "✅ قیمت‌ها ارسال شدند."
            if ok
            else "❌ ارسال قیمت ناموفق بود.",
            chat_id=chat_id
        )
        return

    if data == "pause":
        schedule = load_schedule()

        schedule["enabled"] = False

        save_schedule(schedule)

        send_message(
            "⏸ ارسال خودکار متوقف شد.",
            chat_id=chat_id
        )
        return

    if data == "resume":
        schedule = load_schedule()

        schedule["enabled"] = True

        save_schedule(schedule)

        send_message(
            "▶️ ارسال خودکار فعال شد.",
            chat_id=chat_id
        )
        return

    if data == "status":
        show_status(chat_id)
        return

    if data.startswith("time_"):
        try:
            minutes = int(
                data.replace(
                    "time_",
                    ""
                )
            )

            set_interval(
                minutes,
                chat_id
            )

        except Exception as e:
            print(
                "Callback time error:",
                e
            )


# =========================================================
# TELEGRAM UPDATE OFFSET
# =========================================================

def load_offset():
    data = load_json(
        OFFSET_FILE,
        {"offset": 0}
    )

    try:
        return int(
            data.get(
                "offset",
                0
            )
        )
    except Exception:
        return 0


def save_offset(offset):
    save_json(
        OFFSET_FILE,
        {
            "offset": int(offset)
        }
    )


def get_updates(offset):
    result = telegram(
        "getUpdates",
        {
            "offset": offset,
            "timeout": 5,
            "allowed_updates": json.dumps(
                [
                    "message",
                    "callback_query"
                ]
            )
        },
        timeout=12
    )

    return result or []


# =========================================================
# POLLING
# =========================================================

def poll_updates(seconds):
    print(
        f"Polling Telegram for {seconds} seconds..."
    )

    offset = load_offset()

    started = time.time()

    while time.time() - started < seconds:
        updates = get_updates(offset)

        if updates:
            print(
                f"Received {len(updates)} update(s)."
            )

        for update in updates:
            update_id = update.get(
                "update_id"
            )

            if update_id is not None:
                offset = max(
                    offset,
                    int(update_id) + 1
                )

            try:
                if "message" in update:
                    handle_message(
                        update["message"]
                    )

                elif "callback_query" in update:
                    handle_callback(
                        update["callback_query"]
                    )

            except Exception as e:
                print(
                    "Update handler error:",
                    e
                )

        if updates:
            save_offset(offset)

        time.sleep(1)


# =========================================================
# MAIN
# =========================================================

def main():
    print("=" * 50)
    print("Telegram Price Bot")
    print("=" * 50)

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN missing")
        return

    if not CHAT_ID:
        print("ERROR: CHAT_ID missing")
        return

    if not ADMIN_IDS:
        print(
            "WARNING: ADMIN_ID missing"
        )

    # جلوگیری از گیر کردن getUpdates
    telegram(
        "deleteWebhook",
        timeout=10
    )

    # -----------------------------------------------------
    # اول قیمت را بررسی می‌کنیم
    # -----------------------------------------------------

    if should_send_price():
        print(
            "Automatic price sending is due."
        )

        send_prices()

    else:
        schedule = load_schedule()

        print(
            "Automatic sending is not due."
        )

        print(
            "Interval:",
            schedule["interval_minutes"],
            "minutes"
        )

        print(
            "Last:",
            schedule.get("last_sent_at")
        )

    # -----------------------------------------------------
    # بعد حدود ۴۵ ثانیه پنل/دکمه‌ها را گوش می‌کنیم
    # -----------------------------------------------------

    poll_updates(
        POLL_SECONDS
    )

    print(
        "Workflow polling finished."
    )


if __name__ == "__main__":
    main()
