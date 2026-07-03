"""
Financial News Bot -> Discord
รัน 4 รอบ/วันผ่าน GitHub Actions
พร้อมระบบ:
- จำข่าวเก่า (dedup ด้วย URL)
- ตรวจสอบ context ข่าวก่อนหน้า (BREAKING / NEW / UPDATE)
- วิเคราะห์แนวโน้มทุกข่าว
"""

import os
import json
import feedparser
import requests
import anthropic
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---- Config from environment variables (GitHub Secrets) ----
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# ---- State file (จะ commit กลับ repo หลังรันเสร็จ) ----
STATE_FILE = Path("state.json")

# ---- News sources (RSS feeds) ----
FEEDS = {
    "Reuters Business": "https://feeds.reuters.com/reuters/businessNews",
    "Investing.com Economy": "https://www.investing.com/rss/news_285.rss",
    "CNBC Markets": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Kitco Gold News": "https://www.kitco.com/rss/KitcoNews.xml",
}

# ---- Expanded keywords ----
KEYWORDS = [
    # Fed / นโยบายการเงิน
    "fed", "fomc", "powell", "warsh", "rate", "inflation", "cpi", "ppi",
    "payroll", "nfp", "gdp", "unemployment", "jobs", "yield", "treasury",
    "dollar", "dxy",
    # ธนาคารกลางอื่น
    "ecb", "boj", "boe", "lagarde", "central bank",
    # Commodities
    "gold", "oil", "opec", "brent", "wti", "commodity",
    # Crypto
    "bitcoin", "btc", "crypto", "ethereum", "eth", "sec",
    # ภูมิรัฐศาสตร์ / สงคราม
    "middle east", "war", "iran", "israel", "ukraine", "russia",
    "china", "taiwan", "north korea", "missile", "strike", "ceasefire",
    # การเมืองสหรัฐ / การค้า
    "trump", "biden", "election", "executive order", "sanctions",
    "congress", "senate", "shutdown", "debt ceiling", "veto",
    "tariff", "trade war", "chip ban", "semiconductor",
    # Macro
    "recession", "stagflation",
]


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():
    """โหลด state ที่บันทึกไว้ (posts เก่า 24-36 ชม. + URL ที่โพสไปแล้ว)"""
    if not STATE_FILE.exists():
        return {"posts": [], "posted_urls": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[warn] load_state failed: {e}")
        return {"posts": [], "posted_urls": []}


def save_state(state):
    """บันทึก state (workflow จะ commit กลับ repo ให้อัตโนมัติ)"""
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ============================================================
# NEWS FETCHING
# ============================================================

def fetch_headlines(hours_back=8):
    """ดึงข่าวจาก RSS 8 ชม.ล่าสุด (buffer กัน cron delay)"""
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
                if any(k in text for k in KEYWORDS):
                    items.append({
                        "source": source,
                        "title": title,
                        "summary": summary,
                        "link": entry.get("link", ""),
                        "published": pub_dt.isoformat(),
                    })
        except Exception as e:
            print(f"[warn] {source}: {e}")
    return items


def filter_new_items(items, posted_urls):
    """กรอง URL ที่โพสไปแล้วออก"""
    posted_set = set(posted_urls)
    return [i for i in items if i["link"] and i["link"] not in posted_set]


# ============================================================
# AI SUMMARIZATION
# ============================================================

def ai_summarize(items, previous_posts):
    """ให้ Claude สรุปข่าวเป็นภาษาไทย + วิเคราะห์แนวโน้ม + เทียบกับโพสก่อน"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    joined_news = "\n\n".join(
        f"[{i['source']}] {i['title']}\n{i['summary']}\nURL: {i['link']}"
        for i in items
    )

    # แสดง posts ที่เคยส่ง (สูงสุด 5 ล่าสุด) ให้ AI ใช้เทียบ
    context = ""
    if previous_posts:
        context = "\n\n=== โพสก่อนหน้าที่บอทเคยส่ง (24 ชม.ล่าสุด — ใช้เพื่อเช็คว่าอะไรใหม่/อัปเดต) ===\n"
        for p in previous_posts[-5:]:
            context += f"\n[โพสเมื่อ {p['time']}]\n{p['content']}\n"

    prompt = f"""คุณคือนักวิเคราะห์ตลาดการเงิน สรุปข่าวเศรษฐกิจสำหรับห้อง Discord "ข่าวเศรษฐกิจ"
{context}

=== ข่าวใหม่ 8 ชม.ล่าสุด ===
{joined_news}

**กติกาสำคัญ**:

1. **แบ่งข่าวเป็น 3 กลุ่ม สถานะ**:
   - 🔥 **BREAKING** — ข่าวใหญ่ market-moving ที่พึ่งเกิด (ใช้เฉพาะเหตุการณ์สำคัญมาก เช่น สงคราม, Fed พูดฉุกเฉิน, crash)
   - 🆕 **NEW** — ข่าวใหม่ที่ไม่เคยพูดในโพสก่อนหน้า
   - 🔄 **UPDATE** — เรื่องเก่าจากโพสก่อนที่มีตัวเลข/ทิศทาง/สถานการณ์เปลี่ยน

2. **ข้าม STALE** — ข่าวที่ซ้ำโพสก่อนและ**ไม่มีอะไรเปลี่ยน** ไม่ต้องเอามา

3. **Format แต่ละข่าว 3 บรรทัด**:
```
[emoji status] หัวข้อสั้น
รายละเอียด/ตัวเลข (2-3 บรรทัดสั้นๆ พร้อมตัวเลขจริง)
💡 แนวโน้ม: ทิศทาง + asset ที่กระทบ (ทอง/หุ้น/BTC/USD) + level ที่จับตา
```

4. **UPDATE format พิเศษ**:
```
🔄 อัปเดต — [หัวข้อ]
เดิม: [ที่รายงานไปก่อน]
ล่าสุด: [ข้อมูลใหม่/พลิก]
💡 แนวโน้ม: ...
```

5. **กติกา "แนวโน้ม"** (บรรทัด 💡):
   - ต้อง SPECIFIC มีตัวเลข level แนวรับ/ต้าน %target หรือ event ที่ต้องจับตา
   - ระบุ asset ชัดเจน (ทอง/หุ้น/BTC/USD)
   - **ห้ามพูดกำกวมแบบ** "ต้องจับตาต่อไป", "ตลาดผันผวน" — ต้องมีสาระ

6. **ปิดท้าย 1 บรรทัด**: `🎯 ภาพรวม: [สรุปทิศทางตลาดสั้นๆ 1 บรรทัด]`

7. **ถ้าไม่มีข่าวสำคัญเลย** (STALE ทั้งหมด หรือทั้งหมดคุณค่าต่ำ) ตอบเพียง: "ไม่มีข่าวใหม่ที่สำคัญ"

8. **ห้ามเกิน 2500 ตัวอักษร** เลือกเฉพาะข่าวเด่น 3-5 อัน

9. **ห้ามเพิ่มหมวดที่ไม่มีข่าวจริงในนั้น** — ถ้าไม่มีข่าวคริปโต ก็ไม่ต้องมีหัวข้อคริปโต

ตอบกลับเฉพาะเนื้อหาสรุปเท่านั้น ไม่ต้องมีคำนำหรือคำอธิบาย"""

    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [b.text for b in msg.content if getattr(b, "text", None)]
    if not parts:
        raise RuntimeError(f"ไม่มี text ใน response: {msg.content}")
    return "\n".join(parts).strip()


# ============================================================
# DISCORD POSTING
# ============================================================

def post_to_discord(summary):
    """ส่ง embed ไป Discord"""
    now_th = datetime.now(timezone(timedelta(hours=7))).strftime("%d %b %Y %H:%M")

    # เลือกสีตาม content (breaking = แดง, ปกติ = เหลือง)
    color = 15158332 if "🔥 BREAKING" in summary else 15844367

    payload = {
        "username": "AI Bot",
        "embeds": [{
            "title": f"📊 อัปเดตข่าวการเงิน | {now_th}",
            "description": summary[:4000],
            "color": color,
            "footer": {"text": "AI Bot • ข้อมูลนี้เพื่อการติดตามข่าว ไม่ใช่คำแนะนำการลงทุน"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
    r.raise_for_status()
    print(f"[ok] posted to Discord ({r.status_code})")


# ============================================================
# MAIN
# ============================================================

def main():
    # 1. โหลด state
    state = load_state()
    print(f"[info] state: {len(state['posts'])} previous posts, "
          f"{len(state['posted_urls'])} known URLs")

    # 2. ดึงข่าว 8 ชม.ล่าสุด
    items = fetch_headlines(hours_back=8)
    print(f"[info] fetched {len(items)} relevant headlines")

    # 3. กรอง URL ที่โพสแล้ว
    new_items = filter_new_items(items, state["posted_urls"])
    print(f"[info] {len(new_items)} truly new after dedup")

    if not new_items:
        print("[info] no new items — skipping")
        return

    # 4. ให้ AI สรุป (พร้อม context ของ posts ก่อนหน้า)
    summary = ai_summarize(new_items, state["posts"])
    print(f"[info] summary length: {len(summary)} chars")

    # 5. AI บอกว่าไม่มีอะไรสำคัญ = ไม่โพส แต่อัปเดต URL ให้ไม่เจอซ้ำ
    if "ไม่มีข่าวใหม่ที่สำคัญ" in summary or len(summary) < 80:
        print("[info] AI: nothing important — updating URLs only")
        state["posted_urls"] = (state["posted_urls"] + [i["link"] for i in new_items])[-500:]
        save_state(state)
        return

    # 6. โพสเข้า Discord
    post_to_discord(summary)

    # 7. บันทึก state
    now_iso = datetime.now(timezone(timedelta(hours=7))).isoformat()
    state["posts"].append({"time": now_iso, "content": summary})
    state["posts"] = state["posts"][-6:]  # เก็บ 6 posts ล่าสุด (~24-36 ชม.)
    state["posted_urls"] = (state["posted_urls"] + [i["link"] for i in new_items])[-500:]
    save_state(state)
    print(f"[ok] state saved: {len(state['posts'])} posts, "
          f"{len(state['posted_urls'])} URLs")


if __name__ == "__main__":
    main()
