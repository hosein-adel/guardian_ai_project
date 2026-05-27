# ================================================================
#  Guardian AI — ESP32 WROOM — MicroPython
#  نسخه 3.0 — بهینه‌شده با پین‌های اصلی پروژه
# ================================================================
#
#  پین‌های اصلی (دست نخورده از کد اولیه):
#  ┌──────────────────────────────────────────────────────┐
#  │  MQ9  Gas    AO  → GPIO34  (ADC1 — امن با WiFi) ✅  │
#  │  Flame IR    OUT → GPIO27  (Digital — Active LOW) ✅ │
#  │  PIR  Motion OUT → GPIO26  (Digital — Active HIGH)✅ │
#  │  Door Reed   SIG → GPIO25  (PULL_UP — LOW=بسته)  ✅ │
#  │  DS18B20     DQ  → GPIO4   (OneWire — 4.7kΩ PU) ✅  │
#  └──────────────────────────────────────────────────────┘
#
#  بهبودهای نسخه 3.0:
#  ✅ سیستم لاگ کامل با سطح‌بندی
#  ✅ PULL_DOWN برای PIR (رفع مشکل Floating)
#  ✅ Debounce + تأیید چندگانه برای PIR
#  ✅ فیلتر نور محیط برای Flame
#  ✅ Warm-up برای MQ9
#  ✅ Thread پس‌زمینه برای خواندن مداوم سنسورها
#  ✅ Endpoint های /diag /logs /history
#  ✅ get_ip() ایمن
# ================================================================

import network
import time
import ujson as json
import os
import sys
from machine import Pin, ADC

# ================================================================
#  سیستم لاگ
# ================================================================

LOG_DEBUG    = 0
LOG_INFO     = 1
LOG_WARNING  = 2
LOG_ERROR    = 3
LOG_CRITICAL = 4

_LOG_NAMES = {
    LOG_DEBUG:   "DEBUG   ",
    LOG_INFO:    "INFO    ",
    LOG_WARNING: "WARNING ",
    LOG_ERROR:   "ERROR   ",
    LOG_CRITICAL:"CRITICAL",
}

# سطح نمایش لاگ — برای production روی LOG_INFO بگذار
LOG_LEVEL = LOG_DEBUG

log_history  = []
MAX_LOG_HIST = 60


def log(level, module, msg):
    if level < LOG_LEVEL:
        return
    ts   = time.time()
    name = _LOG_NAMES.get(level, "UNKNOWN ")
    line = "[{:>10}] {} [{:<8}] {}".format(ts, name, module, msg)
    print(line)
    log_history.append({"ts": ts, "level": name.strip(),
                         "module": module, "msg": msg})
    if len(log_history) > MAX_LOG_HIST:
        log_history.pop(0)


def log_sep(title=""):
    n   = 54
    sep = "=== {} {}".format(title, "=" * max(0, n - len(title) - 5)) \
          if title else "=" * n
    print(sep)


# ================================================================
#  WiFi Config  ← دست نخورده از کد اصلی
# ================================================================

WIFI_SSID     = "Honor 8A"
WIFI_PASSWORD = "alialialiali"
WIFI_TIMEOUT  = 20

# ================================================================
#  Pin Config   ← دقیقاً همان کد اصلی شما
# ================================================================

MQ9_PIN     = 34   # Analog  — ADC1_CH6
FLAME_PIN   = 27   # Digital — Active LOW
PIR_PIN     = 26   # Digital — Active HIGH  ← GPIO26 با PULL_DOWN fix شد
DOOR_PIN    = 25   # Digital — PULL_UP
DS18B20_PIN = 4    # OneWire ← همان GPIO4 که دما درست کار می‌کرد ✅

# ================================================================
#  App Config   ← دست نخورده از کد اصلی + read_interval جدید
# ================================================================

DEFAULT_CONFIG = {
    "gas_threshold"  : 2000,
    "temp_threshold" : 50,
    "flame_enabled"  : True,
    "motion_enabled" : True,
    "door_enabled"   : True,
    "device_name"    : "Guardian ESP32",
    "read_interval"  : 2        # ثانیه — فاصله خواندن سنسورها در Thread
}

CONFIG_FILE = "config.json"

# ================================================================
#  State
# ================================================================

