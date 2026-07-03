"""
Daily Financial News Bot -> Discord
รันวันละครั้งผ่าน GitHub Actions
"""

import os
import json
import feedparser
import requests
import anthropic
from datetime import datetime, timezone, timedelta

# ---- Config from environment variables (GitHub Secrets) ----
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# ---- News sources (RSS feeds) ----
FEEDS = {
    "Reuters Business": "https://feeds.reuters.com/reuters/businessNews",
    "Investing.com Economy": "https://www.investing.com/rss/news_285.rss",
    "CNBC Markets": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Kitco Gold News": "https://www.kitco.com/rss/KitcoNews.xml",
}

KEYWORDS = [
    "fed", "fomc", "powell", "warsh", "rate", "inflation", "cpi", "ppi",
    "payroll", "nfp", "gdp", "unemployment", "jobs",
    "ecb", "boj", "lagarde", "central bank",
    "gold", "bitcoin", "btc", "crypto", "ethereum",
    "oil", "opec", "brent", "wti",
    "middle east", "war", "iran", "china", "tariff",
    "recession", "yield", "treasury", "dollar", "dxy",
]


def fetch_headlines(hours_back=24):
    """Grab recent headlines from all feeds."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    items = []
    for source, url in FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
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


def ai_summarize(items):
    """Use Claude to write a Thai-language Discord summary."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    joined = "\n\n".join(
        f"[{i['source']}] {i['title']}\n{i['summary']}" for i in items
    )
    prompt = f"""คุณคือนักวิเคราะห์ตลาดการเงิน สรุปข่าวเศรษฐกิจต่อไปนี้เป็นภาษาไทยสำหรับโพสในห้อง Discord "ข่าวเศรษฐกิจ"

ต้องการ:
- สั้น กระชับ อ่านง่าย (เหมาะกับคนเบื่อง่าย) แต่มีรายละเอียดพอใช้ตัดสินใจ
- แบ่งเป็นหัวข้อ: Fed / ธนาคารกลาง, ตัวเลขเศรษฐกิจ, ภูมิรัฐศาสตร์, ทองคำ, Bitcoin/Crypto
- ใส่ emoji ทุกหัวข้อ
- ปิดท้ายด้วย "🎯 สรุปผลกระทบ" และ "📅 ต้องจับตาต่อ"
- ถ้าไม่มีข่าวสำคัญในหมวดใด ให้ข้ามหมวดนั้นได้
- ใช้ตัวหนา **...** ตามเหมาะสม
- ห้ามเกิน 4000 ตัวอักษร

ข่าว 24 ชม.ล่าสุด:
{joined}

ตอบกลับเฉพาะเนื้อหาสรุปเท่านั้น ไม่ต้องมีคำนำ"""
    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    # ดึง text จากทุก content block (ข้าม thinking block ที่ไม่มี text)
    parts = [b.text for b in msg.content if getattr(b, "text", None)]
    if not parts:
        raise RuntimeError(f"ไม่มี text ใน response: {msg.content}")
    return "\n".join(parts).strip()

def post_to_discord(summary):
    """Send embed to Discord webhook."""
    now_th = datetime.now(timezone(timedelta(hours=7))).strftime("%d %b %Y")
    payload = {
        "username": "AI Bot",
        "embeds": [{
            "title": f"📊 สรุปข่าวเศรษฐกิจการเงินโลก | {now_th}",
            "description": summary[:4000],
            "color": 15844367,
            "footer": {"text": "AI Bot • ข้อมูลนี้เพื่อการติดตามข่าว ไม่ใช่คำแนะนำการลงทุน"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
    r.raise_for_status()
    print(f"[ok] posted to Discord ({r.status_code})")


def main():
    items = fetch_headlines(hours_back=24)
    print(f"[info] found {len(items)} relevant headlines")
    if not items:
        print("[info] nothing to post today")
        return
    summary = ai_summarize(items)
    post_to_discord(summary)


if __name__ == "__main__":
    main()
