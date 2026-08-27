"""
BTC & Gold Discord Bot — Full Featured
โฟกัส Bitcoin + ทองคำ พร้อมข้อมูลเทรดครบชุด

Data sources (ฟรีทั้งหมด):
- CoinGecko: BTC price, PAXG price (gold proxy)
- Alternative.me: Fear & Greed Index
- Yahoo Finance: DXY, 10Y yield, GLD, TIP (real yields proxy)
- Binance Futures: BTC funding rate, open interest
- RSS: News from 9 sources
- Local JSON: Economic calendar

Modes:
- full   : สรุปเต็ม (รอบใหญ่ 2x/วัน)
- watch  : ตรวจ BREAKING + price alarm (ทุก 30 นาที)
- weekly : สรุปสัปดาห์ (ทุกอาทิตย์ 20:00)
"""

import os
import json
import time
import argparse
import feedparser
import requests
import anthropic
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# CONFIG
# ============================================================

DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

STATE_FILE = Path("state.json")
CALENDAR_FILE = Path("economic_events.json")

BTC_ALERT_THRESHOLD = 3.0
GOLD_ALERT_THRESHOLD = 1.5
ALERT_COOLDOWN_MINUTES = 60
PRICE_LOOKBACK_HOURS = 1.0

FEEDS = {
    "Reuters Business":     "https://feeds.reuters.com/reuters/businessNews",
    "Reuters Markets":      "https://feeds.reuters.com/reuters/marketsNews",
    "Reuters World":        "https://feeds.reuters.com/Reuters/worldNews",
    "CNBC Markets":         "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "CNBC Economy":         "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "MarketWatch":          "https://feeds.marketwatch.com/marketwatch/topstories/",
    "Yahoo Finance":        "https://finance.yahoo.com/news/rssindex",
    "Investing.com Econ":   "https://www.investing.com/rss/news_285.rss",
    "Kitco Gold":           "https://www.kitco.com/rss/KitcoNews.xml",
    "CoinDesk":             "https://www.coindesk.com/arc/outboundfeeds/rss/",
}

KEYWORDS = [
    "fed", "fomc", "powell", "warsh", "rate", "inflation", "cpi", "ppi", "pce",
    "payroll", "nfp", "gdp", "unemployment", "jobs", "yield", "treasury",
    "dollar", "dxy",
    "gold", "bullion", "kitco",
    "bitcoin", "btc", "spot etf", "sec ", "blackrock", "microstrategy",
    "whale", "halving", "capitulation",
    "middle east", "war", "iran", "israel", "ukraine", "russia", "china",
    "taiwan", "missile", "strike", "attack", "ceasefire",
    "trump", "tariff", "sanctions", "executive order", "shutdown",
]

BREAKING_KEYWORDS = [
    "rate cut", "rate hike", "emergency rate", "emergency meeting",
    "fomc statement", "fomc decision", "unscheduled",
    "powell speaks", "powell testimony", "warsh testimony",
    "cpi report", "cpi rose", "cpi came", "cpi surprise",
    "ppi report", "ppi rose", "pce report",
    "nfp report", "non-farm payrolls",
    "jobless claims surprise", "gdp report", "fomc minutes",
    "airstrike", "missile strike", "invasion", "war declared",
    "iran attack", "israel strike", "taiwan invasion", "ceasefire",
    "us strike", "attack on",
    "tariff announce", "new tariff", "trump tariff",
    "sanctions imposed", "executive order signed",
    "government shutdown", "debt ceiling deal",
    "spot bitcoin etf", "sec approval", "sec rejects",
    "bitcoin halving", "blackrock bitcoin", "microstrategy bought",
    "microstrategy sold", "mt gox distribution",
    "gold record", "gold all-time high", "central bank gold",
    "china gold reserves", "russia gold reserves",
]


# ============================================================
# STATE
# ============================================================

def load_state():
    default = {
        "posts": [], "posted_urls": [], "price_history": [], "last_alert": {},
    }
    if not STATE_FILE.exists():
        return default
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        for k, v in default.items():
            state.setdefault(k, v)
        return state
    except Exception as e:
        print(f"[warn] load_state: {e}")
        return default


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ============================================================
# PRICE FETCHING - BTC + Gold (CoinGecko)
# ============================================================