config           = {}
last_data        = {}
alarm_history    = []
last_alarm_state = False
wlan             = None

# ================================================================
#  Hardware Init
# ================================================================

log_sep("HARDWARE INIT")
log(LOG_INFO, "HW_INIT", "شروع راه‌اندازی سخت‌افزار...")

# ── MQ9 (GPIO34 = ADC1 — همیشه امن با WiFi) ──────────────────
mq9_adc = None
try:
    mq9_adc = ADC(Pin(MQ9_PIN))
    mq9_adc.atten(ADC.ATTN_11DB)    # محدوده 0-3.6V
    # width() در MicroPython 1.19+ حذف شده — با try/except مدیریت می‌شود
    try:
        mq9_adc.width(ADC.WIDTH_12BIT)
        log(LOG_INFO, "MQ9", "GPIO{} | ATTN_11DB | WIDTH_12BIT".format(MQ9_PIN))
    except Exception:
        # نسخه جدید MicroPython به‌صورت پیش‌فرض 12bit است
        log(LOG_INFO, "MQ9", "GPIO{} | ATTN_11DB | 12bit (auto)".format(MQ9_PIN))
except Exception as e:
    log(LOG_CRITICAL, "MQ9", "خطای ADC init: {} → گاز غیرفعال!".format(e))

# ── Flame (GPIO27 — ماژول Pull داخلی دارد) ──────────────────
flame_pin = None
try:
    # GPIO27 پین معمولی است (نه Input-Only) → می‌توان Pull گذاشت
    # اما ماژول‌های Flame معمولاً Pull-up داخلی روی DO دارند
    # بنابراین Pin.IN ساده کافی است
    flame_pin = Pin(FLAME_PIN, Pin.IN)
    log(LOG_INFO, "FLAME",
        "GPIO{} | Active LOW | فیلتر نور: {} سیکل".format(
            FLAME_PIN, 3))
except Exception as e:
    log(LOG_CRITICAL, "FLAME", "خطای init: {} → شعله غیرفعال!".format(e))

# ── PIR (GPIO26 — FIX: اضافه کردن PULL_DOWN) ─────────────────
pir_pin = None
try:
    # ❌ کد اصلی: Pin(PIR_PIN, Pin.IN)  ← Floating!
    # ✅ کد جدید: PULL_DOWN اضافه شد تا پین در حالت بدون سیگنال LOW بماند
    pir_pin = Pin(PIR_PIN, Pin.IN, Pin.PULL_DOWN)
    log(LOG_INFO, "PIR",
        "GPIO{} | PULL_DOWN | Active HIGH | Debounce فعال".format(PIR_PIN))
    log(LOG_WARNING, "PIR",
        "⏳ PIR نیاز به 30-60 ثانیه Calibration دارد پس از تغذیه!")
except Exception as e:
    log(LOG_CRITICAL, "PIR", "خطای init: {} → حرکت غیرفعال!".format(e))

# ── Door (GPIO25 — PULL_UP — همان کد اصلی) ───────────────────
door_pin = None
try:
    door_pin = Pin(DOOR_PIN, Pin.IN, Pin.PULL_UP)
    log(LOG_INFO, "DOOR",
        "GPIO{} | PULL_UP | LOW=بسته / HIGH=باز".format(DOOR_PIN))
except Exception as e:
    log(LOG_CRITICAL, "DOOR", "خطای init: {} → درب غیرفعال!".format(e))

# ── DS18B20 (GPIO4 — همان کد اصلی که درست کار می‌کرد) ────────
DS18B20_AVAILABLE = False
ds_sensor = None
ds_roms   = []

try:
    import onewire
    import ds18x20
    log(LOG_INFO, "DS18B20", "کتابخانه‌ها لود شدند ✅")
    try:
        ow        = onewire.OneWire(Pin(DS18B20_PIN))
        ds_sensor = ds18x20.DS18X20(ow)
        log(LOG_INFO, "DS18B20",
            "OneWire روی GPIO{} | اسکن...".format(DS18B20_PIN))
        ds_roms = ds_sensor.scan()
        if ds_roms:
            DS18B20_AVAILABLE = True
            log(LOG_INFO, "DS18B20",
                "{} سنسور | ROM={}".format(len(ds_roms), ds_roms[0]))
        else:
            log(LOG_ERROR, "DS18B20",
                "سنسوری یافت نشد! مقاومت 4.7kΩ بین DQ و 3.3V را بررسی کنید")
    except Exception as e:
        log(LOG_ERROR, "DS18B20", "خطای OneWire: {}".format(e))
