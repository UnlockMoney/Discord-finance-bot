"""
Event Countdown Bot — สำหรับห้อง 📅-ปฏิทินตลาด
โพส 4 แบบ:
1. จันทร์ 08:00 — สรุป events สัปดาห์
2. ก่อน event 24 ชม. — เตือนล่วงหน้า
3. ก่อน event 1 ชม. — Alert ด่วน
4. หลัง event 15 นาที — สรุปผล
"""

import os
import json
import time
import feedparser
import requests
import anthropic
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---- Config ----
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK_EVENT_URL"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

CALENDAR_FILE = Path("economic_events.json")
STATE_FILE = Path("event_state.json")

TZ = timezone(timedelta(hours=7))  # เวลาไทย
MAX_CONTENT = 1950

FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
]


# ============================================================
# STATE
# ============================================================

def load_state():
    if not STATE_FILE.exists():
        return {"posted": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"posted": {}}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_calendar():
    if not CALENDAR_FILE.exists():
        return []
    try:
        return json.loads(CALENDAR_FILE.read_text(encoding="utf-8")).get("events", [])
    except Exception:
        return []


def event_id(e):
    return f"{e['date']}_{e['time']}_{e['event']}"


def event_datetime(e):
    return datetime.fromisoformat(f"{e['date']}T{e['time']}:00+07:00")


# ============================================================
# DISCORD
# ============================================================

def _post_single(content_text):
    payload = {
        "username": "ยาม AI • ปฏิทินตลาด",
        "content": content_text[:MAX_CONTENT],
        "allowed_mentions": {"parse": []},
    }
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
    r.raise_for_status()
    print(f"[ok] posted ({len(content_text)} chars)")


def post_discord(text):
    _post_single(text)


# ============================================================
# ALERTS
# ============================================================

def _importance_emoji(importance):
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(importance, "⚪")


def _impact_line(e):
    """สรุปว่า event นี้กระทบ BTC/ทองยังไง"""
    name = e["event"].lower()
    if any(k in name for k in ["cpi", "ppi", "pce"]):
        return "💡 ผลกระทบ: 🥇 ทอง (สูง) | ₿ BTC (สูง) — ตัวเลขเงินเฟ้อ"
    if any(k in name for k in ["nfp", "payroll", "jobless"]):
        return "💡 ผลกระทบ: 🥇 ทอง (สูง) | ₿ BTC (กลาง) — ตลาดแรงงาน → Fed direction"
    if any(k in name for k in ["fomc", "fed", "rate"]):
        return "💡 ผลกระทบ: 🥇 ทอง (สูงมาก) | ₿ BTC (สูงมาก) — Fed policy"
    if "gdp" in name:
        return "💡 ผลกระทบ: 🥇 ทอง (กลาง) | ₿ BTC (กลาง)"
    if "ecb" in name or "boj" in name:
        return "💡 ผลกระทบ: 🥇 ทอง (กลาง) | ₿ BTC (ต่ำ)"
    return "💡 ผลกระทบ: 🥇 ทอง / ₿ BTC"


def post_weekly_summary(events, now):
    """จันทร์ 08:00 - สรุป events สัปดาห์"""
    week_end = now + timedelta(days=7)
    week_events = [e for e in events if now <= event_datetime(e) <= week_end]
    if not week_events:
        text = "# 📅 ปฏิทินตลาด — สัปดาห์นี้\n\nสัปดาห์นี้**ไม่มี event สำคัญ** 😌\n\n-# ยาม AI • ปฏิทินตลาด"
        post_discord(text)
        return

    lines = [f"# 📅 Events สัปดาห์นี้ที่ต้องจับตา", ""]
    for e in sorted(week_events, key=event_datetime):
        dt = event_datetime(e)
        emoji = _importance_emoji(e.get("importance"))
        date_str = dt.strftime("%a %d/%m %H:%M")
        consensus = f" (คาด {e['consensus']})" if e.get("consensus") else ""
        lines.append(f"### {emoji} {date_str} — {e['event']}{consensus}")
        lines.append(_impact_line(e))
        lines.append("")

    next_event = sorted(week_events, key=event_datetime)[0]
    delta = event_datetime(next_event) - now
    days = delta.days
    hours = delta.seconds // 3600
    lines.append(f"⏰ Event ถัดไปอีก **{days} วัน {hours} ชม.** — {next_event['event']}")
    lines.append("")
    lines.append("-# ยาม AI • ปฏิทินตลาด")
    post_discord("\n".join(lines))