def fetch_prices():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin,pax-gold",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            },
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        return {
            "btc": {
                "price": d["bitcoin"]["usd"],
                "change_24h": d["bitcoin"].get("usd_24h_change", 0),
                "volume_24h": d["bitcoin"].get("usd_24h_vol", 0),
            },
            "gold": {
                "price": d["pax-gold"]["usd"],
                "change_24h": d["pax-gold"].get("usd_24h_change", 0),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        print(f"[error] fetch_prices: {e}")
        return None


def record_price(state, prices):
    if not prices:
        return
    state["price_history"].append(prices)
    state["price_history"] = state["price_history"][-48:]  # 24 hrs at 30 min


def compute_price_change(prices_now, price_history, hours=1.0):
    if not prices_now or not price_history:
        return None, None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    ref = None
    for p in price_history:
        try:
            p_time = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
            if p_time <= cutoff:
                ref = p
        except Exception:
            continue
    if not ref and len(price_history) >= 2:
        ref = price_history[0]
    if not ref:
        return None, None
    try:
        btc = (prices_now["btc"]["price"] / ref["btc"]["price"] - 1) * 100
        gold = (prices_now["gold"]["price"] / ref["gold"]["price"] - 1) * 100
        return btc, gold
    except Exception:
        return None, None


# ============================================================
# FEAR & GREED INDEX (Alternative.me - ฟรี)
# ============================================================

def fetch_fear_greed():
    try:
        r = requests.get(
            "https://api.alternative.me/fng/",
            params={"limit": 2}, timeout=10,
        )
        r.raise_for_status()
        data = r.json()["data"]
        today = data[0]
        yesterday = data[1] if len(data) > 1 else None
        return {
            "value": int(today["value"]),
            "label": today["value_classification"],
            "yesterday": int(yesterday["value"]) if yesterday else None,
        }
    except Exception as e:
        print(f"[warn] fear_greed: {e}")
        return None


# ============================================================
# MACRO - DXY, 10Y Yield, GLD, TIP (Yahoo Finance query API)
# ============================================================

def _yahoo_quote(symbol):
    """ดึงราคาล่าสุด + % change 1d จาก Yahoo Finance"""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        r.raise_for_status()
        result = r.json()["chart"]["result"][0]
        meta = result["meta"]
        current = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev = meta.get("chartPreviousClose")
        change_pct = ((current - prev) / prev * 100) if prev else 0
        return {"price": current, "change_1d": change_pct}
    except Exception as e:
        print(f"[warn] yahoo {symbol}: {e}")
        return None


def fetch_macro():
    """DXY (Dollar Index), 10Y Yield, GLD ETF, TIP ETF"""
    return {
        "dxy": _yahoo_quote("DX-Y.NYB"),   # US Dollar Index
        "yield_10y": _yahoo_quote("^TNX"),  # 10-Year Treasury Yield
        "gld": _yahoo_quote("GLD"),         # SPDR Gold Shares ETF
        "tip": _yahoo_quote("TIP"),         # iShares TIPS (real yield proxy)
    }


# ============================================================
# BTC PERP DATA - Binance Futures (ฟรี)
# ============================================================

def fetch_btc_perp():
    """Funding rate + Open Interest จาก Binance USDT-M Futures"""
    try:
        # Funding rate + mark price
        r1 = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": "BTCUSDT"}, timeout=10,
        )
        r1.raise_for_status()
        premium = r1.json()

        # Open Interest
        r2 = requests.get(
            "https://fapi.binance.com/fapi/v1/openInterest",
            params={"symbol": "BTCUSDT"}, timeout=10,
        )
        r2.raise_for_status()
        oi = r2.json()

        return {
            "funding_rate": float(premium["lastFundingRate"]) * 100,  # % per 8h
            "mark_price": float(premium["markPrice"]),
            "open_interest": float(oi["openInterest"]),
        }
    except Exception as e:
        print(f"[warn] binance perp: {e}")
        return None


# ============================================================
# TECHNICAL LEVELS - คำนวณ S/R จาก price history
# ============================================================

def compute_technical_levels(price_history):
    """คำนวณ Support/Resistance zones จาก price data 24 ชม.ล่าสุด"""
    if len(price_history) < 3:
        return None
    btc_prices = [p["btc"]["price"] for p in price_history]
    gold_prices = [p["gold"]["price"] for p in price_history]

    def levels(prices):
        current = prices[-1]
        high = max(prices)
        low = min(prices)
        mid = (high + low) / 2
        return {
            "current": current,
            "recent_high": high,
            "recent_low": low,
            "mid": mid,
            "range_pct": ((high - low) / low * 100) if low else 0,
        }

    return {
        "btc": levels(btc_prices),
        "gold": levels(gold_prices),
    }


# ============================================================
# ECONOMIC CALENDAR (Local JSON file - user editable)
# ============================================================

def load_calendar():
    """โหลด economic events จากไฟล์ JSON"""
    if not CALENDAR_FILE.exists():
        return []
    try:
        return json.loads(CALENDAR_FILE.read_text(encoding="utf-8")).get("events", [])
    except Exception as e:
        print(f"[warn] calendar: {e}")
        return []


def upcoming_events(events, hours=72):
    """กรอง events ที่จะเกิดใน X ชม.ข้างหน้า"""
    now = datetime.now(timezone(timedelta(hours=7)))
    cutoff = now + timedelta(hours=hours)
    upcoming = []
    for e in events:
        try:
            dt = datetime.fromisoformat(f"{e['date']}T{e['time']}:00+07:00")
            if now <= dt <= cutoff:
                upcoming.append({**e, "datetime": dt})
        except Exception:
            continue
    upcoming.sort(key=lambda x: x["datetime"])
    return upcoming[:6]  # เอาแค่ 6 อันแรก


# ============================================================
# NEWS FETCHING
# ============================================================

def fetch_headlines(hours_back=8, keywords=None):
    if keywords is None:
        keywords = KEYWORDS
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    items = []
    for source, url in FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:25]:
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if not pub:
                    continue
                pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()[:300]
                text = (title + " " + summary).lower()
                if any(k in text for k in keywords):
                    items.append({
                        "source": source, "title": title,
                        "summary": summary, "link": entry.get("link", ""),
                        "published": pub_dt.isoformat(),
                    })
        except Exception as e:
            print(f"[warn] {source}: {e}")
    return items


