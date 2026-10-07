import os, re, json, requests, jdatetime
from bs4 import BeautifulSoup
from datetime import datetime
from zoneinfo import ZoneInfo

BOT_TOKEN=os.environ["BOT_TOKEN"]; CHAT_ID=os.environ["CHAT_ID"]; ADMIN_ID=os.environ.get("ADMIN_ID","").strip()
USD_URL="https://gem.tgju.org/profile/price_dollar_rl"; GOLD_URL="https://gem.tgju.org/profile/geram18"
STATE_FILE="bot_state.json"; HISTORY_FILE="history.json"; SETTINGS_FILE="schedule.json"; OFFSET_FILE="telegram_offset.json"
TEHRAN=ZoneInfo("Asia/Tehran")
HEADERS={"User-Agent":"Mozilla/5.0 Chrome/130 Safari/537.36","Cache-Control":"no-cache","Pragma":"no-cache"}
DEFAULT={"enabled":True,"interval_minutes":240,"last_sent_at":None}

def norm(s): return s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩","01234567890123456789"))
def load(p,d):
    try:
        with open(p,encoding="utf-8") as f:return json.load(f)
    except:return d.copy() if isinstance(d,dict) else d
def save(p,d):
    with open(p,"w",encoding="utf-8") as f:json.dump(d,f,ensure_ascii=False,indent=2)
def tg(method,data=None):
    r=requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",data=data or {},timeout=20); r.raise_for_status()
    x=r.json()
    if not x.get("ok"): raise RuntimeError(x)
    return x["result"]
def send(text,chat=CHAT_ID,markup=None):
    d={"chat_id":chat,"text":text,"parse_mode":"Markdown"}
    if markup:d["reply_markup"]=json.dumps(markup,ensure_ascii=False)
    return tg("sendMessage",d)["message_id"]
def price(url):
    r=requests.get(url,params={"_":int(datetime.now().timestamp()*1000)},headers=HEADERS,timeout=20); r.raise_for_status()
    s=BeautifulSoup(r.text,"html.parser")
    for sel in ["[data-price]",'[itemprop="price"]','[data-field="price"]']:
        e=s.select_one(sel)
        if e:
            raw=norm(e.get("data-price") or e.get("content") or e.get_text(" ",strip=True))
            m=re.findall(r"\d[\d,]*",raw)
            if m:return float(m[0].replace(",",""))/10
    m=re.search(r"(?:نرخ فعلی|Last)\s*[:：]?\s*([\d,]+)",norm(s.get_text(" ",strip=True)),re.I)
    if m:return float(m.group(1).replace(",",""))/10
    raise RuntimeError("current price not found")
def pin(mid):
    try:tg("pinChatMessage",{"chat_id":CHAT_ID,"message_id":mid,"disable_notification":True})
    except:pass
def unpin(mid):
    try:tg("unpinChatMessage",{"chat_id":CHAT_ID,"message_id":mid})
    except:pass
def msg(title,p,ch):
    now=datetime.now(TEHRAN); j=jdatetime.datetime.fromgregorian(datetime=now)
    a,c,ph=("⬆️","🟢","🔴 ||بگا رفتین||") if ch>0 else (("⬇️","🔴","🟢 ||فکر کنم رفتن||") if ch<0 else ("➡️","⚪","⚪ ||بدون تغییر||"))
    return f"{a} {title}\n\n💰 **{p:,.0f} تومان**\n📊 تغییر: **{c}{ch:+.2f}%**\n{ph}\n\n🕐 {j.strftime('%Y/%m/%d')} | {now.strftime('%H:%M:%S')}"
def fmt(n):
    n=int(n)
    return f"هر {n} دقیقه" if n<60 else (f"هر {n//60} ساعت" if n%60==0 else f"هر {n//60} ساعت و {n%60} دقیقه")
def admin(uid):return bool(ADMIN_ID) and str(uid)==ADMIN_ID
def panel():
    return {"inline_keyboard":[[{"text":"⏱ تغییر زمان","callback_data":"schedule"}],[{"text":"▶️ ارسال فوری","callback_data":"now"},{"text":"⏸ توقف","callback_data":"pause"}],[{"text":"▶️ فعال‌سازی","callback_data":"resume"},{"text":"📊 وضعیت","callback_data":"status"}]]}
def schedule():
    return {"inline_keyboard":[[{"text":"15 دقیقه","callback_data":"set:15"},{"text":"30 دقیقه","callback_data":"set:30"}],[{"text":"1 ساعت","callback_data":"set:60"},{"text":"2 ساعت","callback_data":"set:120"}],[{"text":"4 ساعت","callback_data":"set:240"},{"text":"6 ساعت","callback_data":"set:360"}],[{"text":"12 ساعت","callback_data":"set:720"},{"text":"24 ساعت","callback_data":"set:1440"}],[{"text":"🔢 زمان دلخواه","callback_data":"custom"}],[{"text":"⬅️ برگشت","callback_data":"panel"}]]}
