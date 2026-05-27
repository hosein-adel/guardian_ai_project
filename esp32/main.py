# ================================================================
#  Guardian AI — ESP32 WROOM — MicroPython v5.0
# ================================================================

import network, time, ujson as json, os, sys
from machine import Pin, ADC

# ================================================================
#  پین‌ها
# ================================================================
MQ9_PIN     = 34
FLAME_PIN   = 27
PIR_PIN     = 26
DOOR_PIN    = 25
DS18B20_PIN = 4

DEFAULT_CONFIG = {
    "gas_threshold" : 2000,
    "temp_threshold": 50,
    "flame_enabled" : True,
    "motion_enabled": True,
    "door_enabled"  : True,
    "device_name"   : "Guardian ESP32",
    "read_interval" : 2
}
CONFIG_FILE = "config.json"

WIFI_SSID     = "Honor 8A"
WIFI_PASSWORD = "alialialiali"

# ================================================================
#  سیستم لاگ جداگانه برای هر سنسور
# ================================================================
# هر سنسور یک buffer مجزا دارد
# سنسورهای سالم فقط WARNING/ERROR لاگ می‌دهند
# سنسورهای مشکل‌دار (PIR/Flame) لاگ DEBUG هم دارند

SENSOR_LOGS = {
    "MQ9"    : [],
    "FLAME"  : [],
    "PIR"    : [],
    "DOOR"   : [],
    "DS18B20": [],
    "SYSTEM" : [],
}
MAX_PER_SENSOR = 40

# سطح لاگ هر سنسور — سنسورهای سالم روی WARNING
SENSOR_LOG_LEVEL = {
    "MQ9"    : "WARNING",   # سالم است — فقط هشدار
    "FLAME"  : "DEBUG",     # مشکل‌دار — همه چیز
    "PIR"    : "DEBUG",     # مشکل‌دار — همه چیز
    "DOOR"   : "WARNING",   # سالم است — فقط هشدار
    "DS18B20": "WARNING",   # سالم است — فقط هشدار
    "SYSTEM" : "INFO",
}

LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}


def slog(sensor, level, msg):
    """
    لاگ جداگانه برای هر سنسور
    sensor : کلید SENSOR_LOGS
    level  : DEBUG / INFO / WARNING / ERROR
    msg    : متن
    """
    min_level = LEVELS.get(
        SENSOR_LOG_LEVEL.get(sensor, "INFO"), 1)
    if LEVELS.get(level, 0) < min_level:
        return

    ts  = time.time()
    rec = {"ts": ts, "lvl": level, "msg": msg}

    buf = SENSOR_LOGS.get(sensor)
    if buf is not None:
        buf.append(rec)
        if len(buf) > MAX_PER_SENSOR:
            buf.pop(0)

    # کنسول فقط WARNING به بالا یا DEBUG برای PIR/Flame
    print("[{}][{:<8}][{}] {}".format(ts, sensor, level, msg))


# ================================================================
#  State
# ================================================================
config           = {}
last_data        = {}
alarm_history    = []
last_alarm_state = False
wlan             = None
_mq9_warmup_done = False

# ================================================================
#  سخت‌افزار
# ================================================================
slog("SYSTEM", "INFO", "=== راه‌اندازی سخت‌افزار ===")

# MQ9
mq9_adc = None
try:
    mq9_adc = ADC(Pin(MQ9_PIN))
    mq9_adc.atten(ADC.ATTN_11DB)
    try:    mq9_adc.width(ADC.WIDTH_12BIT)
    except: pass
    slog("MQ9", "INFO", "GPIO{} آماده".format(MQ9_PIN))
except Exception as e:
    slog("MQ9", "ERROR", "init خطا: {}".format(e))

# Flame
flame_pin = None
try:
    flame_pin = Pin(FLAME_PIN, Pin.IN)
    slog("FLAME", "INFO",
         "GPIO{} | Active LOW | Hysteresis".format(FLAME_PIN))
except Exception as e:
    slog("FLAME", "ERROR", "init خطا: {}".format(e))