def filter_new(items, posted_urls):
    posted = set(posted_urls)
    return [i for i in items if i["link"] and i["link"] not in posted]


# ============================================================
# HEADER BUILDER - ราคา + macro + sentiment (ใช้ทุกโพส)
# ============================================================

def build_header(prices, fg, macro, perp, levels, events):
    """สร้าง header block รวมข้อมูลทั้งหมด"""
    lines = []

    # ราคา (ใช้ ## ให้ตัวใหญ่ขึ้น)
    lines.append("## 💰 ราคาปัจจุบัน")
    lines.append(f"• ₿ BTC: **${prices['btc']['price']:,.0f}** "
                 f"({prices['btc']['change_24h']:+.2f}% 24h)")
    lines.append(f"• 🥇 ทอง: **${prices['gold']['price']:,.2f}** "
                 f"({prices['gold']['change_24h']:+.2f}% 24h)")

    # Macro
    if macro:
        macro_parts = []
        if macro.get("dxy"):
            macro_parts.append(f"💵 ดอลลาร์ {macro['dxy']['price']:.2f} "
                               f"({macro['dxy']['change_1d']:+.2f}%)")
        if macro.get("yield_10y"):
            macro_parts.append(f"🏦 พันธบัตร 10 ปี {macro['yield_10y']['price']:.2f}%")
        if macro_parts:
            lines.append("• " + " | ".join(macro_parts))

    # Sentiment
    sentiment_parts = []
    if fg:
        sentiment_parts.append(f"😱 อารมณ์ตลาด BTC: **{fg['value']}** ({fg['label']})")
    if perp:
        funding_emoji = "🔴" if perp["funding_rate"] > 0.05 else "🟢" if perp["funding_rate"] < -0.02 else "⚪"
        sentiment_parts.append(f"{funding_emoji} คน long/short BTC: {perp['funding_rate']:+.3f}%")
    if sentiment_parts:
        lines.append("")
        lines.append("• " + " | ".join(sentiment_parts))

    lines.append("\n---\n")
    return "\n".join(lines)


