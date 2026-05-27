# 🛡️ Guardian AI — OpenAI Multimodal Edition

سیستم پایش هوشمند، صوتی و امنیتی مبتنی بر **Flask + ESP32 + OpenAI (GPT-5-mini / gpt-4o-mini-tts / Whisper)**.

## 🧠 قابلیت‌های هوشمند OpenAI

| قابلیت | مدل OpenAI | فایل |
|--------|-----------|------|
| **Chat / Reasoning** | `gpt-5-mini` | `voice/ai_chat.py` |
| **Text-to-Speech** | `gpt-4o-mini-tts` | `voice/tts.py` |
| **Speech-to-Text** | `whisper-1` | `voice/stt.py` |
| **Push-to-Talk** | مرورگر `getUserMedia` | `static/voice.js` |
| **System Prompts** | — | `prompts/system.txt` |
| **Alarm Responses** | — | `prompts/alarm_responses.json` |

## 📁 ساختار پروژه

```
guardian_ai_project/
├── .env                    ← 🔐 کلیدهای حساس (API Key, WiFi, ...)
├── .env.example            ← نمونه .env
├── .gitignore
│
├── app.py                  ← Entry point: Flask Server + API
├── config.py               ← تنظیمات سراسری (خواندن از .env)
├── config.json             ← تنظیمات runtime JSON
├── requirements.txt        ← وابستگی‌ها
├── README.md               ← همین فایل
│
├── core/                   ← هسته نظارتی
│   ├── __init__.py
│   ├── state.py            ← SharedState (Thread-safe)
│   └── guardian.py         ← GuardianCore + AlertService + AI Chat
│
├── services/               ← سرویس‌های خارجی
│   ├── __init__.py
│   └── esp32.py            ← ESP32Client (HTTP → میکروکنترلر)
│
├── voice/                  ← 🔊 پایپ‌لاین صوتی + AI
│   ├── __init__.py
│   ├── ai_chat.py          ← GPT-5-mini Chat Client (GapGPT bridge)
│   ├── stt.py              ← Whisper API (صوت → متن)
│   ├── tts.py              ← OpenAI TTS API (متن → صوت)
│   └── __init__.py         ← Graceful imports (mock if sounddevice missing)
│
├── prompts/                ← 📝 پرامپت‌های جدا و قابل ویرایش
│   ├── system.txt          ← شخصیت و نقش Guardian AI
│   ├── alarm_responses.json  ← پاسخ FA/EN به آلارم‌ها
│   └── intents.json        ← راهنمای intent recognition
│
├── utils/                  ← ابزارهای کمکی
│   ├── __init__.py
│   └── logger.py           ← لاگ‌گیری لایه‌ای (console + rotating files)
│
├── logs/                   ← فایل‌های لاگ (rotating daily)
│
├── static/                 ← فایل‌های وب
│   ├── alarm.mp3
│   ├── alarm.wav
│   ├── voice.js            ← Push-to-Talk با getUserMedia
│   └── دنا.jpg
│
├── templates/
│   └── index.html          ← داشبورد SPA-like فارسی/انگلیسی
│
├── esp32/                  ← 🔌 فریم‌ورک ESP32 (MicroPython)
│   ├── main.py             ← WiFi + سنسورها + HTTP Server
│   ├── urequests.py        ← HTTP Client ساده
│   └── README_ESP32.md     ← راهنمای فلش و پین‌اوت
│
└── tests/                  ← 🧪 تست‌های جامع (pytest)
    ├── conftest.py         ← fixtures (mock OpenAI, Flask client)
    ├── test_app.py         ← تست API routes
    ├── test_core/
    │   ├── test_state.py   ← تست thread-safe state
    │   └── test_guardian.py  ← تست alarm evaluation + TTS fallback
    ├── test_services/
    │   └── test_esp32.py   ← تست ESP32Client (mocked requests)
    ├── test_voice/
    │   └── test_ai_chat.py  ← تست GPT client (mocked OpenAI)
    └── test_integration/
        └── test_end_to_end.py  ← تست جریان کامل sensor → alarm → API
```

## ⚡ راه‌اندازی سریع

### ۱. نصب وابستگی‌ها

```bash
pip install -r requirements.txt
```

### ۲. تنظیم `.env`

```bash
cp .env.example .env
nano .env
```

**حداقل مقادیر مورد نیاز:**