except ImportError:
    log(LOG_WARNING, "DS18B20", "کتابخانه onewire یافت نشد")

log_sep("HW INIT DONE")


# ================================================================
#  Config Management  ← منطق همان کد اصلی + لاگ
# ================================================================

def load_config():
    global config
    log(LOG_INFO, "CONFIG", "بارگذاری تنظیمات...")
    try:
        if CONFIG_FILE in os.listdir():
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
            log(LOG_INFO, "CONFIG", "فایل {} لود شد".format(CONFIG_FILE))
        else:
            config = DEFAULT_CONFIG.copy()
            save_config()
            log(LOG_INFO, "CONFIG", "فایل یافت نشد — پیش‌فرض استفاده شد")
    except Exception as e:
        config = DEFAULT_CONFIG.copy()
        log(LOG_ERROR, "CONFIG", "خطای خواندن: {} → پیش‌فرض".format(e))

    added = []
    for key in DEFAULT_CONFIG:
        if key not in config:
            config[key] = DEFAULT_CONFIG[key]
            added.append(key)
    if added:
        log(LOG_WARNING, "CONFIG", "کلیدهای جدید: {}".format(added))
        save_config()

    log(LOG_DEBUG, "CONFIG", "تنظیمات: {}".format(config))


def save_config():
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f)
        log(LOG_DEBUG, "CONFIG", "ذخیره شد")
    except Exception as e:
        log(LOG_ERROR, "CONFIG", "خطای ذخیره: {}".format(e))


# ================================================================
#  WiFi  ← منطق همان کد اصلی + لاگ
# ================================================================

def connect_wifi():
    global wlan
    log_sep("WIFI")
    log(LOG_INFO, "WIFI", "اتصال به: {}".format(WIFI_SSID))
    try:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        time.sleep(0.5)

        if wlan.isconnected():
            log(LOG_INFO, "WIFI",
                "از قبل متصل | IP: {}".format(wlan.ifconfig()[0]))
            return wlan

        wlan.connect(WIFI_SSID, WIFI_PASSWORD)

        for i in range(WIFI_TIMEOUT):
            if wlan.isconnected():
                break
            log(LOG_DEBUG, "WIFI", "تلاش {}/{}...".format(i + 1, WIFI_TIMEOUT))
            time.sleep(1)

        if wlan.isconnected():
            cfg = wlan.ifconfig()
            log(LOG_INFO, "WIFI", "متصل شد ✅ | IP={} | GW={}".format(
                cfg[0], cfg[2]))
        else:
            log(LOG_ERROR, "WIFI", "اتصال ناموفق! SSID یا رمز را بررسی کنید")

    except Exception as e:
        log(LOG_CRITICAL, "WIFI", "خطا: {}".format(e))

    return wlan


def get_ip():
    try:
        if wlan and wlan.isconnected():
            return wlan.ifconfig()[0]
    except Exception:
        pass
    return "0.0.0.0"


# ================================================================
#  Sensor Readers
# ================================================================

def read_mq9():
    """
    GPIO34 | ADC1_CH6 | ATTN_11DB | Range: 0-4095
    بدون گاز: ~500-800 | با گاز: >2000
    """
    if mq9_adc is None:
        log(LOG_ERROR, "MQ9", "ADC آماده نیست!")
        return 0
    try:
        raw     = mq9_adc.read()
        voltage = round(raw * 3.6 / 4095, 3)
        thr     = config.get("gas_threshold", DEFAULT_CONFIG["gas_threshold"])
        log(LOG_DEBUG, "MQ9",
            "Raw={} | {}V | Thr={} | {}".format(
                raw, voltage, thr,
                "⚠️ بالای آستانه!" if raw >= thr else "نرمال"))
        return raw
    except Exception as e:
        log(LOG_ERROR, "MQ9", "خطای خواندن: {}".format(e))
        return 0


