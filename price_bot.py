import io
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import arabic_reshaper
from bidi.algorithm import get_display

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
HISTORY_FILE = os.getenv("HISTORY_FILE", "history.json")
MAX_POINTS = int(os.getenv("MAX_POINTS", "120"))

USD_URL = "https://www.tgju.org/profile/price_dollar_rl"
GOLD_URL = "https://www.tgju.org/profile/geram18"

TEHRAN = ZoneInfo("Asia/Tehran")
FONT_DIR = "fonts"
FONT_URL = "https://github.com/rastikerdar/vazirmatn/raw/master/fonts/ttf/Vazirmatn-Regular.ttf"
FONT_BOLD_URL = "https://github.com/rastikerdar/vazirmatn/raw/master/fonts/ttf/Vazirmatn-Bold.ttf"


def ensure_fonts():
    os.makedirs(FONT_DIR, exist_ok=True)
    for name, url in [("Vazirmatn-Regular.ttf", FONT_URL),
                      ("Vazirmatn-Bold.ttf", FONT_BOLD_URL)]:
        path = os.path.join(FONT_DIR, name)
        if not os.path.exists(path):
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)


def font(size, bold=False):
    name = "Vazirmatn-Bold.ttf" if bold else "Vazirmatn-Regular.ttf"
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


def fa(text):
    """Return correctly shaped RTL Persian text for Pillow."""
    if not text:
        return ""
    return get_display(arabic_reshaper.reshape(str(text)))


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
            # TGJU pages expose these prices in Rial.
            return float(digits) / 10.0

    raise RuntimeError(f"Could not find price on TGJU: {url}")


def fetch_prices():
    usd = get_tgju_price(USD_URL)
    gold = get_tgju_price(GOLD_URL)
    print(f"USD={usd:,.0f} Toman | GOLD18={gold:,.0f} Toman")
    return usd, gold


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {"usd": [], "gold": []}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"usd": [], "gold": []}
        data.setdefault("usd", [])
        data.setdefault("gold", [])
        return data
    except Exception:
        return {"usd": [], "gold": []}


def save_history(data):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_history(data, key, price, now_iso):
    arr = data.setdefault(key, [])
    arr.append({"ts": now_iso, "price": price})
    data[key] = arr[-MAX_POINTS:]


def history_prices(data, key):
    """Read numeric prices from both old and new history formats."""
    out = []
    for item in data.get(key, []) or []:
        try:
            if isinstance(item, dict):
                value = item.get("price")
            else:
                value = item
            if value is not None:
                out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def previous_price(data, key, current):
    vals = history_prices(data, key)
    if not vals:
        return float(current)
    # Compare with the last collected observation.
    return vals[-1]

def pct_change(current, previous):
    if not previous:
        return 0.0
    return ((current - previous) / previous) * 100.0


def make_flag(size=64):
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    stripe = size / 13
    for i in range(13):
        if i % 2 == 0:
            d.rectangle((0, int(i * stripe), size, int((i + 1) * stripe + 1)),
                        fill=(190, 35, 55, 255))
    d.rectangle((0, 0, int(size * .42), int(size * 7 / 13)),
                fill=(35, 55, 120, 255))
    for row in range(5):
        for col in range(6):
            x = int(size * .05 + col * size * .065)
            y = int(size * .06 + row * size * .105)
            d.ellipse((x, y, x + 5, y + 5), fill="white")
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, size - 1, size - 1), fill=255)
    img.putalpha(mask)
    return img


def make_coin(size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=(238, 177, 42), outline=(255, 226, 112), width=3)
    d.ellipse((9, 9, size - 9, size - 9), outline=(166, 108, 16), width=2)
    f = font(int(size * .38), True)
    t = fa("طلا")
    box = d.textbbox((0, 0), t, font=f)
    d.text(((size - (box[2]-box[0]))/2, (size - (box[3]-box[1]))/2 - 4),
           t, font=f, fill=(120, 72, 8))
    return img


def draw_centered(draw, xy, text, fnt, fill):
    box = draw.textbbox((0, 0), text, font=fnt)
    w = box[2] - box[0]
    h = box[3] - box[1]
    draw.text((xy[0] - w / 2, xy[1] - h / 2), text, font=fnt, fill=fill)


