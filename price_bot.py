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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

ADMIN_IDS = set()

if ADMIN_ID:
    ADMIN_IDS.add(str(ADMIN_ID))

USD_URL = "https://gem.tgju.org/profile/price_dollar_rl"
GOLD_URL = "https://gem.tgju.org/profile/geram18"

SCHEDULE_FILE = "schedule.json"
STATE_FILE = "price_state.json"
OFFSET_FILE = "telegram_offset.json"

# Change this whenever the price parser/state logic is changed.
STATE_VERSION = 2

# GitHub Actions run should stay alive for about one minute.
POLL_SECONDS = 60

# Reject a scraped price if it suddenly differs by more than 20%.
# This is primarily a protection against a broken HTML parser.
PRICE_SANITY_LIMIT = 0.20

# Tehran timezone.
TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# =========================================================
# BASIC VALIDATION
# =========================================================

if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN is not set.")

if not CHAT_ID:
    print("WARNING: CHAT_ID is not set.")

if not ADMIN_IDS:
    print("WARNING: ADMIN_ID is not set.")


# =========================================================
# TELEGRAM API
# =========================================================

TELEGRAM_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
)


def telegram(method, payload=None):
    """
    Generic Telegram Bot API request.
    """

    if not BOT_TOKEN:
        print("Telegram API unavailable: BOT_TOKEN is missing.")
        return None

    url = f"{TELEGRAM_API}/{method}"

    try:
        response = requests.post(
            url,
            json=payload or {},
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            print(
                f"Telegram API error in {method}: "
                f"{data}"
            )
            return None

        return data.get("result")

    except requests.RequestException as exc:
        print(
            f"Telegram request failed "
            f"({method}): {exc}"
        )
        return None

    except Exception as exc:
        print(
            f"Telegram unexpected error "
            f"({method}): {exc}"
        )
        return None


# =========================================================
# TELEGRAM SEND
# =========================================================

def send_message(chat_id, text):
    """
    Send HTML formatted Telegram message.

    Returns message_id on success.
    Returns None on failure.
    """

    result = telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    )

    if not result:
        return None

    message_id = result.get("message_id")

    if not message_id:
        print("Telegram sendMessage returned no message_id.")
        return None

    return message_id


# =========================================================
# TELEGRAM PIN
# =========================================================

def pin_message(chat_id, message_id):
    """
    Pin a Telegram message.
    """

    result = telegram(
        "pinChatMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "disable_notification": True,
        }
    )

    if result is None:
        print(
            f"Failed to pin message {message_id}."
        )
        return False

    print(
        f"Message {message_id} pinned successfully."
    )

    return True


# =========================================================
# JSON HELPERS
# =========================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except Exception as exc:
        print(
            f"Could not load {filename}: {exc}"
        )
        return default


def save_json(filename, data):
    try:
        temporary = f"{filename}.tmp"

        with open(
            temporary,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temporary,
            filename
        )

        return True

    except Exception as exc:
        print(
            f"Could not save {filename}: {exc}"
        )
        return False


# =========================================================
# PRICE STATE
# =========================================================

def load_price_state():
    """
    Load previous successfully sent prices.

    If the state belongs to an older parser version,
    automatically reset it.
    """

    default_state = {
        "version": STATE_VERSION,
        "usd": None,
        "gold": None,
        "updated_at": None,
    }

    try:
        if not os.path.exists(STATE_FILE):
            return default_state

        data = load_json(
            STATE_FILE,
            default_state
        )

        if not isinstance(data, dict):
            print(
                "Invalid price state. "
                "Creating a new baseline."
            )
            return default_state

        if data.get("version") != STATE_VERSION:
            print(
                "Old price state detected. "
                "Resetting price baseline."
            )

            return default_state

        return {
            "version": STATE_VERSION,
            "usd": data.get("usd"),
            "gold": data.get("gold"),
            "updated_at": data.get("updated_at"),
        }

    except Exception as exc:
        print(
            f"Error loading price state: {exc}"
        )
        return default_state


# =========================================================
# SCHEDULE STATE
# =========================================================

DEFAULT_SCHEDULE = {
    "enabled": True,
    "interval_minutes": 15,
    "last_sent_at": None,
}