# PIR
pir_pin = None
try:
    pir_pin = Pin(PIR_PIN, Pin.IN, Pin.PULL_DOWN)
    slog("PIR", "INFO",
         "GPIO{} | PULL_DOWN | H-mode".format(PIR_PIN))
except Exception as e:
    slog("PIR", "ERROR", "init خطا: {}".format(e))

# Door
door_pin = None
try:
    door_pin = Pin(DOOR_PIN, Pin.IN, Pin.PULL_UP)
    slog("DOOR", "INFO", "GPIO{} | PULL_UP".format(DOOR_PIN))
except Exception as e:
    slog("DOOR", "ERROR", "init خطا: {}".format(e))

# DS18B20
DS18B20_AVAILABLE = False
ds_sensor = None
ds_roms   = []
try:
    import onewire, ds18x20
    ow        = onewire.OneWire(Pin(DS18B20_PIN))
    ds_sensor = ds18x20.DS18X20(ow)
    ds_roms   = ds_sensor.scan()
    if ds_roms:
        DS18B20_AVAILABLE = True
        slog("DS18B20", "INFO",
             "GPIO{} | {} سنسور".format(DS18B20_PIN, len(ds_roms)))
    else:
        slog("DS18B20", "ERROR",
             "سنسوری پیدا نشد! مقاومت 4.7kΩ را بررسی کن")
except Exception as e:
    slog("DS18B20", "ERROR", "init خطا: {}".format(e))

slog("SYSTEM", "INFO", "=== سخت‌افزار آماده ===")

# ================================================================
#  Config
# ================================================================
def load_config():
    global config
    try:
        if CONFIG_FILE in os.listdir():
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
        else:
            config = DEFAULT_CONFIG.copy()
            save_config()
    except Exception:
        config = DEFAULT_CONFIG.copy()
    for k in DEFAULT_CONFIG:
        if k not in config:
            config[k] = DEFAULT_CONFIG[k]
    slog("SYSTEM", "INFO", "Config: {}".format(config))

def save_config():
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f)
    except Exception as e:
        slog("SYSTEM", "ERROR", "save config: {}".format(e))

# ================================================================
#  WiFi
# ================================================================
def connect_wifi():
    global wlan
    slog("SYSTEM", "INFO", "WiFi: {}".format(WIFI_SSID))
    try:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        time.sleep(0.5)
        if wlan.isconnected():
            slog("SYSTEM", "INFO",
                 "قبلاً متصل | IP={}".format(wlan.ifconfig()[0]))
            return wlan
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        for i in range(20):
            if wlan.isconnected(): break
            time.sleep(1)
        if wlan.isconnected():
            slog("SYSTEM", "INFO",
                 "متصل ✅ IP={}".format(wlan.ifconfig()[0]))
        else:
            slog("SYSTEM", "ERROR", "WiFi ناموفق!")
    except Exception as e:
        slog("SYSTEM", "ERROR", "WiFi: {}".format(e))
    return wlan

def get_ip():
    try:
        if wlan and wlan.isconnected():
            return wlan.ifconfig()[0]
    except: pass
    return "0.0.0.0"

# ================================================================
#  سنسور: MQ9
#  سالم است — فقط بالای آستانه لاگ می‌دهد
# ================================================================
MQ9_WARMUP_SEC = 30

def read_mq9():
    if mq9_adc is None:
        slog("MQ9", "ERROR", "ADC آماده نیست")
        return 0
    try:
        raw = mq9_adc.read()
        thr = config.get("gas_threshold",
                          DEFAULT_CONFIG["gas_threshold"])
        if raw >= thr:
            slog("MQ9", "WARNING",
                 "⚠️ گاز! raw={} >= thr={}".format(raw, thr))
        return raw
    except Exception as e:
        slog("MQ9", "ERROR", "خواندن: {}".format(e))
        return 0

# ================================================================
#  سنسور: Flame — Hysteresis State Machine
#  مشکل‌دار — لاگ DEBUG فعال
# ================================================================
FLAME_ON  = 4    # 4 × 2s = 8  ثانیه LOW  متوالی → روشن
FLAME_OFF = 10   # 10× 2s = 20 ثانیه HIGH متوالی → خاموش

