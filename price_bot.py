import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from config import BOT_TOKEN, CHAT_ID, HISTORY_FILE, MAX_POINTS, TGJU_URL

TEHRAN = ZoneInfo("Asia/Tehran")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36"
}


def fa_to_en(s):
    return str(s).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٬", "0123456789,"))


def parse_number(s):
    s = fa_to_en(s)
    s = re.sub(r"[^0-9.-]", "", s.replace(",", ""))
    return float(s) if s else None


def toman(n):
    return round(n / 10)


def fmt(n):
    return f"{int(round(n)):,}"


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {"usd": [], "gold18": []}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"usd": [], "gold18": []}


def save_history(h):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)


def extract_price_from_page(text, keywords):
    # تلاش عمومی برای پیدا کردن عدد نزدیک کلیدواژه‌ها
    for keyword in keywords:
        pos = text.find(keyword)
        if pos >= 0:
            chunk = text[pos:pos + 1200]
            nums = re.findall(r"[۰-۹0-9][۰-۹0-9,٬]{4,}", chunk)
            values = [parse_number(x) for x in nums]
            values = [x for x in values if x and x > 1000]
            if values:
                return values[0]
    return None


def fetch_prices():
    r = requests.get(TGJU_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    # TGJU معمولاً قیمت‌ها را به ریال نمایش می‌دهد.
    usd_ri = extract_price_from_page(
        text, ["دلار", "دلار آمریکا", "قیمت دلار"]
    )
    gold_ri = extract_price_from_page(
        text, ["طلای 18", "طلای ۱۸", "طلا ۱۸", "طلای ۱۸ عیار"]
    )

    if usd_ri is None or gold_ri is None:
        raise RuntimeError(
            f"Could not find prices on TGJU. USD={usd_ri}, GOLD={gold_ri}"
        )

    return toman(usd_ri), toman(gold_ri)


def pct_change(points):
    if len(points) < 2 or not points[-2]["value"]:
        return 0.0
    return (points[-1]["value"] - points[-2]["value"]) / points[-2]["value"] * 100


def font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def chart(draw, points, box, line_width=4):
    x0, y0, x1, y1 = box
    if len(points) < 2:
        return

    vals = [p["value"] for p in points]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1

    # شبکه خیلی ظریف
    for i in range(4):
        y = y0 + (y1-y0) * i / 3
        draw.line((x0, y, x1, y), fill=(220, 225, 232), width=1)

    pts = []
    for i, v in enumerate(vals):
        x = x0 if len(vals) == 1 else x0 + (x1-x0) * i/(len(vals)-1)
        y = y1 - (v-lo)/(hi-lo) * (y1-y0)
        pts.append((x, y))

    # fill زیر نمودار
    poly = [(x0, y1)] + pts + [(x1, y1)]
    draw.polygon(poly, fill=(245, 170, 170))
    draw.line(pts, fill=(235, 55, 60), width=line_width, joint="curve")
    draw.ellipse((pts[-1][0]-5, pts[-1][1]-5, pts[-1][0]+5, pts[-1][1]+5),
                 fill=(220, 45, 50))


def make_card(title, flag, value, change, history, out_path):
    W, H = 1000, 430
    img = Image.new("RGB", (W, H), (246, 247, 249))
    d = ImageDraw.Draw(img)

    # پس‌زمینه شیشه‌ای/نرم
    d.rounded_rectangle((35, 25, W-35, H-25), radius=42,
                        fill=(249, 250, 252), outline=(220, 223, 228), width=2)

    # عنوان
    d.text((690, 55), f"{title} {flag}", font=font(38, True),
           fill=(32, 35, 42), anchor="ra")

    # واحد
    d.rounded_rectangle((55, 55, 130, 105), radius=18, fill=(235, 237, 240))
    d.text((92, 80), "IRT", font=font(23, True),
           fill=(155, 158, 165), anchor="mm")

    # قیمت
    d.text((690, 150), fmt(value), font=font(86, True),
           fill=(35, 38, 47), anchor="ra")
    d.text((710, 145), "تومان", font=font(30, True),
           fill=(80, 83, 91), anchor="la")

    # تغییر
    up = change >= 0
    symbol = "▲" if up else "▼"
    change_text = f"{symbol} {abs(change):.2f}%"
    pill_fill = (221, 244, 229) if up else (250, 224, 224)
    pill_text = (33, 145, 75) if up else (210, 75, 75)
    d.rounded_rectangle((700, 235, 900, 285), radius=22, fill=pill_fill)
    d.text((800, 260), change_text, font=font(24, True),
           fill=pill_text, anchor="mm")

    # نمودار
    chart(d, history, (90, 315, 900, 395), 4)

    img.save(out_path, quality=95)


def telegram(method, data=None, files=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    r = requests.post(url, data=data, files=files, timeout=60)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload["result"]


def send_photo(path, caption):
    with open(path, "rb") as f:
        return telegram(
            "sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"photo": f},
        )


def pin(message_id):
    telegram(
        "pinChatMessage",
        data={
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "disable_notification": "true",
        },
    )


def unpin_all():
    try:
        telegram("unpinAllChatMessages", data={"chat_id": CHAT_ID})
    except Exception:
        # اگر پیام قبلی پین نشده بود، ادامه بده
        pass


def main():
    now = datetime.now(TEHRAN)
    usd, gold = fetch_prices()

    h = load_history()
    stamp = now.isoformat(timespec="seconds")

    h["usd"].append({"time": stamp, "value": usd})
    h["gold18"].append({"time": stamp, "value": gold})
    h["usd"] = h["usd"][-MAX_POINTS:]
    h["gold18"] = h["gold18"][-MAX_POINTS:]
    save_history(h)

    usd_change = pct_change(h["usd"])
    gold_change = pct_change(h["gold18"])

    usd_img = "usd.png"
    gold_img = "gold18.png"

    make_card("دلار آمریکا", "🇺🇸", usd, usd_change, h["usd"], usd_img)
    make_card("طلای ۱۸ عیار", "🥇", gold, gold_change, h["gold18"], gold_img)

    # نمونه پیام با ساختار نزدیک به اسکرین‌شات
    date_str = now.strftime("%Y/%m/%d")
    time_str = now.strftime("%H:%M:%S")

    usd_caption = (
        f"🇺🇸 1 دلار آمریکا :\n"
        f"🟢 {fmt(usd)} toman\n"
        f"🟢 1 dollar\n\n"
        f"🟣 {date_str} | {time_str}"
    )

    gold_caption = (
        f"🥇 طلای ۱۸ عیار :\n"
        f"🟢 {fmt(gold)} toman\n"
        f"🟢 1 gram\n\n"
        f"🟣 {date_str} | {time_str}"
    )

    unpin_all()

    m1 = send_photo(usd_img, usd_caption)
    pin(m1["message_id"])

    m2 = send_photo(gold_img, gold_caption)
    pin(m2["message_id"])


if __name__ == "__main__":
    main()
