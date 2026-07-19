"""
Prediction Game Bot — ทายราคา BTC/Gold รายสัปดาห์

ตารางงาน:
- จันทร์ 09:00 → เปิดรอบใหม่ (ทายราคาปิดศุกร์)
- ทุก 15 นาที (จันทร์-พุธ) → poll ข้อความสมาชิกทาย
- พุธ 23:59 → ปิดรับ
- เสาร์ 09:00 → ประกาศผล + leaderboard

Format การทาย: "BTC 65000" หรือ "Gold 4200"
"""

import os
import re
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---- Config ----
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = os.environ["DISCORD_GAME_CHANNEL_ID"]

STATE_FILE = Path("prediction_state.json")
TZ = timezone(timedelta(hours=7))

API = "https://discord.com/api/v10"
HEADERS = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}


# ============================================================
# STATE
# ============================================================

def load_state():
    default = {
        "current_round": None,
        "leaderboard": {},
        "history": [],
    }
    if not STATE_FILE.exists():
        return default
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        for k, v in default.items():
            state.setdefault(k, v)
        return state
    except Exception:
        return default


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ============================================================
# DISCORD API
# ============================================================

def send_message(content):
    r = requests.post(
        f"{API}/channels/{CHANNEL_ID}/messages",
        headers=HEADERS,
        json={"content": content[:2000], "allowed_mentions": {"parse": []}},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def fetch_messages(after=None, limit=100):
    """ดึงข้อความจาก channel (after = message ID เก่าสุดที่อ่านไปแล้ว)"""
    params = {"limit": limit}
    if after:
        params["after"] = after
    r = requests.get(
        f"{API}/channels/{CHANNEL_ID}/messages",
        headers=HEADERS,
        params=params,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# ============================================================
# PRICE (CoinGecko)
# ============================================================

def get_price(asset):
    """asset = 'btc' หรือ 'gold'"""
    coin_id = "bitcoin" if asset == "btc" else "pax-gold"
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": coin_id, "vs_currencies": "usd"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()[coin_id]["usd"]


# ============================================================
# PREDICTION PARSING
# ============================================================

PATTERN = re.compile(
    r'^(?:predict\s+)?(btc|bitcoin|gold|ทอง|ทองคำ)\s*[:=]?\s*'
    r'([\d,]+(?:\.\d+)?)\s*(k|K)?\s*$',
    re.IGNORECASE
)


def parse_prediction(content):
    """Parse 'BTC 65000', 'Gold 4200', 'BTC 65k' → (asset, value) หรือ None"""
    content = content.strip()
    if len(content) > 50:  # กัน parse ข้อความยาวเกิน
        return None
    m = PATTERN.match(content)
    if not m:
        return None
    asset_word = m.group(1).lower()
    asset = "btc" if asset_word in ("btc", "bitcoin") else "gold"
    value_str = m.group(2).replace(",", "")
    try:
        value = float(value_str)
    except ValueError:
        return None
    if m.group(3):  # k suffix
        value *= 1000
    # Sanity check ราคา
    if asset == "btc" and not (10000 <= value <= 500000):
        return None
    if asset == "gold" and not (1000 <= value <= 10000):
        return None
    return asset, value


# ============================================================
# ROUND MANAGEMENT
# ============================================================

def get_week_string(dt):
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def open_round(state, now):
    """จันทร์ 09:00 - เปิดรอบใหม่"""
    week = get_week_string(now)
    friday = now + timedelta(days=(4 - now.weekday()))
    target = friday.replace(hour=23, minute=59, second=0, microsecond=0)
    close = (now + timedelta(days=2)).replace(hour=23, minute=59, second=0)

    btc_now = get_price("btc")
    gold_now = get_price("gold")

    week_num = week.split('-W')[1]
    content = f"""# 🎮 Prediction Game — Week {week_num}

📅 **ทายราคาปิด ศุกร์ {target.strftime('%d/%m')} เวลา 23:59 (UTC+7)**
🚫 ปิดรับ: **พุธ {close.strftime('%d/%m')} 23:59**

## 📊 ราคาปัจจุบัน (ตอนเปิดรอบ)
• ₿ BTC: **${btc_now:,.0f}**
• 🥇 ทอง: **${gold_now:,.2f}**

## 📝 วิธีทาย
พิมพ์ในห้องนี้:
```
BTC 65000
Gold 4200
```
(เขียน `BTC 65k` แบบสั้นก็ได้)

## ⚠️ กติกา
• ทายได้ 1 ครั้ง/สินทรัพย์/สัปดาห์
• ทายซ้ำ = ใช้ค่าล่าสุด
• ทายทั้ง BTC + Gold ได้
• **ห้ามคุยเล่นในห้องนี้** บอทจะ parse ผิด

🏆 คนที่ทายใกล้สุด (BTC/Gold แยกกัน) = +1 คะแนน

-# ยาม AI • Prediction Game"""

    msg = send_message(content)

    state["current_round"] = {
        "week": week,
        "open_time": now.isoformat(),
        "close_time": close.isoformat(),
        "target_time": target.isoformat(),
        "status": "open",
        "opener_msg_id": msg["id"],
        "last_read_msg_id": msg["id"],
        "btc_open_price": btc_now,
        "gold_open_price": gold_now,
        "predictions": {},
    }
    save_state(state)
    print(f"[open] round {week} opened")


def poll_predictions(state):
    """อ่านข้อความใหม่ + parse predictions"""
    round_ = state["current_round"]
    if not round_ or round_["status"] != "open":
        return

    last_id = round_["last_read_msg_id"]
    try:
        messages = fetch_messages(after=last_id, limit=100)
    except Exception as e:
        print(f"[error] fetch messages: {e}")
        return

    if not messages:
        return

    new_count = 0
    for msg in reversed(messages):  # เก่าไปใหม่
        if msg.get("author", {}).get("bot"):
            continue
        content = msg.get("content", "").strip()
        parsed = parse_prediction(content)
        if not parsed:
            continue

        asset, value = parsed
        user_id = msg["author"]["id"]
        author = msg["author"]
        username = author.get("global_name") or author.get("username") or f"user_{user_id}"

        round_["predictions"].setdefault(user_id, {"username": username})
        round_["predictions"][user_id]["username"] = username
        round_["predictions"][user_id][asset] = value
        round_["predictions"][user_id][f"{asset}_msg_id"] = msg["id"]
        new_count += 1
        print(f"[predict] {username}: {asset} = {value}")

    # Update last_read_msg_id ให้เป็น ID ใหม่สุด
    round_["last_read_msg_id"] = messages[0]["id"]
    print(f"[poll] processed {len(messages)} messages, {new_count} predictions")


def close_round(state):
    """พุธ 23:59 - ปิดรับ"""
    round_ = state["current_round"]
    if not round_ or round_["status"] != "open":
        return

    round_["status"] = "closed"
    total = len(round_["predictions"])
    btc_count = sum(1 for p in round_["predictions"].values() if "btc" in p)
    gold_count = sum(1 for p in round_["predictions"].values() if "gold" in p)
    week_num = round_["week"].split("-W")[1]

    content = f"""# 🚫 ปิดรับ Week {week_num}

## 📊 สรุปการทาย
• 👥 สมาชิกทาย: **{total} คน**
• ₿ BTC: **{btc_count}** คน
• 🥇 Gold: **{gold_count}** คน

⏰ รอราคาปิดศุกร์ 23:59
🏆 ประกาศผล: **เสาร์ 09:00**

-# ยาม AI • Prediction Game"""

    send_message(content)
    save_state(state)
    print(f"[close] closed with {total} predictions")


def announce_winners(state):
    """เสาร์ 09:00 - ประกาศผล"""
    round_ = state["current_round"]
    if not round_ or round_["status"] != "closed":
        return

    btc_actual = get_price("btc")
    gold_actual = get_price("gold")

    btc_preds = [(uid, p) for uid, p in round_["predictions"].items() if "btc" in p]
    gold_preds = [(uid, p) for uid, p in round_["predictions"].items() if "gold" in p]

    def rank(preds, actual, key):
        return sorted(preds, key=lambda x: abs(x[1][key] - actual))

    btc_ranked = rank(btc_preds, btc_actual, "btc")
    gold_ranked = rank(gold_preds, gold_actual, "gold")

    # Update leaderboard
    if btc_ranked:
        wid, wp = btc_ranked[0]
        state["leaderboard"].setdefault(wid, {"username": wp["username"], "wins": 0})
        state["leaderboard"][wid]["wins"] += 1
        state["leaderboard"][wid]["username"] = wp["username"]

    if gold_ranked:
        wid, wp = gold_ranked[0]
        state["leaderboard"].setdefault(wid, {"username": wp["username"], "wins": 0})
        state["leaderboard"][wid]["wins"] += 1
        state["leaderboard"][wid]["username"] = wp["username"]

    week_num = round_["week"].split("-W")[1]
    lines = [
        f"# 🏆 ผลรอบ Week {week_num}",
        "",
        "## 📊 ราคาปิดจริง",
        f"• ₿ BTC: **${btc_actual:,.0f}**",
        f"• 🥇 ทอง: **${gold_actual:,.2f}**",
        "",
    ]

    if btc_ranked:
        lines.append("## ₿ BTC Winners")
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, p) in enumerate(btc_ranked[:3]):
            diff = abs(p["btc"] - btc_actual)
            lines.append(f"{medals[i]} **{p['username']}** — ทาย ${p['btc']:,.0f} (คลาด ${diff:,.0f})")
        lines.append("")
    else:
        lines.append("## ₿ BTC — ไม่มีคนทาย\n")

    if gold_ranked:
        lines.append("## 🥇 Gold Winners")
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, p) in enumerate(gold_ranked[:3]):
            diff = abs(p["gold"] - gold_actual)
            lines.append(f"{medals[i]} **{p['username']}** — ทาย ${p['gold']:,.2f} (คลาด ${diff:.2f})")
        lines.append("")
    else:
        lines.append("## 🥇 Gold — ไม่มีคนทาย\n")

    top5 = sorted(state["leaderboard"].values(), key=lambda x: -x["wins"])[:5]
    if top5:
        lines.append("## 📊 คะแนนสะสมปีนี้ (Top 5)")
        medals = ["🥇", "🥈", "🥉", "4.", "5."]
        for i, e in enumerate(top5):
            lines.append(f"{medals[i]} {e['username']} — **{e['wins']} wins**")
        lines.append("")

    lines.append("รอบใหม่เริ่ม **จันทร์เช้า 09:00** 🎮")
    lines.append("-# ยาม AI • Prediction Game")

    send_message("\n".join(lines))

    state["history"].append({
        "week": round_["week"],
        "btc_actual": btc_actual,
        "gold_actual": gold_actual,
        "btc_winner": btc_ranked[0][1]["username"] if btc_ranked else None,
        "gold_winner": gold_ranked[0][1]["username"] if gold_ranked else None,
        "btc_count": len(btc_preds),
        "gold_count": len(gold_preds),
    })
    state["history"] = state["history"][-52:]
    round_["status"] = "announced"
    save_state(state)
    print(f"[announce] winners announced")


# ============================================================
# MAIN
# ============================================================

def main():
    now = datetime.now(TZ)
    state = load_state()
    weekday = now.weekday()  # 0=Mon
    hour = now.hour
    minute = now.minute

    # 1. เปิดรอบ: จันทร์ 09:00-09:30
    if True:
        current = state.get("current_round")
        current_week = current["week"] if current else None
        this_week = get_week_string(now)
        if current_week != this_week:
            open_round(state, now)
            return

    # 2. ปิดรอบ: พุธ 23:45-23:59
    if weekday == 2 and hour == 23 and minute >= 45:
        current = state.get("current_round")
        if current and current["status"] == "open":
            close_round(state)
            return

    # 3. ประกาศ: เสาร์ 09:00-09:30
    if weekday == 5 and hour == 9 and minute < 30:
        current = state.get("current_round")
        if current and current["status"] == "closed":
            announce_winners(state)
            return

    # 4. Poll ข้อความ (ทำเสมอถ้ารอบเปิดอยู่)
    current = state.get("current_round")
    if current and current["status"] == "open":
        poll_predictions(state)
        save_state(state)

    print("[ok] done")


if __name__ == "__main__":
    main()