_fl_active = False
_fl_lo     = 0
_fl_hi     = 0

def read_flame():
    global _fl_active, _fl_lo, _fl_hi
    if flame_pin is None:
        slog("FLAME", "ERROR", "پین آماده نیست")
        return False
    try:
        raw = flame_pin.value()

        if not _fl_active:
            # منتظر ON
            if raw == 0:
                _fl_lo += 1
                slog("FLAME", "DEBUG",
                     "LOW {}/{} → waiting ON".format(
                         _fl_lo, FLAME_ON))
                if _fl_lo >= FLAME_ON:
                    _fl_active = True
                    _fl_lo     = 0
                    slog("FLAME", "WARNING",
                         "🔥 شعله تأیید شد! "
                         "({} سیکل متوالی)".format(FLAME_ON))
            else:
                if _fl_lo:
                    slog("FLAME", "DEBUG",
                         "HIGH → counter ریست (بود {})".format(
                             _fl_lo))
                _fl_lo = 0
        else:
            # منتظر OFF
            if raw == 1:
                _fl_hi += 1
                slog("FLAME", "DEBUG",
                     "HIGH {}/{} → waiting OFF".format(
                         _fl_hi, FLAME_OFF))
                if _fl_hi >= FLAME_OFF:
                    _fl_active = False
                    _fl_hi     = 0
                    slog("FLAME", "WARNING",
                         "✅ شعله خاموش تأیید شد "
                         "({} سیکل)".format(FLAME_OFF))
            else:
                if _fl_hi:
                    slog("FLAME", "DEBUG",
                         "LOW → hi counter ریست (بود {})".format(
                             _fl_hi))
                _fl_hi = 0

        return _fl_active

    except Exception as e:
        slog("FLAME", "ERROR", "خواندن: {}".format(e))
        return False

# ================================================================
#  سنسور: PIR — H mode + PULL_DOWN
#  مشکل‌دار — لاگ DEBUG فعال
# ================================================================
PIR_CONFIRM = 1   # H mode → یک HIGH کافی است
PIR_RESET   = 2   # دو LOW متوالی → ریست

_pir_hi = 0
_pir_lo = 0

def read_motion():
    global _pir_hi, _pir_lo
    if pir_pin is None:
        slog("PIR", "ERROR", "پین آماده نیست")
        return False
    try:
        raw = pir_pin.value()

        if raw == 1:
            _pir_hi += 1
            _pir_lo  = 0
            slog("PIR", "DEBUG",
                 "HIGH cnt={}".format(_pir_hi))
            if _pir_hi == PIR_CONFIRM:
                slog("PIR", "WARNING",
                     "🚶 حرکت تأیید شد!")
        else:
            _pir_lo += 1
            slog("PIR", "DEBUG",
                 "LOW cnt={}".format(_pir_lo))
            if _pir_lo >= PIR_RESET:
                if _pir_hi:
                    slog("PIR", "DEBUG",
                         "حرکت پایان یافت | ریست")
                _pir_hi = 0
                _pir_lo = 0

        return _pir_hi >= PIR_CONFIRM

    except Exception as e:
        slog("PIR", "ERROR", "خواندن: {}".format(e))
        return False

# ================================================================
#  سنسور: Door — سالم، فقط WARNING
# ================================================================
def read_door():
    if door_pin is None:
        slog("DOOR", "ERROR", "پین آماده نیست")
        return False
    try:
        raw     = door_pin.value()
        is_open = (raw == 1)
        if is_open:
            slog("DOOR", "WARNING", "🚪 درب باز!")
        return is_open
    except Exception as e:
        slog("DOOR", "ERROR", "خواندن: {}".format(e))
        return False