# ── Flame — فیلتر نور محیط ────────────────────────────────────
# مشکل: سنسور شعله به نور محیط (لامپ/آفتاب) هم واکنش نشان می‌دهد
# راه‌حل: فقط اگر N سیکل متوالی LOW بود = شعله واقعی
_flame_count     = 0
FLAME_CONFIRM    = 3   # تعداد سیکل برای تأیید (با read_interval=2s → 6 ثانیه)


def read_flame():
    """
    GPIO27 | Active LOW | فیلتر نور: FLAME_CONFIRM سیکل متوالی
    نور محیط: سیگنال متناوب  → تأیید نمی‌شود
    شعله واقعی: سیگنال پایدار → تأیید می‌شود
    """
    global _flame_count
    if flame_pin is None:
        log(LOG_ERROR, "FLAME", "پین آماده نیست!")
        return False
    try:
        raw = flame_pin.value()

        if raw == 0:       # LOW = سیگنال دریافت شد
            _flame_count += 1
        else:              # HIGH = سیگنال قطع شد
            if _flame_count > 0:
                log(LOG_DEBUG, "FLAME",
                    "سیگنال قطع شد | counter ریست (بود: {})".format(
                        _flame_count))
            _flame_count = 0

        confirmed = (_flame_count >= FLAME_CONFIRM)

        log(LOG_DEBUG, "FLAME",
            "Raw={} | Count={}/{} | Confirmed={}".format(
                raw, _flame_count, FLAME_CONFIRM, confirmed))

        if confirmed and _flame_count == FLAME_CONFIRM:
            log(LOG_WARNING, "FLAME",
                "🔥 شعله تأیید شد! ({} سیکل متوالی LOW)".format(
                    _flame_count))

        return confirmed

    except Exception as e:
        log(LOG_ERROR, "FLAME", "خطای خواندن: {}".format(e))
        return False


# ── PIR — Debounce + تأیید چندگانه ──────────────────────────
# مشکل اصلی: GPIO26 بدون PULL_DOWN → Floating → مقادیر تصادفی
# راه‌حل سخت‌افزاری: اضافه کردن PULL_DOWN در init (بالای کد انجام شد)
# راه‌حل نرم‌افزاری: Debounce + تأیید N بار متوالی HIGH
_pir_count       = 0
_pir_zero_count  = 0
PIR_CONFIRM      = 2   # تعداد سیکل HIGH برای تأیید حرکت
PIR_RESET        = 3   # تعداد سیکل LOW برای ریست (جلوگیری از Latch)


def read_motion():
    """
    GPIO26 | PULL_DOWN | Active HIGH
    FIX: در کد اصلی بدون PULL_DOWN بود → Floating → خطای تشخیص
    FIX: Debounce اضافه شد → جلوگیری از False Positive
    """
    global _pir_count, _pir_zero_count
    if pir_pin is None:
        log(LOG_ERROR, "PIR", "پین آماده نیست!")
        return False
    try:
        raw = pir_pin.value()

        if raw == 1:
            _pir_count     += 1
            _pir_zero_count = 0
        else:
            _pir_zero_count += 1
            # فقط اگر N سیکل متوالی LOW بود، counter را ریست کن
            if _pir_zero_count >= PIR_RESET:
                if _pir_count > 0:
                    log(LOG_DEBUG, "PIR",
                        "حرکت پایان یافت | counter ریست")
                _pir_count      = 0
                _pir_zero_count = 0

        confirmed = (_pir_count >= PIR_CONFIRM)

        log(LOG_DEBUG, "PIR",
            "Raw={} | HiCount={}/{} | LoCount={}/{} | Motion={}".format(
                raw, _pir_count, PIR_CONFIRM,
                _pir_zero_count, PIR_RESET, confirmed))

        if confirmed and _pir_count == PIR_CONFIRM:
            log(LOG_WARNING, "PIR",
                "🚶 حرکت تأیید شد! ({} سیکل متوالی HIGH)".format(
                    _pir_count))

        return confirmed

    except Exception as e:
        log(LOG_ERROR, "PIR", "خطای خواندن: {}".format(e))
        return False