def post_24h_warning(e):
    """24 ชม.ก่อน event"""
    dt = event_datetime(e)
    date_str = dt.strftime("%a %d/%m เวลา %H:%M")
    consensus = e.get("consensus", "N/A")
    text = f"""# ⏰ อีก 24 ชม. — {e['event']}

📅 พรุ่งนี้ {date_str} (เวลาไทย)
📊 Consensus: **{consensus}**

{_impact_line(e)}

## 🎯 สิ่งที่ควรรู้
{_scenario_text(e)}

⚠️ **ระวังโพซิชั่น** 30 นาทีก่อน–หลังประกาศ
ตลาดผันผวนแรงหลังตัวเลขออก

-# ยาม AI • ปฏิทินตลาด"""
    post_discord(text)


def post_1h_alert(e):
    """1 ชม.ก่อน event"""
    dt = event_datetime(e)
    text = f"""# 🚨 อีก 1 ชั่วโมง! {e['event']}

⏰ เวลา **{dt.strftime('%H:%M น.')}**
📊 Consensus: **{e.get('consensus', 'N/A')}**

{_impact_line(e)}

## ⚠️ Snapshot ก่อนตัวเลขออก
- ตรวจสอบ stop-loss
- ลดขนาดโพซิชั่นถ้ากังวล
- อย่าเปิดไม้ใหม่ 15 นาทีก่อนประกาศ

หลังตัวเลขออก 15 นาที บอทจะสรุปผล

-# ยาม AI • ปฏิทินตลาด"""
    post_discord(text)


def post_result(e):
    """15 นาทีหลัง event - สรุปด้วย AI จาก RSS"""
    news = _fetch_recent_news(hours_back=1)
    keyword = _event_keyword(e)
    matched = [n for n in news if keyword.lower() in (n["title"] + n["summary"]).lower()]

    if not matched:
        text = f"""# ⏰ {e['event']} ประกาศแล้ว

ตรวจสอบผลลัพธ์ในห้อง **#👁️-ai-เฝ้าตลาด** สำหรับข่าวเต็ม
หรือดูข่าวสดจาก Reuters/CNBC

📊 Consensus: {e.get('consensus', 'N/A')}
{_impact_line(e)}

-# ยาม AI • ปฏิทินตลาด"""
        post_discord(text)
        return

    # ให้ AI สรุปผล
    summary = _ai_summarize_result(e, matched)
    post_discord(summary)


def _scenario_text(e):
    """เขียน scenario if higher/lower/inline"""
    name = e["event"].lower()
    if "cpi" in name or "ppi" in name or "pce" in name:
        return ("- ถ้า > คาด → 🥇 ทอง**พุ่ง** 1-2% (hedge เงินเฟ้อ) | ₿ BTC**อ่อน** (Fed hawkish)\n"
                "- ถ้า < คาด → 🥇 ทอง**ย่อ** | ₿ BTC**บวก** 3-5%\n"
                "- ถ้า = คาด → ตลาดแทบไม่ขยับ")
    if "nfp" in name or "payroll" in name:
        return ("- ถ้า > คาดมาก → เศรษฐกิจแข็ง Fed อาจ hawkish → กด BTC/ทอง\n"
                "- ถ้า < คาดมาก → Fed dovish → หนุน BTC/ทอง\n"
                "- Wage growth สำคัญกว่าตัวเลข headline")
    if "fomc" in name or "rate" in name:
        return ("- ประเด็นสำคัญ: dot plot, ประโยค forward guidance\n"
                "- Hawkish surprise → BTC/ทอง ร่วงแรง\n"
                "- Dovish surprise → BTC/ทอง พุ่งแรง")
    return "- ติดตามตัวเลขจริง vs consensus\n- ผลกระทบขึ้นกับความห่างจากคาดการณ์"