def draw_chart(base, rect, points, line_color, fill_color):
    x0, y0, x1, y1 = rect
    points = [float(p) for p in points if p is not None]
    if not points:
        return

    # Never invent a trend. With one point, show a single point; with 2+,
    # connect exactly the collected observations in chronological order.
    lo, hi = min(points), max(points)
    if hi == lo:
        pad = max(abs(hi) * 0.002, 1.0)
    else:
        pad = (hi - lo) * 0.12
    lo -= pad
    hi += pad

    n = len(points)
    coords = []
    for i, p in enumerate(points):
        x = x0 if n == 1 else x0 + (x1 - x0) * i / (n - 1)
        y = y1 - (p - lo) / (hi - lo) * (y1 - y0)
        coords.append((x, y))

    d = ImageDraw.Draw(base, "RGBA")

    # Horizontal grid.
    for k in range(5):
        y = y0 + (y1 - y0) * k / 4
        d.line((x0, y, x1, y), fill=(110, 120, 145, 55), width=2)

    if len(coords) >= 2:
        poly = coords + [(coords[-1][0], y1), (coords[0][0], y1)]
        d.polygon(poly, fill=fill_color)
        d.line(coords, fill=line_color, width=6, joint="curve")

    r = 9
    for cx, cy in (coords[-1],):
        d.ellipse((cx-r, cy-r, cx+r, cy+r), fill=line_color)


def build_card(title, price, previous, history_points, is_gold, now):
    W, H = 1200, 800
    bg = Image.new("RGBA", (W, H), (13, 28, 43, 255))
    bd = ImageDraw.Draw(bg)

    if is_gold:
        for y in range(H):
            t = y / H
            bd.line((0, y, W, y),
                    fill=(52 + int(35*t), 38 + int(25*t), 19 + int(18*t), 255))
        for i in range(18):
            x = (i * 97) % W
            y = 90 + ((i * 151) % 430)
            bd.ellipse((x-70, y-70, x+70, y+70), fill=(180, 125, 28, 16))
    else:
        for i in range(13):
            yy = int(i * H / 13)
            color = (143, 27, 47, 255) if i % 2 == 0 else (225, 226, 226, 255)
            bd.rectangle((0, yy, W, int((i+1)*H/13)), fill=color)
        bd.rectangle((0, 0, 460, int(H*7/13)), fill=(26, 49, 104, 255))
        for row in range(5):
            for col in range(7):
                x = 35 + col * 60 + (25 if row % 2 else 0)
                y = 28 + row * 55
                bd.ellipse((x, y, x+11, y+11), fill=(245,245,245,210))

    bd.rectangle((0, 650, W, H), fill=(11, 26, 41, 250))

    card = Image.new("RGBA", (W-90, 610), (245, 247, 250, 238))
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, card.width-1, card.height-1), radius=48, fill=255
    )
    bg.paste(card, (45, 35), mask)
    d = ImageDraw.Draw(bg, "RGBA")

    title_f = font(44, True)
    price_f = font(86, True)
    unit_f = font(34, True)
    small_f = font(28, True)

    icon = make_coin(70) if is_gold else make_flag(70)
    bg.paste(icon, (1040, 62), icon)

    # RTL text is drawn from the right edge using an explicit anchor.
    title_text = fa(title)
    d.text((1020, 75), title_text, font=title_f, fill=(19,29,43,255), anchor="ra")

    price_text = fmt_price(price)
    d.text((1010, 130), price_text, font=price_f, fill=(19,29,43,255), anchor="ra")
    d.text((760, 190), fa("تومان"), font=unit_f, fill=(92,101,119,255), anchor="ra")

    pct = pct_change(price, previous)
    if pct > 0:
        badge_color = (34, 170, 92, 235)
        arrow = "↑"
        mood = "بگا رفتین"
        mood_color = (235, 54, 70, 255)
    elif pct < 0:
        badge_color = (231, 53, 69, 235)
        arrow = "↓"
        mood = "فکر کنم رفتن"
        mood_color = (39, 181, 96, 255)
    else:
        badge_color = (110, 120, 135, 235)
        arrow = "→"
        mood = "ثابت"
        mood_color = (150, 160, 170, 255)

    badge_text = fa(f"{arrow}  {fmt_pct(pct)}")
    bb = d.textbbox((0,0), badge_text, font=small_f)
    bw, bh = bb[2]-bb[0]+46, bb[3]-bb[1]+28
    bx, by = 810, 245
    d.rounded_rectangle((bx, by, bx+bw, by+bh), radius=22, fill=badge_color)
    d.text((bx+bw-23, by+bh/2), badge_text, font=small_f, fill="white", anchor="rm")

    # Use the actual collected series. Keep chronological order and do not
    # replace it with a synthetic curve.
    points = [float(p) for p in history_points if isinstance(p, (int, float))]
    if not points:
        points = [float(price)]
    chart_rect = (115, 350, 1085, 585)
    line_color = (34, 170, 92, 255) if pct > 0 else (231, 53, 69, 255)
    fill_color = (34, 170, 92, 55) if pct > 0 else (231, 53, 69, 55)
    draw_chart(bg, chart_rect, points, line_color, fill_color)

    label_f = font(22, False)
    lo, hi = min(points), max(points)
    for k, val in enumerate([hi, (hi+lo)/2, lo]):
        y = chart_rect[1] + (chart_rect[3]-chart_rect[1]) * k/2
        d.text((58, y-14), fmt_price(val), font=label_f, fill=(86,98,118,220))

    # Prominent status text in the empty lower area — exactly the two requested texts.
    status_f = font(42, True)
    status_color = mood_color if pct != 0 else (150,160,170,255)
    sb = d.textbbox((0,0), fa(mood), font=status_f)
    sw, sh = sb[2]-sb[0]+70, sb[3]-sb[1]+38
    sx, sy = 900, 682
    d.rounded_rectangle((sx, sy, sx+sw, sy+sh), radius=30,
                         fill=(18,30,45,245), outline=status_color, width=4)
    d.text((sx+sw-35, sy+sh/2), fa(mood), font=status_f,
           fill=status_color, anchor="rm")

    label = "دلار آمریکا" if not is_gold else "طلای ۱۸ عیار"
    d.text((100, 690), fa(label), font=font(32, True), fill="white")
    d.text((100, 738), f"{fmt_price(price)} تومان", font=font(29, False), fill=(235,240,246,255))

    try:
        import jdatetime
        jnow = jdatetime.datetime.fromgregorian(datetime=now.replace(tzinfo=None))
        date_s = jnow.strftime("%Y/%m/%d")
    except Exception:
        date_s = now.strftime("%Y/%m/%d")
    d.text((100, 775), f"{date_s}  |  {now.strftime('%H:%M:%S')}",
           font=font(24, False), fill=(155,170,188,255))

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="JPEG", quality=93, optimize=True)
    out.seek(0)
    return out, mood, pct