def read_door():
    """
    GPIO25 | PULL_UP | LOW=بسته / HIGH=باز
    ← منطق دقیقاً همان کد اصلی
    """
    if door_pin is None:
        log(LOG_ERROR, "DOOR", "پین آماده نیست!")
        return False
    try:
        raw     = door_pin.value()
        is_open = (raw == 1)
        log(LOG_DEBUG, "DOOR",
            "Raw={} | Open={}".format(raw, is_open))
        if is_open:
            log(LOG_WARNING, "DOOR", "🚪 درب باز است!")
        return is_open
    except Exception as e:
        log(LOG_ERROR, "DOOR", "خطای خواندن: {}".format(e))
        return False


def read_temperature():
    """
    GPIO4 | OneWire | DS18B20
    ← دقیقاً همان کد اصلی که درست کار می‌کرد
    """
    if not DS18B20_AVAILABLE or ds_sensor is None or not ds_roms:
        log(LOG_DEBUG, "DS18B20",
            "غیرفعال (available={})".format(DS18B20_AVAILABLE))
        return None
    try:
        ds_sensor.convert_temp()
        time.sleep_ms(750)
        temp = ds_sensor.read_temp(ds_roms[0])
        if temp is None:
            log(LOG_ERROR, "DS18B20", "read_temp → None")
            return None
        result = round(float(temp), 2)
        log(LOG_DEBUG, "DS18B20",
            "{}°C | Thr={}°C".format(
                result,
                config.get("temp_threshold",
                            DEFAULT_CONFIG["temp_threshold"])))
        return result
    except Exception as e:
        log(LOG_ERROR, "DS18B20", "خطای خواندن: {}".format(e))
        return None


# ================================================================
#  Alarm Evaluator  ← منطق همان کد اصلی
# ================================================================

def evaluate_alarm(data):
    gas_thr  = config.get("gas_threshold",  DEFAULT_CONFIG["gas_threshold"])
    temp_thr = config.get("temp_threshold", DEFAULT_CONFIG["temp_threshold"])

    gas_alarm  = data["mq9"] >= gas_thr
    temp_alarm = (data["temperature"] is not None and
                  data["temperature"] >= temp_thr)

    flame_alarm  = config.get("flame_enabled",  True) and data["flame"]
    motion_alarm = config.get("motion_enabled", True) and data["motion"]
    door_alarm   = config.get("door_enabled",   True) and data["door_open"]

    reasons = {
        "gas_alarm"   : gas_alarm,
        "temp_alarm"  : temp_alarm,
        "flame_alarm" : flame_alarm,
        "motion_alarm": motion_alarm,
        "door_alarm"  : door_alarm,
    }

    alarm = any(reasons.values())

    if alarm:
        active = [k for k, v in reasons.items() if v]
        log(LOG_WARNING, "ALARM", "🚨 ALARM! دلایل: {}".format(active))
    else:
        log(LOG_DEBUG, "ALARM", "وضعیت سبز — بدون هشدار")

    return alarm, reasons


# ================================================================
#  Main Sensor Read  ← همان ساختار کد اصلی + لاگ + cache
# ================================================================

_mq9_warmup_done = False
MQ9_WARMUP_SEC   = 30


def read_sensors():
    """
    خواندن همه سنسورها — همان ساختار دقیق کد اصلی
    """
    global last_data, alarm_history, last_alarm_state

    log(LOG_DEBUG, "SENSORS", "── شروع خواندن ──")

    mq9_value        = read_mq9()
    temp_value       = read_temperature()
    flame_status     = read_flame()
    motion_status    = read_motion()
    door_open_status = read_door()

    gas_leak_status = mq9_value >= config.get(
        "gas_threshold", DEFAULT_CONFIG["gas_threshold"])

    data = {
        "mq9"           : mq9_value,
        "temperature"   : temp_value if temp_value is not None else 0,
        "humidity"      : 0,         # فعلاً سنسور رطوبت نداریم (همان کد اصلی)
        "gas_leak"      : gas_leak_status,
        "motion"        : motion_status,
        "door_open"     : door_open_status,
        "flame"         : flame_status,
        "device_name"   : config.get("device_name", DEFAULT_CONFIG["device_name"]),
        "ip"            : get_ip(),
        "timestamp"     : time.time(),
        "guardian_active": True,
        "alarm_muted"   : False,
        "warmup_done"   : _mq9_warmup_done,
        "ds18b20_active": DS18B20_AVAILABLE,
    }

    alarm, reasons = evaluate_alarm(data)
    data["alarm"]         = alarm
    data["alarm_reasons"] = reasons

    # ثبت تاریخچه فقط در لحظه تغییر False → True
    if alarm and not last_alarm_state:
        try:
            alarm_history.append({
                "timestamp": data["timestamp"],
                "reasons"  : reasons
            })
            if len(alarm_history) > 20:
                alarm_history = alarm_history[-20:]
            log(LOG_WARNING, "ALARM",
                "رویداد در تاریخچه ثبت شد (جمع: {})".format(
                    len(alarm_history)))
        except Exception as e:
            log(LOG_ERROR, "ALARM", "خطای تاریخچه: {}".format(e))

    last_alarm_state = alarm
    last_data        = data

    log(LOG_DEBUG, "SENSORS",
        "MQ9={} | T={}°C | Flame={} | PIR={} | Door={}".format(
            mq9_value, temp_value, flame_status,
            motion_status, door_open_status))
    log(LOG_DEBUG, "SENSORS", "── پایان خواندن ──")

    return data