# ================================================================
#  سنسور: DS18B20 — سالم، فقط WARNING
# ================================================================
def read_temperature():
    if not DS18B20_AVAILABLE or not ds_roms:
        return None
    try:
        ds_sensor.convert_temp()
        time.sleep_ms(750)
        temp = ds_sensor.read_temp(ds_roms[0])
        if temp is None:
            slog("DS18B20", "ERROR", "read_temp → None")
            return None
        result = round(float(temp), 2)
        thr    = config.get("temp_threshold",
                             DEFAULT_CONFIG["temp_threshold"])
        if result >= thr:
            slog("DS18B20", "WARNING",
                 "⚠️ دما بالا! {}°C >= {}°C".format(
                     result, thr))
        return result
    except Exception as e:
        slog("DS18B20", "ERROR", "خواندن: {}".format(e))
        return None

# ================================================================
#  Alarm
# ================================================================
def evaluate_alarm(data):
    gas_thr  = config.get("gas_threshold",
                           DEFAULT_CONFIG["gas_threshold"])
    temp_thr = config.get("temp_threshold",
                           DEFAULT_CONFIG["temp_threshold"])

    reasons = {
        "gas_alarm"   : data["mq9"] >= gas_thr,
        "temp_alarm"  : (data["temperature"] is not None and
                         data["temperature"] >= temp_thr),
        "flame_alarm" : (config.get("flame_enabled",  True) and
                         data["flame"]),
        "motion_alarm": (config.get("motion_enabled", True) and
                         data["motion"]),
        "door_alarm"  : (config.get("door_enabled",   True) and
                         data["door_open"]),
    }
    alarm  = any(reasons.values())
    if alarm:
        active = [k for k, v in reasons.items() if v]
        slog("SYSTEM", "WARNING",
             "🚨 ALARM: {}".format(active))
    return alarm, reasons

# ================================================================
#  خواندن همه سنسورها
# ================================================================
def read_sensors():
    global last_data, alarm_history, last_alarm_state

    mq9_val    = read_mq9()
    temp_val   = read_temperature()
    flame_val  = read_flame()
    motion_val = read_motion()
    door_val   = read_door()

    data = {
        "mq9"            : mq9_val,
        "temperature"    : temp_val if temp_val is not None else 0,
        "gas_leak"       : mq9_val >= config.get(
                               "gas_threshold",
                               DEFAULT_CONFIG["gas_threshold"]),
        "flame"          : flame_val,
        "motion"         : motion_val,
        "door_open"      : door_val,
        "device_name"    : config.get("device_name",
                            DEFAULT_CONFIG["device_name"]),
        "ip"             : get_ip(),
        "timestamp"      : time.time(),
        "guardian_active": True,
        "alarm_muted"    : False,
        "warmup_done"    : _mq9_warmup_done,
    }

    alarm, reasons = evaluate_alarm(data)
    data["alarm"]         = alarm
    data["alarm_reasons"] = reasons

    if alarm and not last_alarm_state:
        alarm_history.append({
            "timestamp": data["timestamp"],
            "reasons"  : reasons
        })
        if len(alarm_history) > 20:
            alarm_history = alarm_history[-20:]

    last_alarm_state = alarm
    last_data        = data
    return data

# ================================================================
#  Thread پس‌زمینه
# ================================================================
def _sensor_loop():
    global _mq9_warmup_done
    slog("SYSTEM", "INFO",
         "Thread شروع | interval={}s".format(
             config.get("read_interval",
                         DEFAULT_CONFIG["read_interval"])))

    slog("MQ9", "WARNING",
         "⏳ Warm-up {} ثانیه...".format(MQ9_WARMUP_SEC))
    for i in range(MQ9_WARMUP_SEC):
        time.sleep(1)
    _mq9_warmup_done = True
    slog("MQ9", "INFO", "✅ Warm-up کامل")

    while True:
        try:
            read_sensors()
        except Exception as e:
            slog("SYSTEM", "ERROR",
                 "loop خطا: {}".format(e))
        time.sleep(config.get("read_interval",
                               DEFAULT_CONFIG["read_interval"]))

# ================================================================
#  HTTP
# ================================================================
def http_ok_json(conn, body):
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    _send(conn, 200, "application/json", body)

def http_ok_html(conn, body):
    _send(conn, 200, "text/html; charset=utf-8", body)