# ============================================================
# AI SUMMARIZATION
# ============================================================

def ai_client():
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _extract_text(msg):
    parts = [b.text for b in msg.content if getattr(b, "text", None)]
    if not parts:
        raise RuntimeError(f"empty response: {msg.content}")
    return "\n".join(parts).strip()


def ai_summarize_brief(items, prices, fg, macro, perp, levels, previous_posts):
    """สรุปเต็มสำหรับรอบใหญ่"""
    client = ai_client()
    news_txt = "\n\n".join(
        f"[{i['source']}] {i['title']}\n{i['summary']}\nURL: {i['link']}"
        for i in items
    )
    context = ""
    if previous_posts:
        context = "\n\n=== โพสก่อนหน้าของบอท ===\n"
        for p in previous_posts[-4:]:
            context += f"\n[{p['time']}]\n{p['content'][:500]}\n"

    # Data block for AI
    data_ctx = f"""
=== ข้อมูลราคา + macro + sentiment ===
- BTC ${prices['btc']['price']:,.0f} ({prices['btc']['change_24h']:+.2f}% 24h)
- Gold ${prices['gold']['price']:,.2f} ({prices['gold']['change_24h']:+.2f}% 24h)
- F&G Index: {fg['value']} ({fg['label']})""" if fg else ""

    if macro:
        if macro.get("dxy"):
            data_ctx += f"\n- DXY: {macro['dxy']['price']:.2f} ({macro['dxy']['change_1d']:+.2f}%)"
        if macro.get("yield_10y"):
            data_ctx += f"\n- 10Y Yield: {macro['yield_10y']['price']:.2f}%"
        if macro.get("gld"):
            data_ctx += f"\n- GLD ETF: ${macro['gld']['price']:.2f} ({macro['gld']['change_1d']:+.2f}%)"

    if perp:
        data_ctx += f"\n- BTC Perp funding: {perp['funding_rate']:+.3f}%/8h"

    # (Key Levels removed - user preference)

    prompt = f"""คุณคือนักวิเคราะห์ตลาด BTC และทองคำ สำหรับกลุ่ม Discord trader

{data_ctx}
{context}

=== ข่าวใหม่ 8 ชม.ล่าสุด ===
{news_txt}

**กติกาสำคัญ**:

1. **ห้ามใส่หัวข้อเปิด/intro** เช่น "📊 BTC & Gold Market Update" ห้ามเด็ดขาด — เพราะบอทมีชื่อโพสอยู่แล้ว

2. **เลือกเฉพาะข่าวที่กระทบ BTC หรือทอง** — ตัดข่าวที่ไม่กระทบทิ้ง

3. **สถานะข่าว**:
   - 🔥 BREAKING — market-moving ระดับสูง
   - 🆕 NEW — ข่าวใหม่ยังไม่เคยพูด
   - 🔄 UPDATE — เรื่องเก่าที่พลิก/มีตัวเลขใหม่

4. **Format แต่ละข่าว** (หัวข้อใช้ ### เพื่อให้ตัวใหญ่ อ่านง่าย):
```
### [emoji status] หัวข้อสั้น
รายละเอียด/ตัวเลข (2-3 บรรทัด)
💡 กระทบ: 🥇 [ทอง + level] | ₿ [BTC + level]
```

5. **UPDATE format**:
```
### 🔄 อัปเดต — [หัวข้อ]
📌 เดิม: ...
🆕 ล่าสุด: ...
💡 กระทบ: ...
```

6. **ต้องเลือกข่าวเด่นสุด 3-4 อัน** (ห้ามต่ำกว่า 3 ถ้าข่าวมีให้เลือก — ถ้ามีจริงๆ แค่ 1-2 อันที่กระทบ ค่อยเอาเท่านั้น)

7. **ใช้ข้อมูล macro/sentiment ที่ให้มา** ประกอบการวิเคราะห์

8. **ปิดท้ายบังคับ 3 บรรทัด** (ห้ามลืม):
```
## 🎯 สรุป
📈 Trend: BTC [uptrend/sideways/downtrend] | Gold [uptrend/sideways/downtrend]
⚠️ Risk: [ระดับ + เหตุผลสั้น]
🎯 ภาพรวม: [สรุปทิศทาง 1 บรรทัด]
```

9. **ห้ามเกิน 1600 ตัวอักษร** (สำคัญ! Discord จำกัดความยาว)
10. Level ต้องเป็นตัวเลขจริง
11. **ตรวจสอบก่อนส่ง**: ต้องจบด้วย 🎯 ภาพรวม — ห้ามค้างกลางประโยค
12. เขียนกระชับ ตัดคำเยิ่นเย้อออก — คุณภาพต้องดี แต่สั้น

ตอบเฉพาะเนื้อหา ห้ามใส่คำนำ ห้ามใส่หัวข้อภาพรวมด้านบน เริ่มด้วย ### ข่าวแรกเลย"""

    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_text(msg)


