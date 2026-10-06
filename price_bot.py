import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from config import BOT_TOKEN, CHAT_ID, HISTORY_FILE, MAX_POINTS

TEHRAN = ZoneInfo("Asia/Tehran")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
}

USD_URL = "https://www.tgju.org/profile/price_dollar_rl"
GOLD_URL = "https://www.tgju.org/profile/geram18"


def fa_to_en(text):
    return str(text).translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٬",
            "0123456789,"
        )
    )


def parse_price(text):
    text = fa_to_en(text)
    text = text.replace(",", "").replace("٬", "")
    numbers = re.findall(r"\d+(?:\.\d+)?", text)

    if not numbers:
        return None

    return float(numbers[0])


def get_tgju_price(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    # روش اصلی TGJU
    span = soup.find("span", class_="value")

    if span:
        value = span.get_text(
            " ",
            strip=True
        )

        price = parse_price(value)

        if price:
            return price

    # روش جایگزین
    selectors = [
        '[data-field="price"]',
        '[data-price]',
        '.price',
        '.value',
    ]

    for selector in selectors:
        element = soup.select_one(selector)

        if not element:
            continue

        raw = (
            element.get("data-price")
            or element.get_text(" ", strip=True)
        )

        price = parse_price(raw)

        if price:
            return price

    raise RuntimeError(
        f"Could not extract price from {url}"
    )


def fetch_prices():
    usd_rial = get_tgju_price(USD_URL)
    gold_rial = get_tgju_price(GOLD_URL)

    if usd_rial is None:
        raise RuntimeError(
            "USD price not found"
        )

    if gold_rial is None:
        raise RuntimeError(
            "Gold 18 price not found"
        )

    # TGJU قیمت‌ها را ریالی می‌دهد.
    usd_toman = round(usd_rial / 10)
    gold_toman = round(gold_rial / 10)

    return usd_toman, gold_toman


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {
            "usd": [],
            "gold18": []
        }

    try:
        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception:
        return {
            "usd": [],
            "gold18": []
        }


def save_history(history):
    with open(
        HISTORY_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            history,
            f,
            ensure_ascii=False,
            indent=2
        )


def percent_change(points):
    if len(points) < 2:
        return 0.0

    previous = points[-2]["value"]
    current = points[-1]["value"]

    if previous == 0:
        return 0.0

    return (
        (current - previous)
        / previous
        * 100
    )


def fmt(value):
    return f"{int(round(value)):,}"


def get_font(size, bold=False):

    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",

        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",

        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    ]

    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


def draw_chart(draw, points, box):

    x0, y0, x1, y1 = box

    if len(points) < 2:
        return

    values = [
        item["value"]
        for item in points
    ]

    minimum = min(values)
    maximum = max(values)

    if maximum == minimum:
        maximum += 1

    # خطوط زمینه
    for i in range(4):

        y = y0 + (
            (y1 - y0)
            * i
            / 3
        )

        draw.line(
            (x0, y, x1, y),
            fill=(220, 223, 228),
            width=1
        )

    coordinates = []

    for i, value in enumerate(values):

        x = (
            x0
            if len(values) == 1
            else x0
            + (x1 - x0)
            * i
            / (len(values) - 1)
        )

        y = (
            y1
            - (
                (value - minimum)
                / (maximum - minimum)
            )
            * (y1 - y0)
        )

        coordinates.append(
            (x, y)
        )

    # ناحیه زیر نمودار
    polygon = [
        (x0, y1),
        *coordinates,
        (x1, y1)
    ]

    draw.polygon(
        polygon,
        fill=(248, 225, 225)
    )

    draw.line(
        coordinates,
        fill=(220, 55, 60),
        width=4
    )

    last_x, last_y = coordinates[-1]

    draw.ellipse(
        (
            last_x - 5,
            last_y - 5,
            last_x + 5,
            last_y + 5
        ),
        fill=(220, 55, 60)
    )