def _send(conn, code, ctype, body):
    reasons = {200:"OK", 400:"Bad Request",
               404:"Not Found", 500:"Internal Server Error"}
    r  = "HTTP/1.1 {} {}\r\n".format(code, reasons.get(code,"OK"))
    r += "Content-Type: {}\r\n".format(ctype)
    r += "Access-Control-Allow-Origin: *\r\n"
    r += "Connection: close\r\n"
    r += "Content-Length: {}\r\n\r\n".format(len(body))
    r += body
    try:    conn.send(r.encode("utf-8"))
    except: conn.send(r)

def parse_request(conn):
    try:
        raw = conn.recv(2048)
        if not raw: return None, None, None
        try:    txt = raw.decode("utf-8")
        except: txt = raw.decode("latin-1")
        lines = txt.split("\r\n")
        if not lines or len(lines[0].split()) < 2:
            return None, None, None
        parts  = lines[0].split()
        method = parts[0]
        path   = parts[1]
        body   = txt.split("\r\n\r\n", 1)[1] \
                 if "\r\n\r\n" in txt else ""
        return method, path, body
    except: return None, None, None

# ================================================================
#  صفحه HTML لاگ — در مرورگر باز می‌شود
# ================================================================
def _log_html(sensor_name, entries):
    """
    یک صفحه HTML ساده که در مرورگر نمایش داده می‌شود
    رنگ‌بندی: DEBUG=خاکستری / INFO=آبی / WARNING=نارنجی / ERROR=قرمز
    """
    rows = ""
    for e in reversed(entries):
        lvl = e["lvl"]
        color = {
            "DEBUG"  : "#888",
            "INFO"   : "#2196F3",
            "WARNING": "#FF9800",
            "ERROR"  : "#f44336",
        }.get(lvl, "#fff")

        rows += (
            "<tr>"
            "<td style='color:#aaa'>{}</td>"
            "<td style='color:{};font-weight:bold'>{}</td>"
            "<td>{}</td>"
            "</tr>"
        ).format(e["ts"], color, lvl, e["msg"])

    html = """<!DOCTYPE html>
<html><head>
<meta charset='utf-8'>
<title>Log: {name}</title>
<meta http-equiv='refresh' content='3'>
<style>
  body{{background:#1e1e1e;color:#ddd;
       font-family:monospace;padding:20px}}
  h2{{color:#4fc3f7}}
  table{{width:100%;border-collapse:collapse}}
  td{{padding:6px 10px;border-bottom:1px solid #333;
      vertical-align:top}}
  td:first-child{{width:120px;white-space:nowrap}}
  td:nth-child(2){{width:80px}}
  .refresh{{color:#666;font-size:12px}}
</style>
</head><body>
<h2>🛡 Guardian — لاگ سنسور: {name}</h2>
<p class='refresh'>هر 3 ثانیه بارگذاری مجدد</p>
<table>{rows}</table>
<hr>
<a href='/logs' style='color:#4fc3f7'>← همه سنسورها</a>
</body></html>""".format(name=sensor_name, rows=rows)
    return html