def ai_breaking_alert(breaking_items, prices, fg, previous_posts):
    """สรุปข่าว BREAKING"""
    client = ai_client()
    news_txt = "\n\n".join(
        f"[{i['source']}] {i['title']}\n{i['summary']}\n"
        f"เผยแพร่: {i['published']}"
        for i in breaking_items
    )
    context = ""
    if previous_posts:
        context = "\n\n=== โพสล่าสุดของบอท ===\n"
        for p in previous_posts[-3:]:
            context += f"\n{p['content'][:400]}\n---\n"

    data_ctx = f"BTC ${prices['btc']['price']:,.0f} | Gold ${prices['gold']['price']:,.2f}"
    if fg:
        data_ctx += f" | F&G {fg['value']} ({fg['label']})"

    prompt = f"""ตลาด BTC/ทอง — มีข่าว BREAKING เข้ามา

{data_ctx}
{context}

=== ข่าว BREAKING ===
{news_txt}

**เลือก 1 จาก 3 กรณี**:

**A. SKIP** → ตอบ "SKIP" อย่างเดียว เมื่อ:
- ไม่กระทบ BTC/ทอง
- เคยพูดในโพสก่อนและไม่มีอะไรใหม่

**B. 🔄 UPDATE** → เมื่อเรื่องเดิมแต่พลิก/มีตัวเลขใหม่:
```
## 🔄 อัปเดต — [หัวข้อ]
📌 เดิม: ...
🆕 ล่าสุด: ...

💡 กระทบ BTC: [ทิศทาง + level]
💡 กระทบทอง: [ทิศทาง + level]
🎯 [ต่อไป]
```

**C. 🔥 BREAKING** → เมื่อข่าวใหม่ล้วน:
```
## 🔥 BREAKING — [หัวข้อ]
[รายละเอียด + ตัวเลข]

💡 กระทบ BTC: [ทิศทาง + level]
💡 กระทบทอง: [ทิศทาง + level]
🎯 [ต่อไป]
```

- ห้ามเกิน 1500 ตัวอักษร
- ถ้ากระทบแค่ตัวเดียว = ระบุแค่ตัวนั้น

ตัวอย่างการตัดสิน:
- โพสก่อน: "Trump ประกาศบุกอิหร่าน" | ใหม่: "Trump ยกเลิก" → UPDATE
- โพสก่อน: "Trump ประกาศบุกอิหร่าน" | ใหม่: "นักวิเคราะห์วิจารณ์ Trump" → SKIP

ตอบเฉพาะเนื้อหา หรือ SKIP"""

    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_text(msg)


