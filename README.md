# Real-Time Fraud Monitoring System for Payment Switch
*(ISO 20022 & REST API JSON)*

ระบบตรวจจับการทุจริตแบบเรียลไทม์ สำหรับระบบ Payment Switch รองรับทั้งข้อความมาตรฐาน **ISO 20022 (XML)** และ **REST API (JSON)**

---

## 📁 โครงสร้างโปรเจกต์ (Project Structure)

```text
D:\Dev_By_Eng\Fraud-monitor\
├── GUIDE_AND_REQUIREMENTS.txt    # เอกสารข้อกำหนดและสถาปัตยกรรมระบบอย่างละเอียด
├── docker-compose.yml            # Docker สำหรับ Kafka, Redis, ClickHouse, Grafana
├── requirements.txt              # รายชื่อ Python libraries ทั้งหมด
├── .env                          # ไฟล์ตั้งค่า Environment (Port, Telegram Token, ฯลฯ)
│
├── config/
│   ├── vector.yaml               # ตั้งค่า Vector สำหรับดึง Log ไฟล์ของ Switching เข้า Kafka
│   └── clickhouse_init.sql       # สคริปต์สร้าง Database และตารางใน ClickHouse
│
├── src/
│   ├── common/
│   │   ├── models.py             # Data Model (Transaction, FraudAlert)
│   │   └── logger.py             # ระบบบันทึก Log
│   ├── ingestion/
│   │   └── parser.py             # ตัวแปลงข้อมูล ISO 20022 (pacs.008) และ REST JSON
│   ├── engine/
│   │   ├── redis_velocity.py     # ตัวนับความถี่แบบ Real-time Sliding Window บน Redis
│   │   ├── rules.py              # กฎตรวจสอบแบบตรงไปตรงมา (Blacklist, ยอดเงินสูง, ความถี่)
│   │   ├── ai_model.py           # โมเดล AI ตรวจจับพฤติกรรมผิดปกติ (Isolation Forest)
│   │   └── clickhouse_writer.py  # ตัวบันทึกข้อมูลธุรกรรมและแจ้งเตือนลง ClickHouse
│   ├── alert/
│   │   └── telegram_alert.py     # ตัวยิงข้อความแจ้งเตือนเข้า Telegram Bot
│   └── main_pipeline.py          # ตัวควบคุม Pipeline การประมวลผลทั้งหมด
│
└── tests/
    ├── sample_pacs008.xml        # ตัวอย่างไฟล์ทดสอบ ISO 20022 pacs.008
    ├── sample_rest.json          # ตัวอย่างไฟล์ทดสอบ REST JSON
    └── simulate_traffic.py       # สคริปต์จำลองธุรกรรมปกติและยิงรูปแบบการโกงเพื่อทดสอบ
```

---

## 🚀 วิธีเริ่มต้นใช้งาน (Quick Start)

### 1. ติดตั้ง Python และ Dependencies

ติดตั้ง Python 3.10 ขึ้นไป จากนั้นรัน:
```bash
pip install -r requirements.txt
```

### 2. รัน Infrastructure ด้วย Docker (Kafka, Redis, ClickHouse, Grafana)

```bash
docker compose up -d
```

ตรวจสอบสถานะ Containers:
```bash
docker compose ps
```

* **Kafka Broker**: `localhost:9092`
* **Redis**: `localhost:6379`
* **ClickHouse HTTP**: `http://localhost:8123`
* **Grafana Dashboard**: `http://localhost:3001` (User: `admin` / Password: `admin`)

---

### 3. ทดสอบการทำงานของระบบ (Simulation Test)

รันสคริปต์จำลองธุรกรรมเพื่อดูการตรวจจับและตัดเกรดความเสี่ยงแบบสดๆ:

```bash
python tests/simulate_traffic.py
```

สคริปต์จะทดสอบ 6 รูปแบบ:
1. ธุรกรรมการโอนปกติในชีวิตประจำวัน (คะแนนความเสี่ยงต่ำ: `0 - 20`)
2. ธุรกรรมมาตรฐาน **ISO 20022 pacs.008 XML**
3. **การโกงแบบที่ 1 (High Velocity Burst)**: บัญชีเดียวกันโอนเงินรัวๆ 7 ครั้งในไม่กี่วินาที (ระบบจะตรวจจับด้วย Redis และส่ง Alert)
4. **การโกงแบบที่ 2 (Large Amount)**: การโอนยอดเงินสูงผิดปกติเกินเกณฑ์ที่กำหนด (เช่น 95,000,000 กีบ)
5. **การโกงแบบที่ 3 (Blacklisted Account)**: บัญชีติด Blacklist พยายามโอนเงิน (ถูกบล็อก/เตือนทันที 100 คะแนน)
6. **การโกงแบบที่ 4 (Off-Hours Anomaly)**: การโอนยอดเงินสูงช่วงเวลาตี 3:15 น. (AI Anomaly Detection ดักจับได้)

---

### 4. รันระบบจริงเพื่อรอรับข้อมูลจาก Kafka

เมื่อพร้อมเชื่อมต่อกับระบบจริง:
```bash
python src/main_pipeline.py
```

---

## 🔔 การเปิดใช้งานแจ้งเตือนผ่าน Telegram

เปิดไฟล์ `.env` แล้วแก้ไข:
```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=ใส่_Bot_Token_ที่ได้จาก_BotFather
TELEGRAM_CHAT_ID=ใส่_Chat_ID_ของกลุ่มหรือส่วนตัว
```