def create_card(
    title,
    emoji,
    value,
    change,
    history,
    filename
):

    width = 1000
    height = 430

    image = Image.new(
        "RGB",
        (width, height),
        (246, 247, 249)
    )

    draw = ImageDraw.Draw(image)

    # کارت
    draw.rounded_rectangle(
        (
            30,
            25,
            width - 30,
            height - 25
        ),
        radius=40,
        fill=(250, 250, 252),
        outline=(220, 223, 228),
        width=2
    )

    # عنوان
    draw.text(
        (900, 60),
        f"{title} {emoji}",
        font=get_font(38, True),
        fill=(35, 38, 45),
        anchor="ra"
    )

    # واحد
    draw.rounded_rectangle(
        (65, 55, 145, 105),
        radius=18,
        fill=(235, 237, 240)
    )

    draw.text(
        (105, 80),
        "IRT",
        font=get_font(22, True),
        fill=(130, 133, 140),
        anchor="mm"
    )

    # قیمت
    draw.text(
        (720, 165),
        fmt(value),
        font=get_font(80, True),
        fill=(35, 38, 45),
        anchor="ra"
    )

    draw.text(
        (740, 160),
        "تومان",
        font=get_font(28, True),
        fill=(80, 83, 90),
        anchor="la"
    )

    # درصد تغییر
    positive = change >= 0

    arrow = "▲" if positive else "▼"

    change_text = (
        f"{arrow} {abs(change):.2f}%"
    )

    background = (
        (221, 244, 229)
        if positive
        else
        (250, 224, 224)
    )

    text_color = (
        (35, 145, 75)
        if positive
        else
        (210, 75, 75)
    )

    draw.rounded_rectangle(
        (700, 230, 900, 285),
        radius=22,
        fill=background
    )

    draw.text(
        (800, 257),
        change_text,
        font=get_font(24, True),
        fill=text_color,
        anchor="mm"
    )

    # نمودار
    draw_chart(
        draw,
        history,
        (90, 315, 900, 395)
    )

    image.save(
        filename,
        quality=95
    )


def telegram_request(
    method,
    data=None,
    files=None
):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    response = requests.post(
        url,
        data=data,
        files=files,
        timeout=60
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    return result["result"]


def send_photo(
    filename,
    caption
):

    with open(
        filename,
        "rb"
    ) as photo:

        return telegram_request(
            "sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "caption": caption,
            },
            files={
                "photo": photo
            }
        )


def unpin_all():

    try:
        telegram_request(
            "unpinAllChatMessages",
            data={
                "chat_id": CHAT_ID
            }
        )

    except Exception:
        pass


def pin_message(message_id):

    telegram_request(
        "pinChatMessage",
        data={
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "disable_notification": "true"
        }
    )


def main():

    now = datetime.now(
        TEHRAN
    )

    print("Fetching prices...")

    usd, gold = fetch_prices()

    print(
        f"USD: {usd:,} toman"
    )

    print(
        f"Gold 18: {gold:,} toman"
    )

    history = load_history()

    timestamp = now.isoformat(
        timespec="seconds"
    )

    history["usd"].append({
        "time": timestamp,
        "value": usd
    })

    history["gold18"].append({
        "time": timestamp,
        "value": gold
    })

    history["usd"] = (
        history["usd"][-MAX_POINTS:]
    )

    history["gold18"] = (
        history["gold18"][-MAX_POINTS:]
    )

    save_history(history)

    usd_change = percent_change(
        history["usd"]
    )

    gold_change = percent_change(
        history["gold18"]
    )

    create_card(
        "دلار آمریکا",
        "🇺🇸",
        usd,
        usd_change,
        history["usd"],
        "usd.png"
    )

    create_card(
        "طلای ۱۸ عیار",
        "🥇",
        gold,
        gold_change,
        history["gold18"],
        "gold18.png"
    )

    date = now.strftime(
        "%Y/%m/%d"
    )

    time = now.strftime(
        "%H:%M:%S"
    )

    usd_caption = (
        f"🇺🇸 1 دلار آمریکا :\n"
        f"🟢 {fmt(usd)} تومان\n"
        f"🟢 1 dollar\n\n"
        f"🟣 {date} | {time}"
    )

    gold_caption = (
        f"🥇 طلای ۱۸ عیار :\n"
        f"🟢 {fmt(gold)} تومان\n"
        f"🟢 1 gram\n\n"
        f"🟣 {date} | {time}"
    )

    print("Sending messages...")

    unpin_all()

    usd_message = send_photo(
        "usd.png",
        usd_caption
    )

    pin_message(
        usd_message["message_id"]
    )

    gold_message = send_photo(
        "gold18.png",
        gold_caption
    )

    pin_message(
        gold_message["message_id"]
    )

    print("DONE!")


if __name__ == "__main__":
    main()