def load_schedule():
    data = load_json(
        SCHEDULE_FILE,
        DEFAULT_SCHEDULE
    )

    if not isinstance(data, dict):
        data = DEFAULT_SCHEDULE.copy()

    enabled = data.get(
        "enabled",
        True
    )

    interval = data.get(
        "interval_minutes",
        15
    )

    last_sent_at = data.get(
        "last_sent_at"
    )

    try:
        interval = int(interval)
    except (ValueError, TypeError):
        interval = 15

    allowed_intervals = {
        15,
        30,
        60,
        120,
        240,
        360,
        720,
        1440,
    }

    if interval not in allowed_intervals:
        interval = 15

    return {
        "enabled": bool(enabled),
        "interval_minutes": interval,
        "last_sent_at": last_sent_at,
    }


def save_schedule(schedule):
    return save_json(
        SCHEDULE_FILE,
        schedule
    )


# =========================================================
# TIME HELPERS
# =========================================================

def tehran_now():
    return datetime.now(TEHRAN_TZ)


def jalali_datetime():
    now = tehran_now()

    return jdatetime.datetime.fromgregorian(
        datetime=now
    )


def jalali_date_text():
    jd = jalali_datetime()

    return jd.strftime("%Y/%m/%d")


def tehran_time_text():
    return tehran_now().strftime(
        "%H:%M:%S"
    )


def iso_now():
    return tehran_now().isoformat()


def parse_iso_datetime(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=TEHRAN_TZ
            )

        return dt

    except Exception:
        return None


# =========================================================
# DIGIT NORMALIZATION
# =========================================================

def normalize_digits(value):
    """
    Convert Persian and Arabic digits to ASCII digits.
    """

    if value is None:
        return ""

    value = str(value)

    translation = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )

    return value.translate(translation)


def parse_number(value):
    """
    Convert a formatted number into integer.

    Handles:

    2,688,200
    ۲,۶۸۸,۲۰۰
    2٬688٬200
    ۲۶۲٬۴۲۹٬۰۰۰
    """

    if value is None:
        return None

    value = normalize_digits(value)

    value = (
        value
        .replace(",", "")
        .replace("٬", "")
        .replace(" ", "")
        .replace("\u200c", "")
        .strip()
    )

    match = re.search(
        r"\d+",
        value
    )

    if not match:
        return None

    try:
        return int(match.group())
    except (ValueError, TypeError):
        return None


# =========================================================
# PRICE RANGE VALIDATION
# =========================================================

def is_valid_rial_price(value, kind):
    """
    Validate a candidate TGJU value.

    These ranges are intentionally broad.
    They are only intended to eliminate timestamps,
    IDs and unrelated statistics.
    """

    if value is None:
        return False

    if kind == "usd":
        # USD free-market price in Rial.
        return (
            500_000
            <= value
            <= 20_000_000
        )

    if kind == "gold":
        # 18K gold price in Rial.
        return (
            50_000_000
            <= value
            <= 2_000_000_000
        )

    return False


# =========================================================
# TGJU PRICE EXTRACTION
# =========================================================