def _all_logs_html():
    """
    صفحه اصلی لاگ — لینک به هر سنسور
    آخرین WARNING/ERROR هر سنسور نمایش داده می‌شود
    """
    cards = ""
    for sensor, entries in SENSOR_LOGS.items():
        last_warn = next(
            (e for e in reversed(entries)
             if e["lvl"] in ("WARNING", "ERROR")), None)

        badge_color = "#4caf50"   # سبز = سالم
        badge_text  = "OK"
        last_msg    = "—"

        if last_warn:
            if last_warn["lvl"] == "ERROR":
                badge_color = "#f44336"
                badge_text  = "ERROR"
            else:
                badge_color = "#FF9800"
                badge_text  = "WARN"
            last_msg = last_warn["msg"][:60]

        cards += """
<div style='background:#2d2d2d;border-radius:8px;
            padding:15px;margin:10px 0;
            border-left:4px solid {bc}'>
  <div style='display:flex;justify-content:space-between'>
    <a href='/logs/{s}' style='color:#4fc3f7;
       font-size:18px;font-weight:bold'>{s}</a>
    <span style='background:{bc};color:#fff;
          padding:3px 10px;border-radius:4px;
          font-size:12px'>{bt}</span>
  </div>
  <div style='color:#888;margin-top:8px;
              font-size:13px'>{lm}</div>
  <div style='color:#555;margin-top:5px;
              font-size:11px'>{cnt} رویداد</div>
</div>""".format(
            bc=badge_color, bt=badge_text,
            s=sensor, lm=last_msg,
            cnt=len(entries))

    ip = get_ip()
    html = """<!DOCTYPE html>
<html><head>
<meta charset='utf-8'>
<title>Guardian Logs</title>
<meta http-equiv='refresh' content='5'>
<style>
  body{{background:#1e1e1e;color:#ddd;
       font-family:sans-serif;padding:20px;
       max-width:700px;margin:auto}}
  h2{{color:#4fc3f7}}
  a{{text-decoration:none}}
  .nav{{margin-top:20px;color:#666}}
  .nav a{{color:#4fc3f7;margin-right:15px}}
</style>
</head><body>
<h2>🛡 Guardian AI — داشبورد لاگ</h2>
<div style='color:#666;font-size:13px'>
  IP: {ip} | هر 5 ثانیه بارگذاری مجدد
</div>
<div style='margin-top:20px'>{cards}</div>
<div class='nav'>
  <a href='/data'>📊 داده زنده</a>
  <a href='/diag'>🔧 دیاگنوز</a>
</div>
</body></html>""".format(ip=ip, cards=cards)
    return html

def _diag_html():
    """
    صفحه دیاگنوز — وضعیت لحظه‌ای همه سنسورها
    """
    def row(label, value, ok=True):
        color = "#4caf50" if ok else "#f44336"
        return (
            "<tr><td style='color:#888'>{}</td>"
            "<td style='color:{}'>{}</td></tr>"
        ).format(label, color, value)

    mq9_raw  = read_mq9() if mq9_adc else "N/A"
    temp     = read_temperature()
    door_raw = door_pin.value() if door_pin else "N/A"
    pir_raw  = pir_pin.value()  if pir_pin  else "N/A"
    fl_raw   = flame_pin.value() if flame_pin else "N/A"

    thr = config.get("gas_threshold",
                      DEFAULT_CONFIG["gas_threshold"])
    rows = (
        row("MQ9 Raw",
            "{} / {}".format(mq9_raw, thr),
            mq9_raw < thr if isinstance(mq9_raw, int) else False)
        + row("MQ9 Warmup",
              "✅ آماده" if _mq9_warmup_done else "⏳ در حال گرم شدن",
              _mq9_warmup_done)
        + row("DS18B20",
              "{}°C".format(temp) if temp else "یافت نشد",
              temp is not None)
        + row("Flame Raw GPIO{}".format(FLAME_PIN),
              "{} | active={}".format(fl_raw, _fl_active),
              not _fl_active)
        + row("Flame Counters",
              "LOW={}/{} HIGH={}/{}".format(
                  _fl_lo, FLAME_ON, _fl_hi, FLAME_OFF),
              True)
        + row("PIR Raw GPIO{}".format(PIR_PIN),
              "{} | hi={} lo={}".format(
                  pir_raw, _pir_hi, _pir_lo),
              True)
        + row("Door Raw GPIO{}".format(DOOR_PIN),
              "{} | {}".format(
                  door_raw,
                  "باز" if door_raw == 1 else "بسته"),
              door_raw == 0)
        + row("WiFi",
              "✅ {}".format(get_ip())
              if (wlan and wlan.isconnected())
              else "❌ قطع",
              wlan is not None and wlan.isconnected())
    )

    html = """<!DOCTYPE html>
<html><head>
<meta charset='utf-8'>
<title>Guardian Diag</title>
<meta http-equiv='refresh' content='2'>
<style>
  body{{background:#1e1e1e;color:#ddd;
       font-family:monospace;padding:20px}}
  h2{{color:#4fc3f7}}
  table{{border-collapse:collapse;width:100%}}
  td{{padding:8px 12px;border-bottom:1px solid #333}}
  td:first-child{{color:#888;width:220px}}
  .nav a{{color:#4fc3f7;margin-right:15px;
          text-decoration:none}}
</style>
</head><body>
<h2>🔧 دیاگنوز لحظه‌ای</h2>
<div style='color:#666;font-size:12px;margin-bottom:15px'>
  هر 2 ثانیه بارگذاری مجدد
</div>
<table>{rows}</table>
<div style='margin-top:20px' class='nav'>
  <a href='/logs'>📋 لاگ‌ها</a>
  <a href='/data'>📊 داده JSON</a>
</div>
</body></html>""".format(rows=rows)
    return html