def status(s):return f"⚙️ **پنل مدیریت**\n\nوضعیت: {'🟢 فعال' if s.get('enabled',True) else '⏸ متوقف'}\nزمان‌بندی: **{fmt(s.get('interval_minutes',240))}**"
def updates(s):
    if not ADMIN_ID:return s
    off=load(OFFSET_FILE,{"offset":0}).get("offset",0)
    us=tg("getUpdates",{"offset":off,"timeout":1,"allowed_updates":json.dumps(["message","callback_query"])})
    mx=off-1
    for u in us:
        mx=max(mx,u["update_id"])
        if "callback_query" in u:
            c=u["callback_query"]; uid=c.get("from",{}).get("id"); data=c.get("data","")
            try:tg("answerCallbackQuery",{"callback_query_id":c["id"]})
            except:pass
            if not admin(uid):continue
            chat=uid
            if data=="panel":send(status(s),chat,panel())
            elif data=="schedule":send("⏱ فاصله ارسال را انتخاب کن:",chat,schedule())
            elif data.startswith("set:"):
                s["interval_minutes"]=int(data[4:]);s["enabled"]=True;save(SETTINGS_FILE,s);send("✅ زمان‌بندی شد: **"+fmt(s["interval_minutes"])+"**",chat,panel())
            elif data=="custom":send("🔢 زمان دلخواه را بفرست، مثلاً:\n`/settime 90`\nیعنی هر 90 دقیقه.",chat)
            elif data=="pause":s["enabled"]=False;save(SETTINGS_FILE,s);send("⏸ ارسال خودکار متوقف شد.",chat,panel())
            elif data=="resume":s["enabled"]=True;save(SETTINGS_FILE,s);send("▶️ ارسال خودکار فعال شد.",chat,panel())
            elif data=="status":send(status(s),chat,panel())
            elif data=="now":s["_send_now"]=True;save(SETTINGS_FILE,s);send("▶️ ارسال فوری ثبت شد.",chat,panel())
        else:
            m=u.get("message",{}); uid=m.get("from",{}).get("id"); text=(m.get("text") or "").strip(); chat=m.get("chat",{}).get("id")
            if not admin(uid):continue
            if text=="/admin":send(status(s),chat,panel())
            elif text.startswith("/settime "):
                try:
                    n=int(text.split()[1])
                    if not 5<=n<=10080:raise ValueError
                    s["interval_minutes"]=n;s["enabled"]=True;save(SETTINGS_FILE,s);send("✅ زمان‌بندی شد: **"+fmt(n)+"**",chat,panel())
                except:send("❌ عدد باید بین 5 تا 10080 دقیقه باشد. مثال: `/settime 90`",chat)
            elif text=="/pause":s["enabled"]=False;save(SETTINGS_FILE,s);send("⏸ متوقف شد.",chat,panel())
            elif text=="/resume":s["enabled"]=True;save(SETTINGS_FILE,s);send("▶️ فعال شد.",chat,panel())
            elif text=="/send":s["_send_now"]=True;save(SETTINGS_FILE,s);send("▶️ ارسال فوری ثبت شد.",chat)
            elif text=="/status":send(status(s),chat,panel())
    if mx>=off:save(OFFSET_FILE,{"offset":mx+1})
    return s
def do_send():
    u,g=price(USD_URL),price(GOLD_URL); h=load(HISTORY_FILE,{}); st=load(STATE_FILE,{"pinned_message_ids":[]})
    uc=0 if not h.get("usd") else (u-h["usd"])/h["usd"]*100; gc=0 if not h.get("gold") else (g-h["gold"])/h["gold"]*100
    for mid in st.get("pinned_message_ids",[]):unpin(mid)
    a=send(msg("دلار آزاد 💵",u,uc));b=send(msg("طلای ۱۸ عیار 🪙",g,gc));pin(a);pin(b)
    now=datetime.now(TEHRAN).isoformat();save(HISTORY_FILE,{"usd":u,"gold":g,"updated_at":now});save(STATE_FILE,{"pinned_message_ids":[a,b],"updated_at":now})
def main():
    s=updates(load(SETTINGS_FILE,DEFAULT))
    go=s.pop("_send_now",False)
    if not go and s.get("enabled",True):
        last=s.get("last_sent_at")
        go=not last or (datetime.now(TEHRAN)-datetime.fromisoformat(last)).total_seconds()>=s.get("interval_minutes",240)*60
    if go:
        do_send();s["last_sent_at"]=datetime.now(TEHRAN).isoformat()
    save(SETTINGS_FILE,s)
if __name__=="__main__":main()