# ================================================================
#  Background Sensor Loop — خواندن مداوم مستقل از HTTP
# ================================================================

def _sensor_loop():
    """
    Thread پس‌زمینه — مستقل از HTTP Server اجرا می‌شود
    اطمینان می‌دهد که alarm حتی بدون Request هم تشخیص داده می‌شود
    """
    global _mq9_warmup_done

    interval = config.get("read_interval", DEFAULT_CONFIG["read_interval"])
    log(LOG_INFO, "LOOP",
        "Thread شروع شد | فاصله: {}s".format(interval))

    # MQ9 Warm-up
    log(LOG_WARNING, "MQ9",
        "⏳ Warm-up شروع ({} ثانیه) — مقادیر گاز هنوز معتبر نیستند".format(
            MQ9_WARMUP_SEC))
    for i in range(MQ9_WARMUP_SEC):
        if i % 5 == 0 and i > 0:
            log(LOG_DEBUG, "MQ9",
                "Warm-up: {}/{} ثانیه...".format(i, MQ9_WARMUP_SEC))
        time.sleep(1)
    _mq9_warmup_done = True
    log(LOG_INFO, "MQ9", "✅ Warm-up کامل — مقادیر حالا معتبرند")

    # حلقه اصلی
    while True:
        try:
            read_sensors()
        except Exception as e:
            log(LOG_ERROR, "LOOP", "خطا: {}".format(e))
        interval = config.get("read_interval", DEFAULT_CONFIG["read_interval"])
        time.sleep(interval)


# ================================================================
#  HTTP Response Builder  ← همان کد اصلی
# ================================================================

def http_response(conn, status_code=200,
                  content_type="application/json", body=""):
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found",
              405: "Method Not Allowed",
              500: "Internal Server Error"}.get(status_code, "OK")

    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    if not isinstance(body, str):
        body = str(body)

    resp  = "HTTP/1.1 {} {}\r\n".format(status_code, reason)
    resp += "Content-Type: {}\r\n".format(content_type)
    resp += "Access-Control-Allow-Origin: *\r\n"
    resp += "Connection: close\r\n"
    resp += "Content-Length: {}\r\n".format(len(body))
    resp += "\r\n"
    resp += body

    try:
        conn.send(resp.encode("utf-8"))
    except Exception:
        try:
            conn.send(resp)
        except Exception as e:
            log(LOG_ERROR, "HTTP", "خطای send: {}".format(e))


# ================================================================
#  Request Parser  ← همان کد اصلی + لاگ
# ================================================================

def parse_request(client_conn):
    try:
        request = client_conn.recv(2048)
        if not request:
            return None, None, None, None

        try:
            req_text = request.decode("utf-8")
        except Exception:
            req_text = request.decode("latin-1")

        lines = req_text.split("\r\n")
        if not lines or len(lines[0].split()) < 2:
            return None, None, None, None

        parts  = lines[0].split()
        method = parts[0]
        path   = parts[1]

        headers = {}
        i = 1
        while i < len(lines) and lines[i]:
            if ":" in lines[i]:
                k, v = lines[i].split(":", 1)
                headers[k.strip().lower()] = v.strip()
            i += 1

        body = ""
        if "\r\n\r\n" in req_text:
            body = req_text.split("\r\n\r\n", 1)[1]

        log(LOG_DEBUG, "HTTP", "← {} {}".format(method, path))
        return method, path, headers, body

    except Exception as e:
        log(LOG_ERROR, "HTTP", "خطای parse: {}".format(e))
        return None, None, None, None


