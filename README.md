# 🤖 Discord Finance News Bot — Deployment Guide

บอทนี้จะรันทุกวันเวลา **08:00 น. (เวลาไทย)** ผ่าน GitHub Actions
ดึงข่าวเศรษฐกิจจาก 5 แหล่งใหญ่ → ใช้ Claude สรุปเป็นภาษาไทย → โพสเข้า Discord

**ฟรี 100%** ตราบใดที่รันวันละครั้ง (GitHub Actions ให้ 2000 นาที/เดือน)

---

## 🚀 ขั้นตอน Deploy (10 นาที)

### 1. สร้าง GitHub repo
1. เข้า https://github.com/new
2. ตั้งชื่ออะไรก็ได้ เช่น `discord-finance-bot`
3. เลือก **Private** (แนะนำ)
4. กด **Create repository**

### 2. อัปโหลดไฟล์
อัปโหลดไฟล์ทั้งหมดในโฟลเดอร์ `auto-poster` เข้า repo (รวม `.github/workflows/daily-news.yml`)

**หรือใช้คำสั่ง git:**
```bash
cd auto-poster
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/discord-finance-bot.git
git push -u origin main
```

### 3. ตั้ง Secrets
เข้า repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

เพิ่ม 2 ตัว:

| Name | Value |
|------|-------|
| `DISCORD_WEBHOOK_URL` | Webhook URL ห้องข่าวเศรษฐกิจของคุณ |
| `ANTHROPIC_API_KEY`   | API key จาก https://console.anthropic.com/settings/keys |

> **หมายเหตุ**: Anthropic API มีฟรี tier แต่จำกัด ถ้ารันวันละครั้งใช้ประมาณ **$0.01–0.03/วัน** (~1 บาท) เติมเครดิตขั้นต่ำ $5 ใช้ได้เกือบทั้งปี

### 4. เปิด Actions
เข้าแถบ **Actions** ที่ด้านบนของ repo → กด **I understand my workflows, go ahead and enable them**

### 5. ทดสอบยิงเลย
- ไปแถบ **Actions** → เลือก **Daily Discord Finance News** ที่แถบซ้าย
- กด **Run workflow** → **Run workflow** (ปุ่มเขียว)
- รอ ~1 นาที ดู log แล้วเช็คห้อง Discord

เรียบร้อย! หลังจากนี้บอทจะรันเองทุก 08:00 น. เวลาไทย

---

## 🛠️ ปรับแต่ง

### เปลี่ยนเวลาที่รัน
แก้ `.github/workflows/daily-news.yml` บรรทัด `cron:`
```yaml
- cron: '0 1 * * *'   # 08:00 น. เวลาไทย
- cron: '0 0 * * *'   # 07:00 น. เวลาไทย
- cron: '0 12 * * *'  # 19:00 น. เวลาไทย
```
> ⚠️ ใช้ UTC (เวลาไทย = UTC+7)

### รันหลายรอบต่อวัน
```yaml
schedule:
  - cron: '0 1 * * *'    # 08:00 เช้า
  - cron: '30 8 * * *'   # 15:30 (ตลาดหุ้นเปิด)
  - cron: '0 13 * * *'   # 20:00 (ตลาดสหรัฐเปิด)
```

### เพิ่ม/ลดคีย์เวิร์ด
แก้ตัวแปร `KEYWORDS` ใน `daily_news.py`

### เพิ่มแหล่งข่าว
แก้ตัวแปร `FEEDS` ใน `daily_news.py`

---

## ❓ Troubleshooting

- **Actions รันแต่ไม่มีข้อความเข้า Discord** → เช็คว่า `DISCORD_WEBHOOK_URL` ถูกต้อง
- **Error: anthropic auth** → เช็ค `ANTHROPIC_API_KEY` และเครดิตในบัญชี
- **"nothing to post today"** → ไม่มีข่าวเข้าเกณฑ์ในช่วง 24 ชม. (ปกติในวันหยุด)