def ai_price_alert(prices, btc_change, gold_change, context_news, fg, perp):
    """สรุป price alert + ข่าวประกอบ"""
    client = ai_client()
    news_txt = ""
    if context_news:
        news_txt = "=== ข่าว 60 นาที (อาจเป็นสาเหตุ) ===\n"
        news_txt += "\n\n".join(
            f"[{i['source']}] {i['title']}\n{i['summary'][:250]}"
            for i in context_news[:5]
        )
    else:
        news_txt = "=== ไม่พบข่าวที่เกี่ยวข้อง ==="

    triggered = []
    if btc_change is not None and abs(btc_change) >= BTC_ALERT_THRESHOLD:
        triggered.append(f"BTC {btc_change:+.2f}%")
    if gold_change is not None and abs(gold_change) >= GOLD_ALERT_THRESHOLD:
        triggered.append(f"ทอง {gold_change:+.2f}%")

    sentiment = ""
    if fg:
        sentiment += f"F&G: {fg['value']} ({fg['label']})"
    if perp:
        sentiment += f" | Funding: {perp['funding_rate']:+.3f}%"

    prompt = f"""ราคาผิดปกติ — เขียน alert ด่วน

BTC ${prices['btc']['price']:,.0f} | Gold ${prices['gold']['price']:,.2f}
{sentiment}

Trigger: {', '.join(triggered)} ใน 1 ชม.

{news_txt}

**Format**:
```
## 🚨 [BTC/GOLD/BOTH] ALERT

📈/📉 [asset] $[ราคา] ([%change] ใน 1 ชม.)

### 📰 น่าจะเป็นสาเหตุ
• [ข่าว 1-2 อัน]  (ถ้ามี)
หรือ
### 📰 ไม่พบข่าวชัดเจน
• [liquidation/whale/technical]  (ถ้าไม่มีข่าว)

### 💡 แนวโน้ม
• แนวรับ/ต้าน: [ตัวเลข]
• ถ้าหลุด → ...
• ถ้ายืน → ...

🎯 กระทบตัวอื่น: [ถ้ามี]
```

- ห้ามเกิน 1500 ตัวอักษร
- ซื่อสัตย์: ไม่มีข่าว = บอกว่าไม่มี
- ใช้ F&G + Funding ประกอบการวิเคราะห์

ตอบเฉพาะเนื้อหา"""

    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_text(msg)


def ai_weekly_wrap(prices, fg, macro, perp, week_posts):
    """สรุปสัปดาห์ทุกวันอาทิตย์"""
    client = ai_client()
    week_summary = "\n\n".join(
        f"[{p['time']}]\n{p['content'][:400]}"
        for p in week_posts[-14:]
    )

    prompt = f"""เขียน Weekly Wrap สำหรับ BTC + ทองคำ

=== ราคา ณ สิ้นสัปดาห์ ===
- BTC ${prices['btc']['price']:,.0f} ({prices['btc']['change_24h']:+.2f}% 24h)
- Gold ${prices['gold']['price']:,.2f} ({prices['gold']['change_24h']:+.2f}% 24h)
- F&G: {fg['value']} ({fg['label']}) if fg else 'N/A'

=== โพสสัปดาห์นี้ ===
{week_summary}

**Format**:
```
📊 Weekly Wrap — สัปดาห์ที่ผ่านมา

📈 ผลตอบแทน:
• BTC: [%สัปดาห์ + ทิศทาง]
• Gold: [%สัปดาห์ + ทิศทาง]

🎯 ประเด็นสำคัญ (3 อัน):
1. [ประเด็น 1]
2. [ประเด็น 2]
3. [ประเด็น 3]

📅 สัปดาห์หน้าจับตา:
• [event สำคัญ]

🔮 Outlook สัปดาห์หน้า:
• BTC: [ทิศทาง + level]
• Gold: [ทิศทาง + level]
```

ห้ามเกิน 2000 ตัวอักษร
ตอบเฉพาะเนื้อหา"""

    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_text(msg)


# ============================================================
# DISCORD
# ============================================================

MAX_CONTENT = 1950  # Discord content limit 2000 chars — เผื่อ 50 chars safety


def _post_single(content_text):
    """ยิงข้อความ 1 ก้อนเข้า Discord แบบ content (font ปกติ ใหญ่)"""
    payload = {
        "username": "ยาม AI เฝ้าตลาด",
        "content": content_text[:MAX_CONTENT],
        "allowed_mentions": {"parse": []},
    }
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
    r.raise_for_status()
    print(f"[ok] posted chunk ({len(content_text)} chars)")