# ================================================================
#  HTTP Handlers
# ================================================================

def handle_root(conn):
    http_response(conn, 200, "application/json", {
        "message"    : "Guardian ESP32 API is running ✅",
        "device_name": config.get("device_name", DEFAULT_CONFIG["device_name"]),
        "ip"         : get_ip(),
        "warmup_done": _mq9_warmup_done,
        "ds18b20"    : DS18B20_AVAILABLE,
        "endpoints"  : {
            "GET /data"   : "خواندن سنسورها",
            "GET /config" : "نمایش تنظیمات",
            "POST /config": "بروزرسانی تنظیمات",
            "GET /logs"   : "لاگ‌ها",
            "GET /history": "تاریخچه alarm",
            "GET /diag"   : "دیاگنوز سخت‌افزار",
        }
    })


def handle_data(conn):
    """
    اگر cache دارد → از cache بده (سریع‌تر)
    اگر cache ندارد → مستقیم بخوان
    """
    try:
        data = last_data if last_data else read_sensors()
        http_response(conn, 200, "application/json", data)
    except Exception as e:
        log(LOG_ERROR, "HTTP", "handle_data خطا: {}".format(e))
        http_response(conn, 500, "application/json", {
            "error": "failed_to_read_sensors", "details": str(e)
        })


def handle_config_get(conn):
    try:
        http_response(conn, 200, "application/json", config)
    except Exception as e:
        http_response(conn, 500, "application/json",
                      {"error": "failed_to_get_config", "details": str(e)})


def handle_config_post(conn, body):
    global config
    try:
        if not body:
            http_response(conn, 400, "application/json",
                          {"success": False, "error": "empty body"})
            return

        new_cfg = json.loads(body)
        if not isinstance(new_cfg, dict):
            http_response(conn, 400, "application/json",
                          {"success": False, "error": "invalid json"})
            return

        changed = []
        for key in DEFAULT_CONFIG:
            if key in new_cfg:
                old = config.get(key)
                config[key] = new_cfg[key]
                if old != new_cfg[key]:
                    changed.append("{}:{} → {}".format(key, old, new_cfg[key]))

        save_config()
        if changed:
            log(LOG_INFO, "CONFIG", "تغییرات: {}".format(changed))

        http_response(conn, 200, "application/json", {
            "success": True, "changed": changed, "config": config
        })

    except Exception as e:
        log(LOG_ERROR, "CONFIG", "خطای post: {}".format(e))
        http_response(conn, 500, "application/json",
                      {"success": False, "error": str(e)})


def handle_logs(conn):
    try:
        http_response(conn, 200, "application/json", {
            "total": len(log_history),
            "logs" : log_history
        })
    except Exception as e:
        http_response(conn, 500, "application/json", {"error": str(e)})


def handle_history(conn):
    try:
        http_response(conn, 200, "application/json", {
            "total"  : len(alarm_history),
            "history": alarm_history
        })
    except Exception as e:
        http_response(conn, 500, "application/json", {"error": str(e)})