def extract_current_rate(
    html,
    visible_text,
    kind
):
    """
    Extract the CURRENT TGJU price.

    Priority:

    1. "نرخ فعلی"
    2. Specific DOM price elements
    3. Context around "قیمت"
    4. Other safe fallbacks

    We intentionally DO NOT use TGJU daily percentage.
    """

    # -----------------------------------------------------
    # 1. Strongest match:
    #    نرخ فعلی:: 2,688,200
    # -----------------------------------------------------

    sources = []

    if visible_text:
        sources.append(
            normalize_digits(
                visible_text
            )
        )

    if html:
        sources.append(
            normalize_digits(
                html
            )
        )

    current_patterns = [
        r"نرخ\s*فعلی\s*[:：]+\s*"
        r"([0-9][0-9,٬\s]{4,})",

        r"نرخ\s*فعلی\s*[:：]?\s*"
        r"([0-9][0-9,٬\s]{4,})",
    ]

    for source in sources:

        if not source:
            continue

        for pattern in current_patterns:

            match = re.search(
                pattern,
                source,
                flags=re.IGNORECASE
            )

            if not match:
                continue

            number = parse_number(
                match.group(1)
            )

            if (
                number is not None
                and is_valid_rial_price(
                    number,
                    kind
                )
            ):
                print(
                    f"{kind}: current rate found "
                    f"using 'نرخ فعلی': "
                    f"{number:,} Rial"
                )

                return number

    # -----------------------------------------------------
    # 2. DOM selectors
    # -----------------------------------------------------

    try:

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        selectors = [
            '[data-field="price"]',
            '[data-field="current"]',
            '[data-field="last"]',
            '.price',
            '.price-value',
            '.current-price',
            '.profile-price',
            '.info .value',
            '.price-box .value',
            '[class*="price"]',
        ]

        for selector in selectors:

            try:
                elements = soup.select(
                    selector
                )
            except Exception:
                continue

            for element in elements:

                text_value = element.get_text(
                    " ",
                    strip=True
                )

                if not text_value:
                    continue

                candidates = re.findall(
                    r"[0-9][0-9,٬\s]{4,}",
                    normalize_digits(
                        text_value
                    )
                )

                for candidate in candidates:

                    number = parse_number(
                        candidate
                    )

                    if (
                        number is not None
                        and is_valid_rial_price(
                            number,
                            kind
                        )
                    ):
                        print(
                            f"{kind}: current rate found "
                            f"from DOM selector "
                            f"{selector}: "
                            f"{number:,} Rial"
                        )

                        return number

    except Exception as exc:

        print(
            f"DOM parser warning for "
            f"{kind}: {exc}"
        )

    # -----------------------------------------------------
    # 3. Contextual fallback
    # -----------------------------------------------------

    text = normalize_digits(
        visible_text or ""
    )

    contextual_patterns = [

        r"در\s+حال\s+حاضر.*?"
        r"([0-9][0-9,٬\s]{5,})"
        r"\s*ریال",

        r"قیمت.*?"
        r"([0-9][0-9,٬\s]{5,})"
        r"\s*ریال",
    ]

    for pattern in contextual_patterns:

        matches = re.findall(
            pattern,
            text,
            flags=re.DOTALL
        )

        for candidate in matches:

            number = parse_number(
                candidate
            )

            if (
                number is not None
                and is_valid_rial_price(
                    number,
                    kind
                )
            ):
                print(
                    f"{kind}: current rate found "
                    f"using contextual fallback: "
                    f"{number:,} Rial"
                )

                return number

    # -----------------------------------------------------
    # 4. Embedded JSON fallback
    # -----------------------------------------------------

    json_patterns = [

        r'"price"\s*:\s*"'
        r"([0-9][0-9,٬]*)",

        r'"price"\s*:\s*'
        r"([0-9][0-9,٬]*)",

        r'"current"\s*:\s*"'
        r"([0-9][0-9,٬]*)",

        r'"current"\s*:\s*'
        r"([0-9][0-9,٬]*)",
    ]

    for source in sources:

        for pattern in json_patterns:

            matches = re.findall(
                pattern,
                source
            )

            for candidate in matches:

                number = parse_number(
                    candidate
                )

                if (
                    number is not None
                    and is_valid_rial_price(
                        number,
                        kind
                    )
                ):
                    print(
                        f"{kind}: current rate found "
                        f"from embedded data: "
                        f"{number:,} Rial"
                    )

                    return number

    print(
        f"ERROR: no reliable current "
        f"{kind} rate found."
    )

    return None


# =========================================================
# FETCH MARKET PRICE
# =========================================================