# ================================================================
#  HTTP Handlers
# ================================================================
def handle_data(conn):
    try:
        data = last_data if last_data else read_sensors()
        http_ok_json(conn, data)
    except Exception as e:
        _send(conn, 500, "application/json",
              json.dumps({"error": str(e)}))

def handle_config_get(conn):
    http_ok_json(conn, config)

def handle_config_post(conn, body):
    global config
    try:
        nc = json.loads(body)
        if not isinstance(nc, dict):
            raise ValueError("not a dict")
        for k in DEFAULT_CONFIG:
            if k in nc:
                config[k] = nc[k]
        save_config()
        http_ok_json(conn, {"success": True, "config": config})
    except Exception as e:
        _send(conn, 400, "application/json",
              json.dumps({"success": False, "error": str(e)}))

# ================================================================
#  HTTP Server
# ================================================================
def start_server():
    import socket
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s    = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(5)
    ip = get_ip()
    slog("SYSTEM", "INFO",
         "سرور آماده | http://{}".format(ip))
    slog("SYSTEM", "INFO",
         "لاگ‌ها: http://{}/logs".format(ip))
    slog("SYSTEM", "INFO",
         "دیاگنوز: http://{}/diag".format(ip))

    while True:
        conn, addr = s.accept()
        try:
            method, path, body = parse_request(conn)
            if not method:
                _send(conn, 400, "application/json",
                      '{"error":"bad request"}')

            elif method == "OPTIONS":
                _send(conn, 200, "text/plain", "")

            elif method == "GET" and path == "/":
                http_ok_html(conn, _all_logs_html())

            elif method == "GET" and path == "/logs":
                http_ok_html(conn, _all_logs_html())

            elif method == "GET" and path.startswith("/logs/"):
                sensor = path[6:].upper()
                if sensor in SENSOR_LOGS:
                    http_ok_html(conn,
                        _log_html(sensor,
                                   SENSOR_LOGS[sensor]))
                else:
                    _send(conn, 404, "text/plain",
                          "sensor not found")

            elif method == "GET" and path == "/diag":
                http_ok_html(conn, _diag_html())

            elif method == "GET" and path == "/data":
                handle_data(conn)

            elif method == "GET"  and path == "/config":
                handle_config_get(conn)

            elif method == "POST" and path == "/config":
                handle_config_post(conn, body)

            else:
                _send(conn, 404, "application/json",
                      json.dumps({"error": "not found"}))

        except Exception as e:
            slog("SYSTEM", "ERROR",
                 "handler: {}".format(e))
            try:
                _send(conn, 500, "application/json",
                      json.dumps({"error": str(e)}))
            except: pass
        finally:
            try: conn.close()
            except: pass

# ================================================================
#  Main
# ================================================================
slog("SYSTEM", "INFO",
     "Guardian AI v5.0 | {}".format(sys.version))

load_config()
wlan = connect_wifi()
time.sleep(1)

try:
    import _thread
    _thread.start_new_thread(_sensor_loop, ())
    slog("SYSTEM", "INFO", "Thread سنسور ✅")
except Exception as e:
    slog("SYSTEM", "ERROR",
         "Thread: {} → مستقیم".format(e))
    try: read_sensors()
    except: pass

start_server()