def handle_diag(conn):
    """
    دیاگنوز کامل سخت‌افزار — برای دیباگ از مرورگر
    GET /diag
    """
    try:
        diag = {
            "mq9": {
                "pin"        : MQ9_PIN,
                "ready"      : mq9_adc is not None,
                "warmup_done": _mq9_warmup_done,
                "raw"        : read_mq9() if mq9_adc else -1,
                "type"       : "ADC1_CH6 (امن با WiFi)",
            },
            "flame": {
                "pin"        : FLAME_PIN,
                "ready"      : flame_pin is not None,
                "raw"        : flame_pin.value() if flame_pin else None,
                "count"      : _flame_count,
                "confirm_thr": FLAME_CONFIRM,
                "detected"   : read_flame() if flame_pin else None,
                "logic"      : "Active LOW | فیلتر نور فعال",
            },
            "pir": {
                "pin"        : PIR_PIN,
                "ready"      : pir_pin is not None,
                "raw"        : pir_pin.value() if pir_pin else None,
                "hi_count"   : _pir_count,
                "confirm_thr": PIR_CONFIRM,
                "detected"   : _pir_count >= PIR_CONFIRM,
                "logic"      : "Active HIGH | PULL_DOWN | Debounce",
                "fix"        : "PULL_DOWN اضافه شد — Floating برطرف شد",
            },
            "door": {
                "pin"    : DOOR_PIN,
                "ready"  : door_pin is not None,
                "raw"    : door_pin.value() if door_pin else None,
                "is_open": read_door() if door_pin else None,
                "logic"  : "PULL_UP | LOW=بسته / HIGH=باز",
            },
            "ds18b20": {
                "pin"      : DS18B20_PIN,
                "available": DS18B20_AVAILABLE,
                "rom"      : str(ds_roms[0]) if ds_roms else None,
                "temp_c"   : read_temperature(),
                "note"     : "GPIO4 — همان پین اصلی که درست کار می‌کرد",
            },
            "wifi": {
                "connected": wlan.isconnected() if wlan else False,
                "ip"       : get_ip(),
                "ssid"     : WIFI_SSID,
            },
            "config": config,
        }
        log(LOG_INFO, "DIAG", "درخواست دیاگنوز پاسخ داده شد")
        http_response(conn, 200, "application/json", diag)
    except Exception as e:
        http_response(conn, 500, "application/json", {"error": str(e)})


# ================================================================
#  HTTP Server  ← همان ساختار کد اصلی + endpoint های جدید
# ================================================================

def start_server():
    import socket
    log_sep("HTTP SERVER")
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s    = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(5)

    ip = get_ip()
    log(LOG_INFO, "HTTP", "سرور روی پورت 80 | IP: {}".format(ip))
    log(LOG_INFO, "HTTP", "دیاگنوز : http://{}/diag".format(ip))
    log(LOG_INFO, "HTTP", "لاگ‌ها  : http://{}/logs".format(ip))
    log(LOG_INFO, "HTTP", "داده‌ها : http://{}/data".format(ip))

    while True:
        conn, addr = s.accept()
        log(LOG_DEBUG, "HTTP", "اتصال از {}".format(addr[0]))
        try:
            method, path, headers, body = parse_request(conn)

            if method is None:
                http_response(conn, 400, "application/json",
                              {"error": "bad request"})
            elif method == "OPTIONS":
                http_response(conn, 200, "text/plain", "")
            elif method == "GET"  and path == "/":
                handle_root(conn)
            elif method == "GET"  and path == "/data":
                handle_data(conn)
            elif method == "GET"  and path == "/config":
                handle_config_get(conn)
            elif method == "POST" and path == "/config":
                handle_config_post(conn, body)
            elif method == "GET"  and path == "/logs":
                handle_logs(conn)
            elif method == "GET"  and path == "/history":
                handle_history(conn)
            elif method == "GET"  and path == "/diag":
                handle_diag(conn)
            else:
                log(LOG_WARNING, "HTTP",
                    "مسیر یافت نشد: {} {}".format(method, path))
                http_response(conn, 404, "application/json",
                              {"error": "not found", "path": path})

        except Exception as e:
            log(LOG_ERROR, "HTTP", "خطای handler: {}".format(e))
            try:
                http_response(conn, 500, "application/json",
                              {"error": "server_error", "details": str(e)})
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ================================================================
#  Main  ← همان ساختار کد اصلی + Thread
# ================================================================

log_sep("GUARDIAN BOOT")
log(LOG_INFO, "MAIN", "🛡️  Guardian AI ESP32 v3.0")
log(LOG_INFO, "MAIN", "MicroPython: {}".format(sys.version))

load_config()
wlan = connect_wifi()
time.sleep(1)

# شروع Thread پس‌زمینه سنسورها
try:
    import _thread
    _thread.start_new_thread(_sensor_loop, ())
    log(LOG_INFO, "MAIN", "Thread سنسور شروع شد ✅")
except Exception as e:
    log(LOG_ERROR, "MAIN",
        "Thread شروع نشد: {} → خواندن اولیه مستقیم".format(e))
    try:
        read_sensors()
    except Exception as e2:
        log(LOG_WARNING, "MAIN", "خواندن اولیه: {}".format(e2))

log_sep("BOOT DONE")
start_server()