```env
OPENAI_API_KEY=your-gapgpt-api-key
OPENAI_BASE_URL=https://api.gapgpt.app/v1
OPENAI_CHAT_MODEL=gpt-5-mini
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=shimmer
OPENAI_STT_MODEL=whisper-1
ESP32_BASE_URL=http://192.168.43.219
```

> **توجه:** فایل `.env` در `.gitignore` قرار دارد و هرگز commit نمی‌شود.

### ۳. اجرای بک‌اند

```bash
python app.py
```

سرور روی `http://127.0.0.1:5000` بالا می‌آید.

### ۴. Push-to-Talk از مرورگر

1. داشبورد را باز کنید: `http://127.0.0.1:5000`
2. روی دکمه **🎤 نگه دارید و صحبت کنید** کلیک کنید و نگه دارید
3. صحبت کنید و رها کنید
4. صدا ضبط شده به Whisper API فرستاده می‌شود → GPT-5-mini پاسخ می‌دهد → TTS صدا را پخش می‌کند

### ۵. فلش ESP32

به پوشه `esp32/` بروید و طبق `README_ESP32.md` عمل کنید.

## 🔌 API های اصلی

| Endpoint | Method | توضیحات |
|----------|--------|---------|
| `/` | GET | داشبورد |
| `/api/health` | GET | وضعیت سرویس‌ها (AI, ESP32, ...) |
| `/api/data` | GET | داده خام سنسورها |
| `/api/guardian/chat` | POST | چت متنی با GPT |
| `/api/voice/transcribe` | POST | دریافت audio blob → Whisper → GPT → TTS |
| `/api/tts/speak` | POST | TTS متن |
| `/api/alarm/mute` | POST | بی‌صدا کردن آلارم |

## 🧪 اجرای تست‌ها

```bash
pytest tests/ -v
```

**۳۳ تست** در لایه‌های مختلف:
- **Unit:** state, guardian evaluation, alarm logic, AI chat
- **Service:** ESP32 HTTP client (mocked)
- **Integration:** sensor → alarm → Flask API

## 🧩 معماری OpenAI Multimodal

```
[User Voice] ──→ [Browser getUserMedia] ──→ [Audio Blob]
                                              ↓
                                         [Whisper API]
                                              ↓
                                         [GPT-5-mini]
                                              ↓
                                     [AI Response Text]
                                              ↓
                                    [OpenAI TTS API]
                                              ↓
                                     [Speaker Output]
```

## 🔧 قابلیت‌های جدید این نسخه

### ➕ OpenAI API Integration
- **GPT-5-mini** برای چت و reasoning (`voice/ai_chat.py`)
- **Whisper-1** برای STT (`voice/stt.py`)
- **gpt-4o-mini-tts** برای TTS (`voice/tts.py`)
- همه از طریق **GapGPT bridge** (`https://api.gapgpt.app/v1`)

### ➕ Voice from Browser (Push-to-Talk)
- `getUserMedia` + `MediaRecorder` در مرورگر
- فقط وقتی دکمه نگه داشته می‌شود صدا ضبط و به API فرستاده می‌شود (مقرون‌به‌صرفه)
- هیچ پردازش WakeWord محلی یا API مصرفی وجود ندارد

### ➕ لاگ‌گیری لایه‌ای (`utils/logger.py`)
- **Console:** INFO+
- **guardian.log:** INFO+ (rotating daily, ۷ روز نگهداری)
- **error.log:** ERROR+ (rotating, ۱۴ روز)
- **debug.log:** DEBUG+ (rotating, ۳ روز)

### ➕ تست‌های جامع (`tests/`)
- `pytest` + `pytest-flask`
- Mocked OpenAI SDK برای تست بدون API key واقعی
- Thread-safety test برای `SharedState`

### ➕ پرامپت‌های خارجی (`prompts/`)
- `system.txt` — قابل ویرایش بدون تغییر کد
- `alarm_responses.json` — پاسخ‌های چندزبانه به آلارم

### ➖ حذف‌شده
- `vosk` — STT حالا Whisper API است
- `pyttsx3` — TTS حالا OpenAI TTS API است
- `edge-tts` — حذف شد
- `pvporcupine` / `wakeword.py` — حذف شد (WakeWord با مرورگر Push-to-Talk جایگزین شد)

## 📝 نکات امنیتی

- **هرگز** `OPENAI_API_KEY` را در کد سخت‌کد نکنید — فقط در `.env`
- `.env` در `.gitignore` است
- اگر `.env` خالی باشد، سرویس‌های OpenAI با **fallback** (DummyTTS) اجرا می‌شوند