def _smart_split(text, max_len=MAX_CONTENT):
    """แบ่งข้อความเป็นชิ้นๆ ตัดตรง section header ก่อน"""
    if len(text) <= max_len:
        return [text]

    # หา breakpoint ตามลำดับความสำคัญ
    breakpoints = ["\n## ", "\n### ", "\n---\n", "\n\n"]
    parts = []
    remaining = text

    while len(remaining) > max_len:
        cut_pos = -1
        # หา breakpoint ที่ใกล้ max_len ที่สุด (ไม่เกิน)
        for bp in breakpoints:
            search_end = max_len
            pos = remaining.rfind(bp, 0, search_end)
            if pos > cut_pos:
                cut_pos = pos

        if cut_pos <= 0:
            cut_pos = max_len

        parts.append(remaining[:cut_pos].rstrip())
        remaining = remaining[cut_pos:].lstrip()

    if remaining:
        parts.append(remaining)
    return parts


def post_discord(summary, title_prefix="📊 อัปเดต BTC & ทอง", color=None):
    """
    ส่งเป็น content ปกติ (font ใหญ่) — ถ้ายาวเกิน split เป็นหลายข้อความ
    color ไม่ได้ใช้แล้ว (เก็บ argument ไว้ compatible)
    """
    now_th = datetime.now(timezone(timedelta(hours=7))).strftime("%d %b %Y %H:%M")
    # สร้างข้อความเต็ม
    full_text = f"# {title_prefix} | {now_th}\n\n{summary}\n\n-# ยาม AI • ไม่ใช่คำแนะนำการลงทุน"

    chunks = _smart_split(full_text)
    for i, chunk in enumerate(chunks):
        # ถ้าหลาย chunk เพิ่ม (ต่อ) กำกับ
        if len(chunks) > 1 and i > 0:
            chunk = f"*(ต่อ {i+1}/{len(chunks)})*\n\n" + chunk
        _post_single(chunk)
        if i < len(chunks) - 1:
            time.sleep(0.7)  # กัน rate limit
    print(f"[ok] posted total {len(chunks)} message(s)")


# ============================================================
# MODE: FULL BRIEF
# ============================================================

def mode_full_brief():
    print("[mode] full_brief")
    state = load_state()

    # Fetch all data
    prices = fetch_prices()
    if not prices:
        print("[error] no prices — abort")
        return

    fg = fetch_fear_greed()
    macro = fetch_macro()
    perp = fetch_btc_perp()
    levels = None    # ตัด Key Levels ออก
    events = []      # ตัด Calendar ออก

    items = fetch_headlines(hours_back=8, keywords=KEYWORDS)
    new_items = filter_new(items, state["posted_urls"])
    print(f"[info] {len(new_items)} new items")

    if not new_items:
        print("[info] no news — skip")
        record_price(state, prices)
        save_state(state)
        return

    summary = ai_summarize_brief(new_items, prices, fg, macro, perp, levels, state["posts"])

    if "ไม่มีข่าวใหม่ที่สำคัญ" in summary or len(summary) < 80:
        print("[info] AI: nothing important")
        state["posted_urls"] = (state["posted_urls"] + [i["link"] for i in new_items])[-500:]
        record_price(state, prices)
        save_state(state)
        return

    header = build_header(prices, fg, macro, perp, levels, events)
    body = header + summary
    color = 15158332 if "🔥 BREAKING" in summary else 15844367
    post_discord(body, color=color)

    now_iso = datetime.now(timezone(timedelta(hours=7))).isoformat()
    state["posts"].append({"time": now_iso, "content": summary})
    state["posts"] = state["posts"][-8:]
    state["posted_urls"] = (state["posted_urls"] + [i["link"] for i in new_items])[-500:]
    record_price(state, prices)
    save_state(state)
    print("[ok] full brief done")


# ============================================================
# MODE: WATCH (BREAKING + Price alarm)
# ============================================================

