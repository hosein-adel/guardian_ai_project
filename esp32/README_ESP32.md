# 🔌 Guardian ESP32 Firmware (MicroPython)

این پوشه شامل فریم‌ورک میکروکنترلر **ESP32** برای پروژه Guardian AI است.

## 📁 فایل‌ها

| فایل | توضیحات |
|------|---------|
| `main.py` | کد اصلی: اتصال WiFi + خواندن سنسورها + Minimal HTTP Server |
| `urequests.py` | کتابخانه HTTP Client ساده‌شده برای MicroPython |

## 🔌 پین‌اوت سنسورها

| سنسور | پین ESP32 | نوع |
|-------|-----------|-----|
| MQ9 (گاز) | GPIO 34 | آنالوگ (ADC) |
| Flame (شعله) | GPIO 27 | دیجیتال |
| PIR (حرکت) | GPIO 26 | دیجیتال |
| Door (درب) | GPIO 25 | دیجیتال (Pull-Up) |
| DS18B20 (دما) | GPIO 4 | OneWire (اختیاری) |

## ⚙️ تنظیمات WiFi

در ابتدای `main.py` مقادیر زیر را تغییر دهید:

```python
WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
```

## 📡 API Endpoints

پس از فلش و ریبوت، ESP32 روی پورت 80 گوش می‌دهد:

| Endpoint | Method | خروجی |
|----------|--------|-------|
| `GET /` | GET | اطلاعات دستگاه و لیست endpointها |
| `GET /data` | GET | داده‌های خام سنسورها + وضعیت آلارم |
| `GET /config` | GET | تنظیمات فعلی |
| `POST /config` | POST | بروزرسانی تنظیمات (JSON body) |

### مثال پاسخ `/data`

```json
{
  "mq9": 150,
  "temperature": 24.5,
  "gas_leak": false,
  "motion": false,
  "door_open": false,
  "flame": false,
  "alarm": false,
  "alarm_reasons": {
    "gas_alarm": false,
    "temp_alarm": false,
    "flame_alarm": false,
    "motion_alarm": false,
    "door_alarm": false
  },
  "device_name": "Guardian ESP32",
  "ip": "192.168.43.219",
  "timestamp": 1716739200
}
```

## 🔧 فلش کردن روی ESP32

1. **نصب MicroPython** روی ESP32:
   ```bash
   esptool.py --chip esp32 erase_flash
   esptool.py --chip esp32 write_flash -z 0x1000 esp32-20220618-v1.19.1.bin
   ```

2. **انتقال فایل‌ها** با `ampy` یا `rshell`:
   ```bash
   ampy --port /dev/ttyUSB0 put main.py
   ampy --port /dev/ttyUSB0 put urequests.py
   ```

3. **ریبوت** ESP32. پس از اتصال WiFi، IP را در ترمینال می‌بینید.

## ⚠️ نکات

- DS18B20 اختیاری است. اگر متصل نباشد، دما `0` برمی‌گردد.
- فایل `config.json` روی SPIFFS ذخیره می‌شود و در ریبوت‌ها حفظ می‌شود.
- ماژول شعله (Flame) معمولاً در تشخیص شعله **LOW** می‌شود.