def _event_keyword(e):
    """เลือก keyword หา RSS หลัง event"""
    name = e["event"].lower()
    if "cpi" in name: return "CPI"
    if "ppi" in name: return "PPI"
    if "pce" in name: return "PCE"
    if "nfp" in name or "payroll" in name: return "payrolls"
    if "jobless" in name: return "jobless claims"
    if "gdp" in name: return "GDP"
    if "fomc" in name: return "FOMC"
    return e["event"].split()[0]


def _fetch_recent_news(hours_back=1):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    items = []
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if not pub:
                    continue
                pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
                items.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:400],
                    "link": entry.get("link", ""),
                })
        except Exception as e:
            print(f"[warn] rss: {e}")
    return items


def _ai_summarize_result(e, news):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    news_txt = "\n\n".join(f"[{n['title']}]\n{n['summary']}" for n in news[:5])
    prompt = f"""สรุปผลการประกาศ economic event สำหรับกลุ่ม Discord ที่โฟกัส BTC + ทอง

Event: {e['event']}
Consensus: {e.get('consensus', 'N/A')}

ข่าวหลังประกาศ:
{news_txt}

Format:
```
# 📊 {e['event']} ประกาศแล้ว!

📈 ตัวเลขจริง: [ดึงจากข่าว]
📊 Consensus: {e.get('consensus', 'N/A')}
🎯 [สูงกว่า/ต่ำกว่า/ตรง] คาด

## 💡 ผลกระทบ
🥇 ทอง: [ทิศทาง + %change ถ้ามีในข่าว]
₿ BTC: [ทิศทาง + %change]

## 🔮 มุมมองต่อไป
[1-2 บรรทัดว่าตลาดจะไปทางไหน]
```

ห้ามเกิน 1500 ตัวอักษร ตอบเฉพาะเนื้อหา"""

    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [b.text for b in msg.content if getattr(b, "text", None)]
    return "\n".join(parts).strip() + "\n\n-# ยาม AI • ปฏิทินตลาด"


# ============================================================
# MAIN
# ============================================================

def main():
    now = datetime.now(TZ)
    state = load_state()
    events = load_calendar()
    print(f"[info] now={now.isoformat()}, {len(events)} events in calendar")

    # 1. Weekly summary — จันทร์ 08:00-08:30
    if now.weekday() == 0 and now.hour == 8 and now.minute < 30:
        key = f"weekly_{now.strftime('%Y-%W')}"
        if key not in state["posted"]:
            print("[action] weekly summary")
            post_weekly_summary(events, now)
            state["posted"][key] = True
            save_state(state)
            return

    # 2-4. Per-event alerts
    for e in events:
        try:
            dt = event_datetime(e)
        except Exception:
            continue

        eid = event_id(e)
        state["posted"].setdefault(eid, [])
        delta = dt - now
        minutes_to = delta.total_seconds() / 60

        # 1-hour warning (window: 45-75 min before)
        # NOTE: 24h warning ตัดออกตามความต้องการ user (ลด noise)
        if 45 <= minutes_to <= 75 and "1h" not in state["posted"][eid]:
            print(f"[action] 1h alert: {e['event']}")
            post_1h_alert(e)
            state["posted"][eid].append("1h")
            save_state(state)

        # Post-event result (window: 10-30 min after)
        elif -30 <= minutes_to <= -10 and "result" not in state["posted"][eid]:
            print(f"[action] result: {e['event']}")
            post_result(e)
            state["posted"][eid].append("result")
            save_state(state)

    # Cleanup old events (>30 days old) from state
    cutoff_dt = now - timedelta(days=30)
    to_remove = []
    for eid in list(state["posted"].keys()):
        if eid.startswith("weekly_"):
            continue
        try:
            date_part = eid.split("_")[0]
            evdt = datetime.fromisoformat(date_part + "T00:00:00+07:00")
            if evdt < cutoff_dt:
                to_remove.append(eid)
        except Exception:
            continue
    for eid in to_remove:
        del state["posted"][eid]
    if to_remove:
        save_state(state)

    print("[ok] done")


if __name__ == "__main__":
    main()