def _cooldown_active(state, key):
    last = state["last_alert"].get(key)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
        return (datetime.now(timezone(timedelta(hours=7))) - last_dt) < timedelta(minutes=ALERT_COOLDOWN_MINUTES)
    except Exception:
        return False


def _fire_price_alert(state, prices, btc_c, gold_c):
    print(f"[alert] PRICE! BTC {btc_c:+.2f}%, Gold {gold_c:+.2f}%")
    context_news = fetch_headlines(hours_back=1, keywords=KEYWORDS)
    fg = fetch_fear_greed()
    perp = fetch_btc_perp()
    summary = ai_price_alert(prices, btc_c, gold_c, context_news[:5], fg, perp)
    post_discord(summary, title_prefix="🚨 PRICE ALERT", color=15158332)
    state["last_alert"]["price"] = datetime.now(timezone(timedelta(hours=7))).isoformat()


def _fire_breaking(state, prices, breaking_items):
    print(f"[alert] BREAKING! {len(breaking_items)} items")
    fg = fetch_fear_greed()
    summary = ai_breaking_alert(breaking_items, prices, fg, state["posts"])
    if summary.strip().upper() == "SKIP":
        print("[info] SKIP")
        state["posted_urls"] = (state["posted_urls"] + [i["link"] for i in breaking_items])[-500:]
        return
    # เพิ่ม mini header
    header = (
        f"💰 BTC **${prices['btc']['price']:,.0f}** ({prices['btc']['change_24h']:+.2f}%) | "
        f"🥇 Gold **${prices['gold']['price']:,.2f}** ({prices['gold']['change_24h']:+.2f}%)\n\n"
    )
    if fg:
        header = f"😱 F&G: {fg['value']} ({fg['label']}) | " + header
    post_discord(header + summary, title_prefix="🔥 BREAKING NEWS", color=15158332)
    now_iso = datetime.now(timezone(timedelta(hours=7))).isoformat()
    state["posts"].append({"time": now_iso, "content": summary})
    state["posts"] = state["posts"][-8:]
    state["posted_urls"] = (state["posted_urls"] + [i["link"] for i in breaking_items])[-500:]
    state["last_alert"]["breaking"] = now_iso


def mode_watch():
    print("[mode] watch")
    state = load_state()
    prices = fetch_prices()
    if not prices:
        print("[error] no prices — abort")
        return

    # Layer 3: Price Alarm
    btc_c, gold_c = compute_price_change(prices, state["price_history"], PRICE_LOOKBACK_HOURS)
    print(f"[info] 1h: BTC {btc_c}, Gold {gold_c}")
    btc_alert = btc_c is not None and abs(btc_c) >= BTC_ALERT_THRESHOLD
    gold_alert = gold_c is not None and abs(gold_c) >= GOLD_ALERT_THRESHOLD

    if (btc_alert or gold_alert) and not _cooldown_active(state, "price"):
        _fire_price_alert(state, prices, btc_c, gold_c)

    # Layer 2: BREAKING
    breaking = fetch_headlines(hours_back=1.5, keywords=BREAKING_KEYWORDS)
    new_breaking = filter_new(breaking, state["posted_urls"])
    if new_breaking and not _cooldown_active(state, "breaking"):
        _fire_breaking(state, prices, new_breaking)

    record_price(state, prices)
    save_state(state)
    print("[ok] watch done")


# ============================================================
# MODE: WEEKLY WRAP
# ============================================================

def mode_weekly():
    print("[mode] weekly")
    state = load_state()
    prices = fetch_prices()
    if not prices:
        print("[error] no prices")
        return
    fg = fetch_fear_greed()
    macro = fetch_macro()
    perp = fetch_btc_perp()
    summary = ai_weekly_wrap(prices, fg, macro, perp, state["posts"])
    header = build_header(prices, fg, macro, perp, None, [])
    post_discord(header + summary, title_prefix="📊 Weekly Wrap", color=3447003)
    print("[ok] weekly done")


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "watch", "weekly"], default="full")
    args = parser.parse_args()

    if args.mode == "full":
        mode_full_brief()
    elif args.mode == "watch":
        mode_watch()
    elif args.mode == "weekly":
        mode_weekly()


if __name__ == "__main__":
    main()