def market_price(url, kind):
    """
    Fetch TGJU current price and return Toman.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept-Language": (
            "fa-IR,fa;q=0.9,en;q=0.8"
        ),
        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/xml;q=0.9,"
            "*/*;q=0.8"
        ),
    }

    try:

        print(
            f"Fetching {kind} from TGJU..."
        )

        response = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        response.raise_for_status()

        html = response.text

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        visible_text = soup.get_text(
            " ",
            strip=True
        )

        rial_price = extract_current_rate(
            html,
            visible_text,
            kind
        )

        if rial_price is None:

            print(
                f"ERROR: Could not extract "
                f"valid {kind} price."
            )

            return None

        # TGJU profile value is Rial.
        # Telegram message is Toman.
        toman_price = rial_price // 10

        print(
            f"{kind.upper()} | "
            f"Rial: {rial_price:,} | "
            f"Toman: {toman_price:,}"
        )

        return toman_price

    except requests.RequestException as exc:

        print(
            f"ERROR fetching {kind}: {exc}"
        )

        return None

    except Exception as exc:

        print(
            f"ERROR parsing {kind}: {exc}"
        )

        return None


# =========================================================
# PERCENTAGE CALCULATION
# =========================================================

def calculate_change(
    previous,
    current
):
    """
    Calculate percentage change based ONLY on
    previous successful bot price.

    Formula:

    ((current - previous) / previous) * 100
    """

    if (
        previous is None
        or current is None
    ):
        return None

    try:

        previous = int(previous)
        current = int(current)

        if previous <= 0:
            return None

        change = (
            (current - previous)
            / previous
        ) * 100

        # Prevent -0.00
        if abs(change) < 0.0005:
            change = 0.0

        return change

    except (
        ValueError,
        TypeError,
        ZeroDivisionError
    ):
        return None


# =========================================================
# PRICE SANITY CHECK
# =========================================================

def is_suspicious_change(
    previous,
    current
):
    """
    Detect likely parser errors.

    If the parser suddenly returns a price more than
    20% away from the previous successful bot price,
    reject it instead of corrupting state.
    """

    if (
        previous is None
        or current is None
    ):
        return False

    try:

        previous = int(previous)
        current = int(current)

        if previous <= 0:
            return True

        ratio = (
            abs(current - previous)
            / previous
        )

        if ratio > PRICE_SANITY_LIMIT:

            print(
                "WARNING: suspicious price jump:"
            )

            print(
                f"Previous: {previous:,}"
            )

            print(
                f"Current : {current:,}"
            )

            print(
                f"Change  : {ratio * 100:.2f}%"
            )

            return True

        return False

    except (
        ValueError,
        TypeError,
        ZeroDivisionError
    ):

        return True


# =========================================================
# MESSAGE FORMAT
# =========================================================

def make_price_message(
    title,
    price,
    change,
    asset=None
):
    """
    Build Telegram HTML message.
    """

    now = tehran_now()

    jalali = jdatetime.datetime.fromgregorian(
        datetime=now
    )

    date_text = jalali.strftime(
        "%Y/%m/%d"
    )

    time_text = now.strftime(
        "%H:%M:%S"
    )

    price_text = f"{int(price):,}"

    # -----------------------------------------------------
    # First baseline
    # -----------------------------------------------------

    if change is None:

        direction = "➡️"

        change_text = (
            "⚪ جدید"
        )

        phrase = (
            "⚪ "
            "<tg-spoiler>"
            "قیمت مرجع ثبت شد"
            "</tg-spoiler>"
        )

    # -----------------------------------------------------
    # Increase
    # -----------------------------------------------------

    elif change > 0:

        direction = "⬆️"

        change_text = (
            f"🟢+{change:.2f}%"
        )

        phrase = (
            "🔴 "
            "<tg-spoiler>"
            "بگا رفتین"
            "</tg-spoiler>"
        )

    # -----------------------------------------------------
    # Decrease
    # -----------------------------------------------------

    elif change < 0:

        direction = "⬇️"

        change_text = (
            f"🔴{change:.2f}%"
        )

        phrase = (
            "🟢 "
            "<tg-spoiler>"
            "فکر کنم رفتن"
            "</tg-spoiler>"
        )

    # -----------------------------------------------------
    # No change
    # -----------------------------------------------------

    else:

        direction = "➡️"

        change_text = (
            "⚪0.00%"
        )

        phrase = (
            "⚪ "
            "<tg-spoiler>"
            "بدون تغییر"
            "</tg-spoiler>"
        )

    message = (
        f"{direction} "
        f"<b>{title}</b>\n\n"
        f"💰 {price_text} تومان\n"
        f"📊 تغییر نسبت به پیام قبلی: "
        f"{change_text}\n"
        f"{phrase}\n\n"
        f"🕐 {date_text} | {time_text}"
    )

    return message


# =========================================================
# SEND PRICES
# =========================================================

def send_prices():
    """
    Fetch USD + Gold, calculate changes based on
    previous successfully sent prices, send messages,
    pin them, and update state only after success.
    """

    state = load_price_state()

    previous_usd = state.get(
        "usd"
    )

    previous_gold = state.get(
        "gold"
    )

    print("")
    print("=" * 60)
    print("STARTING PRICE UPDATE")
    print("=" * 60)

    print(
        f"Previous USD : "
        f"{previous_usd:,}"
        if previous_usd is not None
        else "Previous USD : NONE"
    )

    print(
        f"Previous Gold: "
        f"{previous_gold:,}"
        if previous_gold is not None
        else "Previous Gold: NONE"
    )

    # -----------------------------------------------------
    # Fetch
    # -----------------------------------------------------

    current_usd = market_price(
        USD_URL,
        "usd"
    )

    current_gold = market_price(
        GOLD_URL,
        "gold"
    )

    sent_any = False

    # -----------------------------------------------------
    # USD
    # -----------------------------------------------------

    if current_usd is not None:

        if is_suspicious_change(
            previous_usd,
            current_usd
        ):

            print(
                "USD rejected because "
                "the price jump looks suspicious."
            )

        else:

            usd_change = calculate_change(
                previous_usd,
                current_usd
            )

            print(
                "USD change: "
                f"{usd_change:.4f}%"
                if usd_change is not None
                else "USD change: NEW"
            )

            usd_message = make_price_message(
                title="دلار آزاد 💵",
                price=current_usd,
                change=usd_change,
                asset="usd"
            )

            message_id = send_message(
                CHAT_ID,
                usd_message
            )

            if message_id:

                print(
                    f"USD message sent: "
                    f"{message_id}"
                )

                pin_message(
                    CHAT_ID,
                    message_id
                )

                # Only now update USD state.
                state["usd"] = current_usd

                sent_any = True

            else:

                print(
                    "USD message failed."
                )

                print(
                    "Previous USD state preserved."
                )

    else:

        print(
            "USD price unavailable."
        )

        print(
            "USD state preserved."
        )

    # -----------------------------------------------------
    # GOLD
    # -----------------------------------------------------

    if current_gold is not None:

        if is_suspicious_change(
            previous_gold,
            current_gold
        ):

            print(
                "Gold rejected because "
                "the price jump looks suspicious."
            )

        else:

            gold_change = calculate_change(
                previous_gold,
                current_gold
            )

            print(
                "Gold change: "
                f"{gold_change:.4f}%"
                if gold_change is not None
                else "Gold change: NEW"
            )

            gold_message = make_price_message(
                title="طلای ۱۸ عیار 🪙",
                price=current_gold,
                change=gold_change,
                asset="gold"
            )

            message_id = send_message(
                CHAT_ID,
                gold_message
            )

            if message_id:

                print(
                    f"Gold message sent: "
                    f"{message_id}"
                )

                pin_message(
                    CHAT_ID,
                    message_id
                )

                # Only now update Gold state.
                state["gold"] = current_gold

                sent_any = True

            else:

                print(
                    "Gold message failed."
                )

                print(
                    "Previous Gold state preserved."
                )

    else:

        print(
            "Gold price unavailable."
        )

        print(
            "Gold state preserved."
        )

    # -----------------------------------------------------
    # Save state
    # -----------------------------------------------------

    if sent_any:

        state["version"] = STATE_VERSION

        state["updated_at"] = iso_now()

        save_json(
            STATE_FILE,
            state
        )

        print(
            "Price state saved."
        )

    else:

        print(
            "No successful price message."
        )

        print(
            "Price state NOT changed."
        )

    print("=" * 60)
    print("PRICE UPDATE FINISHED")
    print("=" * 60)
    print("")

    return sent_any


# =========================================================
# SCHEDULE
# =========================================================

def automatic_send_due():
    """
    Determine whether automatic price sending is due.
    """

    schedule = load_schedule()

    if not schedule["enabled"]:
        return False

    last_sent_at = parse_iso_datetime(
        schedule.get("last_sent_at")
    )

    if last_sent_at is None:
        return True

    now = tehran_now()

    elapsed_seconds = (
        now - last_sent_at
    ).total_seconds()

    interval_seconds = (
        schedule["interval_minutes"]
        * 60
    )

    return (
        elapsed_seconds
        >= interval_seconds
    )


def mark_automatic_send():
    schedule = load_schedule()

    schedule["last_sent_at"] = iso_now()

    save_schedule(
        schedule
    )


# =========================================================
# ADMIN KEYBOARD
# =========================================================

def admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📤 ارسال فوری",
                    "callback_data": "send_now"
                }
            ],
            [
                {
                    "text": "▶️ فعال کردن",
                    "callback_data": "resume"
                },
                {
                    "text": "⏸ توقف",
                    "callback_data": "pause"
                }
            ],
            [
                {
                    "text": "⏱ تنظیم زمان",
                    "callback_data": "interval"
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


def interval_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "15 دقیقه",
                    "callback_data": "interval_15"
                },
                {
                    "text": "30 دقیقه",
                    "callback_data": "interval_30"
                }
            ],
            [
                {
                    "text": "1 ساعت",
                    "callback_data": "interval_60"
                },
                {
                    "text": "2 ساعت",
                    "callback_data": "interval_120"
                }
            ],
            [
                {
                    "text": "4 ساعت",
                    "callback_data": "interval_240"
                },
                {
                    "text": "6 ساعت",
                    "callback_data": "interval_360"
                }
            ],
            [
                {
                    "text": "12 ساعت",
                    "callback_data": "interval_720"
                },
                {
                    "text": "24 ساعت",
                    "callback_data": "interval_1440"
                }
            ],
            [
                {
                    "text": "⬅️ بازگشت",
                    "callback_data": "admin_back"
                }
            ]
        ]
    }


# =========================================================
# ADMIN PANEL
# =========================================================

def admin_panel_text():
    schedule = load_schedule()

    status = (
        "🟢 فعال"
        if schedule["enabled"]
        else "🔴 متوقف"
    )

    interval = schedule[
        "interval_minutes"
    ]

    last_sent = (
        schedule.get("last_sent_at")
        or "هنوز ارسال نشده"
    )

    return (
        "⚙️ <b>پنل مدیریت ربات</b>\n\n"
        f"وضعیت: {status}\n"
        f"⏱ فاصله ارسال: "
        f"{format_interval(interval)}\n"
        f"🕐 آخرین ارسال: {last_sent}\n\n"
        "یکی از گزینه‌ها را انتخاب کنید:"
    )


def format_interval(minutes):
    mapping = {
        15: "15 دقیقه",
        30: "30 دقیقه",
        60: "1 ساعت",
        120: "2 ساعت",
        240: "4 ساعت",
        360: "6 ساعت",
        720: "12 ساعت",
        1440: "24 ساعت",
    }

    return mapping.get(
        minutes,
        f"{minutes} دقیقه"
    )


def send_admin_panel(chat_id):
    return telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": admin_panel_text(),
            "parse_mode": "HTML",
            "reply_markup": admin_keyboard(),
        }
    )


# =========================================================
# STATUS
# =========================================================

def status_text():
    schedule = load_schedule()
    state = load_price_state()

    enabled = (
        "🟢 فعال"
        if schedule["enabled"]
        else "🔴 متوقف"
    )

    interval = format_interval(
        schedule["interval_minutes"]
    )

    last_sent = (
        schedule.get("last_sent_at")
        or "هنوز ارسال نشده"
    )

    usd = state.get("usd")
    gold = state.get("gold")

    usd_text = (
        f"{int(usd):,} تومان"
        if usd is not None
        else "ثبت نشده"
    )

    gold_text = (
        f"{int(gold):,} تومان"
        if gold is not None
        else "ثبت نشده"
    )

    return (
        "📊 <b>وضعیت ربات</b>\n\n"
        f"وضعیت ارسال خودکار: {enabled}\n"
        f"⏱ فاصله: {interval}\n"
        f"🕐 آخرین ارسال: {last_sent}\n\n"
        f"💵 آخرین دلار: {usd_text}\n"
        f"🪙 آخرین طلای ۱۸: {gold_text}"
    )


# =========================================================
# CALLBACK ANSWER
# =========================================================

def answer_callback(callback_id, text):
    telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False,
        }
    )


# =========================================================
# EDIT ADMIN MESSAGE
# =========================================================

def edit_message(
    chat_id,
    message_id,
    text,
    reply_markup=None
):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }

    if reply_markup is not None:
        payload[
            "reply_markup"
        ] = reply_markup

    return telegram(
        "editMessageText",
        payload
    )


# =========================================================
# ADMIN AUTHORIZATION
# =========================================================

def is_admin(user_id):
    if user_id is None:
        return False

    return str(user_id) in ADMIN_IDS


# =========================================================
# HANDLE CALLBACK
# =========================================================

def handle_callback(callback):
    callback_id = callback.get(
        "id"
    )

    data = callback.get(
        "data",
        ""
    )

    from_user = callback.get(
        "from",
        {}
    )

    user_id = from_user.get(
        "id"
    )

    if not is_admin(user_id):

        answer_callback(
            callback_id,
            "⛔ دسترسی ندارید."
        )

        return

    message = callback.get(
        "message",
        {}
    )

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    message_id = message.get(
        "message_id"
    )

    # -----------------------------------------------------
    # Send now
    # -----------------------------------------------------

    if data == "send_now":

        answer_callback(
            callback_id,
            "در حال دریافت قیمت..."
        )

        success = send_prices()

        schedule = load_schedule()

        if success:
            schedule[
                "last_sent_at"
            ] = iso_now()

            save_schedule(
                schedule
            )

        edit_message(
            chat_id,
            message_id,
            admin_panel_text(),
            admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # Pause
    # -----------------------------------------------------

    if data == "pause":

        schedule = load_schedule()

        schedule[
            "enabled"
        ] = False

        save_schedule(
            schedule
        )

        answer_callback(
            callback_id,
            "⏸ ارسال خودکار متوقف شد."
        )

        edit_message(
            chat_id,
            message_id,
            admin_panel_text(),
            admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # Resume
    # -----------------------------------------------------

    if data == "resume":

        schedule = load_schedule()

        schedule[
            "enabled"
        ] = True

        save_schedule(
            schedule
        )

        answer_callback(
            callback_id,
            "▶️ ارسال خودکار فعال شد."
        )

        edit_message(
            chat_id,
            message_id,
            admin_panel_text(),
            admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # Interval menu
    # -----------------------------------------------------

    if data == "interval":

        answer_callback(
            callback_id,
            "زمان ارسال را انتخاب کنید."
        )

        edit_message(
            chat_id,
            message_id,
            "⏱ <b>تنظیم فاصله ارسال</b>\n\n"
            "فاصله موردنظر را انتخاب کنید:",
            interval_keyboard()
        )

        return

    # -----------------------------------------------------
    # Back
    # -----------------------------------------------------

    if data == "admin_back":

        answer_callback(
            callback_id,
            "بازگشت"
        )

        edit_message(
            chat_id,
            message_id,
            admin_panel_text(),
            admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # Status
    # -----------------------------------------------------

    if data == "status":

        answer_callback(
            callback_id,
            "وضعیت ربات"
        )

        edit_message(
            chat_id,
            message_id,
            status_text(),
            admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # Interval values
    # -----------------------------------------------------

    if data.startswith(
        "interval_"
    ):

        try:

            minutes = int(
                data.split(
                    "_",
                    1
                )[1]
            )

        except (
            ValueError,
            IndexError
        ):

            answer_callback(
                callback_id,
                "خطای تنظیم زمان."
            )

            return

        allowed = {
            15,
            30,
            60,
            120,
            240,
            360,
            720,
            1440,
        }

        if minutes not in allowed:

            answer_callback(
                callback_id,
                "زمان نامعتبر است."
            )

            return

        schedule = load_schedule()

        schedule[
            "interval_minutes"
        ] = minutes

        save_schedule(
            schedule
        )

        answer_callback(
            callback_id,
            f"زمان روی "
            f"{format_interval(minutes)} "
            f"تنظیم شد."
        )

        edit_message(
            chat_id,
            message_id,
            admin_panel_text(),
            admin_keyboard()
        )

        return

    answer_callback(
        callback_id,
        "دستور ناشناخته."
    )


# =========================================================
# HANDLE MESSAGE
# =========================================================

def handle_message(message):
    from_user = message.get(
        "from",
        {}
    )

    user_id = from_user.get(
        "id"
    )

    text = message.get(
        "text",
        ""
    )

    if not text:
        return

    # Only admin can use /admin.
    if text.startswith(
        "/admin"
    ):

        if not is_admin(user_id):

            print(
                f"Unauthorized /admin "
                f"attempt from user {user_id}"
            )

            return

        chat = message.get(
            "chat",
            {}
        )

        chat_type = chat.get(
            "type"
        )

        # Admin panel should be private.
        if chat_type != "private":

            print(
                "Ignoring /admin outside "
                "private chat."
            )

            return

        chat_id = chat.get(
            "id"
        )

        send_admin_panel(
            chat_id
        )


# =========================================================
# UPDATE OFFSET
# =========================================================

def load_offset():
    data = load_json(
        OFFSET_FILE,
        {
            "offset": 0
        }
    )

    try:
        return int(
            data.get(
                "offset",
                0
            )
        )

    except (
        ValueError,
        TypeError
    ):
        return 0


def save_offset(offset):
    return save_json(
        OFFSET_FILE,
        {
            "offset": int(offset)
        }
    )


# =========================================================
# PROCESS UPDATES
# =========================================================

def process_updates(updates):
    for update in updates:

        try:

            if "callback_query" in update:

                handle_callback(
                    update[
                        "callback_query"
                    ]
                )

            elif "message" in update:

                handle_message(
                    update["message"]
                )

        except Exception as exc:

            print(
                "Error processing update:",
                exc
            )


# =========================================================
# TELEGRAM POLLING
# =========================================================

def poll_telegram():
    """
    Poll Telegram for approximately 60 seconds.

    This keeps the admin panel responsive during
    each GitHub Actions execution.
    """

    print(
        f"Starting Telegram polling "
        f"for {POLL_SECONDS} seconds..."
    )

    started_at = time.time()

    offset = load_offset()

    while (
        time.time() - started_at
        < POLL_SECONDS
    ):

        remaining = (
            POLL_SECONDS
            - (
                time.time()
                - started_at
            )
        )

        if remaining <= 0:
            break

        timeout = min(
            20,
            max(
                1,
                int(remaining)
            )
        )

        result = telegram(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": [
                    "message",
                    "callback_query",
                ],
            }
        )

        if result is None:

            time.sleep(2)

            continue

        if not result:

            continue

        for update in result:

            update_id = update.get(
                "update_id"
            )

            if update_id is not None:

                offset = (
                    int(update_id)
                    + 1
                )

                save_offset(
                    offset
                )

        process_updates(
            result
        )

    print(
        "Telegram polling finished."
    )


# =========================================================
# DELETE WEBHOOK
# =========================================================

def delete_webhook():
    result = telegram(
        "deleteWebhook",
        {
            "drop_pending_updates": False
        }
    )

    if result:
        print(
            "Telegram webhook removed."
        )

    else:
        print(
            "Could not confirm webhook removal."
        )


# =========================================================
# MAIN
# =========================================================

def main():

    print("")
    print("=" * 60)
    print("TELEGRAM PRICE BOT")
    print("=" * 60)

    print(
        f"Tehran time: "
        f"{tehran_now().isoformat()}"
    )

    print(
        f"State version: "
        f"{STATE_VERSION}"
    )

    print(
        f"Poll duration: "
        f"{POLL_SECONDS} seconds"
    )

    print("=" * 60)
    print("")

    # -----------------------------------------------------
    # Telegram setup
    # -----------------------------------------------------

    delete_webhook()

    # -----------------------------------------------------
    # Automatic price sending
    # -----------------------------------------------------

    try:

        if automatic_send_due():

            print(
                "Automatic price update is due."
            )

            success = send_prices()

            if success:

                mark_automatic_send()

                print(
                    "Automatic send completed."
                )

            else:

                print(
                    "Automatic send did not "
                    "complete successfully."
                )

        else:

            schedule = load_schedule()

            print(
                "Automatic send is not due."
            )

            print(
                f"Enabled: "
                f"{schedule['enabled']}"
            )

            print(
                f"Interval: "
                f"{format_interval("
                    schedule['interval_minutes']
                )
            )

    except Exception as exc:

        print(
            "Automatic sending error:",
            exc
        )

    # -----------------------------------------------------
    # Telegram admin polling
    # -----------------------------------------------------

    try:

        poll_telegram()

    except Exception as exc:

        print(
            "Polling error:",
            exc
        )

    print("")
    print(
        "Bot run finished."
    )
    print("")


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()