def telegram(method, payload=None, files=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    r = requests.post(url, data=payload or {}, files=files, timeout=60)
    r.raise_for_status()
    return r.json()


def unpin_all():
    try:
        telegram("unpinAllChatMessages", {"chat_id": CHAT_ID})
    except Exception as e:
        print("Unpin warning:", e)


def send_card(image_bytes, caption):
    result = telegram(
        "sendPhoto",
        {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
        {"photo": ("price.jpg", image_bytes, "image/jpeg")},
    )
    return result["result"]["message_id"]


def pin_message(message_id):
    try:
        telegram("pinChatMessage", {
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "disable_notification": True,
        })
    except Exception as e:
        print("Pin warning:", e)


def caption_for(title, price, pct, mood, now):
    arrow = "🟢⬆️" if pct > 0 else "🔴⬇️" if pct < 0 else "⚪➡️"
    mood_emoji = "🔴" if pct > 0 else "🟢" if pct < 0 else "⚪"
    try:
        import jdatetime
        jnow = jdatetime.datetime.fromgregorian(datetime=now.replace(tzinfo=None))
        date_s = jnow.strftime("%Y/%m/%d")
    except Exception:
        date_s = now.strftime("%Y/%m/%d")
    return (
        f"<b>{arrow} {title}</b>\n\n"
        f"💰 <b>{fmt_price(price)} تومان</b>\n"
        f"📊 تغییر: <b>{fmt_pct(pct)}</b>\n"
        f"{mood_emoji} <b>{mood}</b>\n\n"
        f"🕐 {date_s} | {now.strftime('%H:%M:%S')}"
    )


def main():
    ensure_fonts()
    usd, gold = fetch_prices()
    now = datetime.now(TEHRAN)
    now_iso = now.isoformat()

    history = load_history()
    old_usd = previous_price(history, "usd", usd)
    old_gold = previous_price(history, "gold", gold)

    # Build the chart using the old points + current point.
    usd_points = history_prices(history, "usd") + [float(usd)]
    gold_points = history_prices(history, "gold") + [float(gold)]

    usd_img, usd_mood, usd_pct = build_card(
        "دلار آمریکا", usd, old_usd, usd_points, False, now
    )
    gold_img, gold_mood, gold_pct = build_card(
        "طلای ۱۸ عیار", gold, old_gold, gold_points, True, now
    )

    # Save history after calculating changes, so the comparison is always with the previous run.
    add_history(history, "usd", usd, now_iso)
    add_history(history, "gold", gold, now_iso)
    save_history(history)

    # One pinned pair per run: old pins are removed, then the two fresh cards are pinned.
    unpin_all()

    usd_id = send_card(
        usd_img,
        caption_for("دلار آمریکا 🇺🇸", usd, usd_pct, usd_mood, now)
    )
    gold_id = send_card(
        gold_img,
        caption_for("طلای ۱۸ عیار 🪙", gold, gold_pct, gold_mood, now)
    )

    pin_message(usd_id)
    pin_message(gold_id)

    print("DONE:", usd_id, gold_id)


if __name__ == "__main__":
    main()
