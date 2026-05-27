import os
import io
import tempfile
import wave
from flask import Flask, render_template, jsonify, request, g, has_request_context
import threading
import traceback
import uuid
import config

from utils.logger import setup_logging, get_logger

try:
    from voice.tts import TextToSpeechEngine, DummyTTS
except Exception as exc:
    get_logger("app").warning(f"TTS import failed: {exc}")
    TextToSpeechEngine = None
    DummyTTS = None

try:
    from voice.stt import SpeechToTextEngine
except Exception as exc:
    get_logger("app").warning(f"STT import failed: {exc}")
    SpeechToTextEngine = None

try:
    from voice.ai_chat import AIChatEngine, AIChatError
except Exception as exc:
    get_logger("app").warning(f"AIChat import failed: {exc}")
    AIChatEngine = None
    AIChatError = RuntimeError

from core.guardian import GuardianCore, AlertService
from services.esp32 import ESP32Client
from core.state import SharedState

# ============================================================
# Logging
# ============================================================
setup_logging()
logger = get_logger("app")

# ============================================================
# Flask App
# ============================================================

app = Flask(__name__)


@app.before_request
def attach_request_id():
    """Attach a trace id to every backend request so no request is untraceable."""
    incoming = (request.headers.get("X-Request-ID") or "").strip()
    prefix = "voice" if request.path == "/api/voice/transcribe" else "req"
    g.request_id = incoming[:80] if incoming else new_request_id(prefix)


@app.after_request
def add_request_id_header(response):
    request_id = getattr(g, "request_id", "")
    if request_id:
        response.headers["X-Request-ID"] = request_id
    return response


# ============================================================
# Shared State
# ============================================================

shared_state = SharedState()


# ============================================================
# Safe Dependency Initialization
# ============================================================

def init_service(name, factory, required=False):
    try:
        instance = factory()
        logger.info(f"[INIT] {name} initialized successfully.")
        return instance
    except Exception as exc:
        logger.error(f"[INIT] {name} initialization failed: {exc}")
        logger.debug(traceback.format_exc())
        shared_state.set_last_error(f"{name} init failed: {exc}")

        if required:
            raise

        return None


alert_service = init_service(
    "AlertService",
    lambda: AlertService(shared_state),
    required=True
)

esp32_client = init_service(
    "ESP32Client",
    lambda: ESP32Client(config.ESP32_BASE_URL),
    required=False
)

stt_engine = init_service(
    "SpeechToText",
    lambda: SpeechToTextEngine(config),
    required=False
)

tts_engine = init_service(
    "TextToSpeech",
    lambda: TextToSpeechEngine(config),
    required=False
)

ai_chat = init_service(
    "AIChat",
    lambda: AIChatEngine(config),
    required=False
)

if tts_engine is None:
    tts_engine = DummyTTS() if DummyTTS else None
    logger.warning("[INIT] Using DummyTTS fallback.")

core = init_service(
    "Core",
    lambda: GuardianCore(
        config=config,
        shared_state=shared_state,
        alert_service=alert_service,
        esp32_client=esp32_client,
        stt_engine=stt_engine,
        tts_engine=tts_engine,
        ai_chat=ai_chat,
    ),
    required=False
)

# ============================================================
# Guardian Wrapper
# ============================================================

class GuardianAIWrapper(GuardianCore):
    """
    Stable wrapper for GuardianCore.
    Runs the core in a separate worker thread to keep Flask routes responsive.
    """

    def __init__(
        self,
        config_obj,
        shared_state_obj,
        alert_service_obj,
        esp32_client_obj,
        stt_engine_obj,
        ai_chat_obj=None,
    ):
        super().__init__(
            config_obj,
            shared_state_obj,
            alert_service_obj,
            esp32_client_obj,
            stt_engine=stt_engine_obj,
            tts_engine=tts_engine,
            ai_chat=ai_chat_obj,
        )

        self._worker_thread = None
        self._running_lock = threading.Lock()
        self._running = False
        self._stop_requested = False

        logger.info("[GuardianAIWrapper] Initialized.")

    def is_running(self):
        with self._running_lock:
            return self._running

    def _set_running(self, value: bool):
        with self._running_lock:
            self._running = bool(value)
            self.shared_state.set_guardian_running(bool(value))

    def _dependencies_ready(self):
        missing = []

        if self.alert_service is None:
            missing.append("alert_service")

        if self.esp32_client is None:
            missing.append("esp32_client")

        if self.stt_engine is None:
            missing.append("stt_engine")

        return missing

    def _run_guardian_safe(self):
        logger.info("[GuardianAIWrapper] Guardian loop started.")

        if self._stop_requested:
            logger.info("[GuardianAIWrapper] Guardian start was cancelled before worker began.")
            self._set_running(False)
            return

        self._set_running(True)
        self.shared_state.set_last_error("")

        try:
            self.run()
        except Exception as exc:
            logger.error(f"[GuardianAIWrapper] Guardian loop crashed: {exc}")
            logger.debug(traceback.format_exc())
            self.shared_state.set_last_error(str(exc))
        finally:
            self._set_running(False)
            logger.info("[GuardianAIWrapper] Guardian loop stopped.")

    def start_guardian(self):
        if self.is_running():
            return {
                "ok": True,
                "status": "already_running",
                "message": "Guardian is already running.",
            }

        if self._worker_thread is not None and self._worker_thread.is_alive():
            return {
                "ok": False,
                "status": "stopping",
                "message": "Guardian is still stopping. Please try again in a few seconds.",
            }

        missing = self._dependencies_ready()
        if missing:
            return {
                "ok": False,
                "status": "dependencies_missing",
                "missing": missing,
                "message": "Guardian dependencies are not fully initialized.",
            }

        self._guardian_running = True
        self._stop_requested = False
        self._set_running(True)

        self._worker_thread = threading.Thread(
            target=self._run_guardian_safe,
            daemon=True,
            name="GuardianAIWorker",
        )
        self._worker_thread.start()

        return {
            "ok": True,
            "status": "started",
            "message": "Guardian started.",
        }

    def stop_guardian(self):
        if not self.is_running():
            return {
                "ok": True,
                "status": "not_running",
                "message": "Guardian is not running.",
            }

        self._stop_requested = True
        self._guardian_running = False
        self.shared_state.set_guardian_active(False)
        self._set_running(False)

        for attr_name in ("running", "active", "_active", "stop_requested"):
            if hasattr(self, attr_name):
                try:
                    setattr(self, attr_name, False if "running" in attr_name or "active" in attr_name else True)
                except Exception:
                    pass

        return {
            "ok": True,
            "status": "stop_requested",
            "message": "Stop signal sent to Guardian.",
        }


guardian = GuardianAIWrapper(
    config,
    shared_state,
    alert_service,
    esp32_client,
    stt_engine,
    ai_chat_obj=ai_chat,
)


# ============================================================
# Helper Functions
# ============================================================

def make_error(message, status_code=500, extra=None):
    payload = {
        "ok": False,
        "success": False,
        "error": str(message),
    }

    if has_request_context():
        request_id = getattr(g, "request_id", "")
        if request_id:
            payload["request_id"] = request_id

    if extra:
        payload.update(extra)

    return jsonify(payload), status_code


def new_request_id(prefix="req"):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def safe_esp32_get_data():
    if esp32_client is None:
        return {
            "ok": False,
            "error": "ESP32 client is not initialized.",
        }

    try:
        data = esp32_client.get_data()

        if isinstance(data, dict):
            return data

        return {
            "ok": True,
            "data": data,
        }

    except Exception as exc:
        logger.error(f"[ESP32] get_data failed: {exc}")
        logger.debug(traceback.format_exc())
        shared_state.set_last_error(str(exc))

        return {
            "ok": False,
            "error": str(exc),
        }


def safe_esp32_send_config(payload):
    if esp32_client is None:
        return {
            "ok": False,
            "error": "ESP32 client is not initialized.",
        }, 500

    try:
        result = esp32_client.send_config(payload)

        if isinstance(result, tuple) and len(result) == 2:
            body, status_code = result
            return body, status_code

        return result, 200

    except Exception as exc:
        logger.error(f"[ESP32] send_config failed: {exc}")
        logger.debug(traceback.format_exc())
        shared_state.set_last_error(str(exc))

        return {
            "ok": False,
            "error": str(exc),
        }, 500


def _to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "open", "detected"}
    return bool(value)


def _first_present(data, *keys, default=None):
    for key in keys:
        if isinstance(data, dict) and key in data and data[key] is not None:
            return data[key]
    return default


def normalize_sensor_data(raw):
    """
    Normalize the exact ESP32 hardware contract into one stable API shape.

    Real hardware sensors in esp32/main.py:
    1) MQ9 gas ADC         -> mq9, gas_leak
    2) DS18B20 temperature -> temperature
    3) Flame digital       -> flame
    4) PIR motion          -> motion
    5) Door reed/switch    -> door_open

    Do not expose non-existing environmental sensors.
    """
    if not isinstance(raw, dict):
        raw = {}

    alarm_reasons_raw = raw.get("alarm_reasons", {})

    temperature = _first_present(raw, "temperature", "temp", "t", default=0)

    mq9 = _first_present(raw, "mq9", "mq9_raw", "gas_value", "gas_raw", default=0)
    if mq9 == 0 and not isinstance(raw.get("gas"), bool):
        mq9 = raw.get("gas", 0)

    gas_leak = _first_present(raw, "gas_leak", "gasLeak", "gas_detected", default=None)
    if gas_leak is None and isinstance(alarm_reasons_raw, dict):
        gas_leak = alarm_reasons_raw.get("gas_alarm", False)
    gas_leak = _to_bool(gas_leak, False)

    flame = _to_bool(_first_present(raw, "flame", "fire", "flame_alarm", default=None), False)
    motion = _to_bool(_first_present(raw, "motion", "pir", "motion_alarm", default=None), False)
    door_open = _to_bool(_first_present(raw, "door_open", "door", "door_alarm", default=None), False)

    active_alarm_reasons = []
    if isinstance(alarm_reasons_raw, dict):
        active_alarm_reasons = [
            key for key, value in alarm_reasons_raw.items()
            if _to_bool(value, False)
        ]
    elif isinstance(alarm_reasons_raw, list):
        active_alarm_reasons = [str(x) for x in alarm_reasons_raw]

    alarm_status = _to_bool(_first_present(raw, "alarm", "alarm_status", default=False), False)
    if not alarm_status:
        alarm_status = any([gas_leak, flame, motion, door_open, len(active_alarm_reasons) > 0])

    alarm_muted = _to_bool(raw.get("alarm_muted", False), False)
    guardian_active = _to_bool(raw.get("guardian_active", False), False)
    esp32_online = _to_bool(raw.get("esp32_online", bool(raw)), False)
    warmup_done = _to_bool(raw.get("warmup_done", True), True)

    hardware_sensors = {
        "temperature": temperature,
        "mq9": mq9,
        "gas_leak": gas_leak,
        "flame": flame,
        "motion": motion,
        "door_open": door_open,
        "warmup_done": warmup_done,
    }

    return {
        # DS18B20 temperature
        "temperature": temperature,
        "temp": temperature,

        # MQ9/gas: keep numeric and boolean values separate
        "mq9": mq9,
        "mq9_raw": mq9,
        "gas_value": mq9,
        "gas_raw": mq9,
        "gas": gas_leak,
        "gas_leak": gas_leak,
        "gasLeak": gas_leak,
        "gas_detected": gas_leak,

        # digital sensors and aliases used by UI/legacy code
        "flame": flame,
        "fire": flame,
        "flame_alarm": flame,
        "motion": motion,
        "pir": motion,
        "motion_alarm": motion,
        "door_open": door_open,
        "door": door_open,
        "door_alarm": door_open,

        # alarm/state fields
        "alarm": alarm_status,
        "alarm_status": alarm_status,
        "alarm_muted": alarm_muted,
        "alarm_reasons": active_alarm_reasons,
        "alarm_reasons_raw": alarm_reasons_raw,
        "guardian_active": guardian_active,
        "warmup_done": warmup_done,
        "esp32_online": esp32_online,
        "esp32_error": raw.get("esp32_last_error", raw.get("error", "")),
        "esp32_error_detail": raw.get("esp32_last_error_detail", ""),
        "esp32_base_url": raw.get("esp32_base_url"),

        # metadata
        "device_name": raw.get("device_name", "Guardian ESP32"),
        "timestamp": raw.get("timestamp"),
        "ip": raw.get("ip"),
        "source": raw.get("source", "esp32"),

        # canonical nested object for future frontend cleanup
        "hardware_sensors": hardware_sensors,
        "sensors": hardware_sensors,
        "raw": raw,
    }


def runtime_status_fields():
    """Fields expected by the dashboard, derived from backend/shared runtime state."""
    guardian_running = False
    if guardian is not None and hasattr(guardian, "is_running"):
        attr = getattr(guardian, "is_running")
        guardian_running = attr() if callable(attr) else bool(attr)

    guardian_active = False
    if shared_state is not None:
        guardian_active = bool(getattr(shared_state, "guardian_active", False))

    tts_active = False
    if tts_engine is not None:
        try:
            tts_active = tts_engine.is_enabled() if hasattr(tts_engine, "is_enabled") else True
        except Exception:
            tts_active = True

    return {
        "guardian_status": bool(guardian_running or guardian_active),
        "guardian_running": bool(guardian_running),
        "guardian_active": bool(guardian_active),
        "alarm_muted": shared_state.get_alarm_muted() if shared_state else False,
        "stt_active": bool(getattr(shared_state, "stt_active", False)),
        "tts_active": bool(tts_active),
        "wakeword_active": bool(getattr(shared_state, "wakeword_active", False)),
        "last_voice_command": shared_state.get_last_command() if shared_state else "",
        "last_response": shared_state.get_last_response() if shared_state else "",
    }


# ============================================================
# Routes
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/api/health", methods=["GET"])
def api_health():
    try:
        services = {
            "alert_service": alert_service is not None,
            "esp32_client": esp32_client is not None,
            "guardian": guardian is not None,
            "ai_chat": ai_chat is not None,
            "tts_engine": tts_engine is not None,
            "stt_engine": stt_engine is not None,
        }

        guardian_running = False
        if guardian is not None:
            if hasattr(guardian, "is_running"):
                attr = getattr(guardian, "is_running")
                guardian_running = attr() if callable(attr) else bool(attr)

        shared = None
        if shared_state is not None:
            shared = shared_state.snapshot() if hasattr(shared_state, "snapshot") else {}

        return jsonify({
            "ok": True,
            "status": "healthy",
            "services": services,
            "guardian_running": guardian_running,
            "shared_state": shared,
        })
    except Exception as e:
        logger.exception("Health check failed")
        return jsonify({
            "ok": False,
            "status": "unhealthy",
            "error": str(e),
        }), 500


@app.route("/api/data")
def api_data():
    try:
        raw = esp32_client.get_data() if esp32_client is not None else {}

        if not isinstance(raw, dict):
            raw = {}

        data = normalize_sensor_data(raw)
        online = bool(data.get("esp32_online", False))

        if shared_state is not None:
            if online:
                shared_state.set_sensor_data(data)
                with shared_state.current_data_lock:
                    shared_state.current_data = data
            else:
                last_good = shared_state.get_sensor_data()
                if last_good and last_good.get("esp32_online"):
                    # Keep the last real sensor values on transient disconnects; only status/error changes.
                    data = {
                        **last_good,
                        "esp32_online": False,
                        "esp32_error": data.get("esp32_error", data.get("error", "")),
                        "esp32_error_detail": data.get("esp32_error_detail", ""),
                        "esp32_base_url": data.get("esp32_base_url"),
                        "source": "last_good_cache",
                    }

        return jsonify({
            "ok": True,
            "online": online,
            "esp32_connected": online,
            **data,
            **runtime_status_fields(),
        })

    except Exception as e:
        logger.exception("API data fetch failed")

        return jsonify({
            "ok": False,
            "online": False,
            "esp32_connected": False,
            "error": str(e),
            "temperature": 0,
            "temp": 0,
            "motion": False,
            "pir": False,
            "flame": False,
            "door_open": False,
            "door": False,
            "mq9": 0,
            "gas_value": 0,
            "gas": False,
            "gas_leak": False,
            "gasLeak": False,
            "alarm": False,
            "alarm_status": False,
            "alarm_reasons": []
        }), 500


@app.route("/api/state", methods=["GET"])
def api_state():
    try:
        raw = {}

        try:
            raw = esp32_client.get_data()
        except Exception as e:
            logger.warning(f"[API_STATE] ESP32 /data failed: {e}")

        if not raw:
            try:
                import requests
                url = config.ESP32_BASE.rstrip("/") + "/sensors"
                r = requests.get(url, timeout=3)
                if r.status_code == 200:
                    raw = r.json()
            except Exception as e:
                logger.warning(f"[API_STATE] ESP32 /sensors failed: {e}")

        sensors = normalize_sensor_data(raw)
        runtime = runtime_status_fields()
        online = bool(sensors.get("esp32_online", False))

        if shared_state is not None:
            shared_state.set_sensor_data(sensors)
            with shared_state.current_data_lock:
                shared_state.current_data = sensors

        response = {
            "ok": True,
            "online": online,
            "esp32_connected": online,
            **sensors,
            **runtime,
            "current_activity": "idle",
            "state": {
                "alarm_muted": runtime.get("alarm_muted", False),
                "alarm_status": sensors.get("alarm_status", False),
                "alarm_reasons": sensors.get("alarm_reasons", []),
                "wakeword_active": runtime.get("wakeword_active", False),
                "stt_active": runtime.get("stt_active", False),
                "tts_active": runtime.get("tts_active", False),
                "current_activity": "idle"
            },
            "sensors": sensors.get("hardware_sensors", sensors),
            "normalized": sensors,
        }

        return jsonify(response)

    except Exception as e:
        logger.exception("API state failed")

        return jsonify({
            "ok": False,
            "online": False,
            "esp32_connected": False,
            "error": str(e),
            "temperature": 0,
            "temp": 0,
            "gas": 0,
            "mq9": 0,
            "gasLeak": 0,
            "flame": 0,
            "motion": 0,
            "door_open": 0,
            "door": 0,
            "alarm_muted": getattr(shared_state, "alarm_muted", False),
            "alarm_status": False,
            "alarm_reasons": [],
            "wakeword_active": False,
            "stt_active": False,
            "current_activity": "error",
            "state": {},
            "sensors": {},
            "raw": {}
        }), 500


@app.route("/api/guardian/handle_command", methods=["POST"])
def api_guardian_handle_command():
    try:
        data = request.get_json(silent=True) or {}

        command = (
            data.get("command")
            or data.get("text")
            or data.get("message")
            or data.get("action")
            or ""
        )

        command = str(command).strip()

        if not command:
            return jsonify({
                "ok": False,
                "success": False,
                "error": "No command provided"
            }), 400

        logger.info(f"[GUARDIAN_COMMAND] {command}")

        result = None

        if "guardian" not in globals():
            return jsonify({
                "ok": False,
                "success": False,
                "error": "Guardian not initialized"
            }), 500

        if hasattr(guardian, "handle_command") and callable(guardian.handle_command):
            result = guardian.handle_command(command)
        elif hasattr(guardian, "process_command") and callable(guardian.process_command):
            result = guardian.process_command(command)
        elif hasattr(guardian, "ask") and callable(guardian.ask):
            result = guardian.ask(command)
        elif hasattr(guardian, "chat") and callable(guardian.chat):
            result = guardian.chat(command)
        else:
            result = {
                "message": "Guardian command handler not found",
                "command": command
            }

        return jsonify({
            "ok": True,
            "success": True,
            "command": command,
            "result": result
        })

    except Exception as e:
        logger.exception("Guardian command failed")
        return jsonify({
            "ok": False,
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/voice/transcribe", methods=["POST"])
def api_voice_transcribe():
    """
    Receive audio blob from browser microphone.
    Pipeline: Audio → Whisper (STT) → GPT (chat) → OpenAI TTS → return text + play audio.
    Every branch returns a request_id so no voice request is untraceable.
    """
    request_id = getattr(g, "request_id", "") or new_request_id("voice")
    g.request_id = request_id
    tmp_path = None

    try:
        logger.info(f"[VOICE:{request_id}] Request received")

        if stt_engine is None:
            logger.warning(f"[VOICE:{request_id}] STT engine not initialized")
            return jsonify({"ok": False, "request_id": request_id, "error": "STT engine not initialized"}), 503

        if core is None:
            logger.warning(f"[VOICE:{request_id}] Guardian core not initialized")
            return jsonify({"ok": False, "request_id": request_id, "error": "Guardian core not initialized"}), 503

        audio_file = request.files.get("audio")
        if not audio_file:
            logger.warning(f"[VOICE:{request_id}] No audio file provided")
            return jsonify({"ok": False, "request_id": request_id, "error": "No audio file provided"}), 400

        audio_bytes = audio_file.read()
        if not audio_bytes:
            logger.warning(f"[VOICE:{request_id}] Empty audio file")
            return jsonify({"ok": False, "request_id": request_id, "error": "Audio file is empty"}), 400

        if getattr(stt_engine, "client", None) is None:
            logger.warning(f"[VOICE:{request_id}] OpenAI API key is not configured for STT")
            return jsonify({
                "ok": False,
                "request_id": request_id,
                "error": "OpenAI API key is not configured for STT"
            }), 503

        # Keep the real browser audio extension. MediaRecorder usually sends WebM,
        # and saving it as WAV can confuse transcription backends.
        original_name = audio_file.filename or ""
        suffix = os.path.splitext(original_name)[1].lower()
        allowed_suffixes = {".webm", ".wav", ".mp3", ".ogg", ".m4a", ".mp4"}
        if suffix not in allowed_suffixes:
            mime_suffix_map = {
                "audio/webm": ".webm",
                "audio/wav": ".wav",
                "audio/wave": ".wav",
                "audio/x-wav": ".wav",
                "audio/mpeg": ".mp3",
                "audio/mp3": ".mp3",
                "audio/ogg": ".ogg",
                "audio/mp4": ".m4a",
            }
            suffix = mime_suffix_map.get((audio_file.mimetype or "").lower(), ".webm")

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        logger.info(f"[VOICE:{request_id}] Received audio: {len(audio_bytes)} bytes, suffix={suffix}")

        try:
            with open(tmp_path, "rb") as f:
                transcript = stt_engine.client.audio.transcriptions.create(
                    model=stt_engine.model,
                    file=f,
                    language="fa",
                )
            heard = transcript.text.strip()
        except Exception as e:
            logger.error(f"[VOICE:{request_id}] Whisper transcription failed: {e}")
            return jsonify({
                "ok": False,
                "request_id": request_id,
                "error": f"Transcription failed: {str(e)}"
            }), 500
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception as cleanup_error:
                    logger.warning(f"[VOICE:{request_id}] Temp cleanup failed: {cleanup_error}")

        if not heard:
            logger.info(f"[VOICE:{request_id}] No speech detected")
            return jsonify({
                "ok": True,
                "request_id": request_id,
                "heard": "",
                "reply": "",
                "message": "No speech detected"
            })

        shared_state.set_last_command(heard)
        logger.info(f"[VOICE:{request_id}] Heard: {heard}")

        if hasattr(core, "chat"):
            try:
                # In voice mode this route owns TTS playback. Prevent core.chat from speaking too.
                reply = core.chat(heard, speak=False, raise_errors=True)
            except TypeError:
                # Compatibility for core.chat(text, speak=False) implementations without raise_errors.
                try:
                    reply = core.chat(heard, speak=False)
                except TypeError:
                    reply = core.chat(heard)
        elif hasattr(core, "handle_text"):
            reply = core.handle_text(heard)
        else:
            logger.error(f"[VOICE:{request_id}] No chat method found on guardian_core")
            return jsonify({
                "ok": False,
                "request_id": request_id,
                "error": "No chat method found on guardian_core"
            }), 501

        shared_state.set_last_response(reply)
        logger.info(f"[VOICE:{request_id}] Reply: {reply}")

        def _speak():
            try:
                if tts_engine:
                    logger.info(f"[VOICE:{request_id}] TTS thread started")
                    tts_engine.speak(reply)
                    logger.info(f"[VOICE:{request_id}] TTS thread finished")
            except Exception as ex:
                logger.error(f"[VOICE:{request_id}] TTS failed: {ex}")

        threading.Thread(target=_speak, daemon=True, name=f"VoiceTTS-{request_id}").start()

        return jsonify({
            "ok": True,
            "request_id": request_id,
            "heard": heard,
            "reply": reply,
        })

    except Exception as e:
        if isinstance(e, AIChatError):
            logger.warning(f"[VOICE:{request_id}] AI chat failed: {e}")
            status = 503 if "api key" in str(e).lower() else 502
            return jsonify({
                "ok": False,
                "success": False,
                "request_id": request_id,
                "error": str(e),
                "error_type": "ai_chat_error"
            }), status
        logger.exception(f"[VOICE:{request_id}] Voice transcribe failed")
        return jsonify({"ok": False, "success": False, "request_id": request_id, "error": str(e)}), 500


@app.route("/api/guardian/chat", methods=["POST"])
def api_guardian_chat():
    try:
        if core is None:
            return make_error("Guardian core not initialized", 503)

        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()

        if not text:
            return make_error("text is required", 400)

        if hasattr(core, "chat"):
            try:
                reply = core.chat(text, speak=False, raise_errors=True)
            except TypeError:
                try:
                    reply = core.chat(text, speak=False)
                except TypeError:
                    reply = core.chat(text)
        elif hasattr(core, "handle_text"):
            reply = core.handle_text(text)
        else:
            return make_error("No chat method found on guardian_core", 501)

        return jsonify({
            "ok": True,
            "user_text": text,
            "reply": reply
        })
    except Exception as e:
        if isinstance(e, AIChatError):
            logger.warning(f"Guardian chat AI failed: {e}")
            status = 503 if "api key" in str(e).lower() else 502
            return make_error(str(e), status, {"error_type": "ai_chat_error"})
        logger.exception("Guardian chat failed")
        return make_error(str(e), 500)


@app.route("/api/tts/speak", methods=["POST"])
def api_tts_speak():
    try:
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()

        if not text:
            return make_error("text is required", 400)

        def _speak():
            try:
                if tts_engine:
                    tts_engine.speak(text)
            except Exception as ex:
                logger.error(f"[TTS] Speak failed: {ex}")

        threading.Thread(target=_speak, daemon=True).start()

        return jsonify({
            "ok": True,
            "message": "TTS queued",
            "text": text,
        })
    except Exception as e:
        logger.exception("TTS speak failed")
        return make_error(str(e), 500)


@app.route("/api/alarm/mute", methods=["POST"])
def mute_alarm():
    shared_state.set_alarm_muted(True)
    logger.info("[ALARM] Muted")
    return jsonify({"ok": True, "muted": True})


@app.route("/api/alarm/unmute", methods=["POST"])
def unmute_alarm():
    shared_state.set_alarm_muted(False)
    logger.info("[ALARM] Unmuted")
    return jsonify({"ok": True, "muted": False})


@app.route("/api/chat", methods=["POST"])
def api_chat_compat():
    """Backward-compatible dashboard chat endpoint."""
    try:
        data = request.get_json(silent=True) or {}
        text = str(
            data.get("command")
            or data.get("text")
            or data.get("message")
            or ""
        ).strip()

        if not text:
            return make_error("text/command is required", 400)

        shared_state.set_last_command(text)

        if core is not None and hasattr(core, "chat"):
            try:
                reply = core.chat(text, speak=False, raise_errors=True)
            except TypeError:
                try:
                    reply = core.chat(text, speak=False)
                except TypeError:
                    reply = core.chat(text)
        elif core is not None and hasattr(core, "handle_text"):
            reply = core.handle_text(text)
        elif ai_chat is not None:
            reply = ai_chat.chat(text, sensor_context=getattr(shared_state, "current_data", {}))
        else:
            return make_error("Chat engine is not initialized", 503)

        shared_state.set_last_response(reply)

        return jsonify({
            "ok": True,
            "success": True,
            "command": text,
            "response": reply,
            "reply": reply,
        })
    except Exception as e:
        if isinstance(e, AIChatError):
            logger.warning(f"Compat chat AI failed: {e}")
            status = 503 if "api key" in str(e).lower() else 502
            return make_error(str(e), status, {"error_type": "ai_chat_error"})
        logger.exception("Compat chat failed")
        return make_error(str(e), 500)


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Dashboard config endpoint. Keeps UI working and forwards compatible values to ESP32."""
    if request.method == "GET":
        try:
            system_prompt = ""
            try:
                with open(getattr(config, "SYSTEM_PROMPT_PATH", "prompts/system.txt"), "r", encoding="utf-8") as f:
                    system_prompt = f.read()
            except Exception:
                pass

            return jsonify({
                "ok": True,
                "config": {
                    "ESP32_IP": getattr(config, "ESP32_IP", ""),
                    "ESP32_BASE_URL": getattr(config, "ESP32_BASE_URL", ""),
                    "TEMPERATURE_THRESHOLD": getattr(config, "TEMP_THRESHOLD", getattr(config, "ALARM_THRESHOLD_TEMP", 50)),
                    "GAS_THRESHOLD": getattr(config, "GAS_THRESHOLD", getattr(config, "ALARM_THRESHOLD_MQ9", 2000)),
                    "STT_LANGUAGE": "fa",
                    "TTS_VOICE_ID": getattr(config, "OPENAI_TTS_VOICE", ""),
                    "TTS_MODEL_PATH": getattr(config, "OPENAI_TTS_MODEL", ""),
                    "SYSTEM_PROMPT": system_prompt,
                }
            })
        except Exception as e:
            logger.exception("Config get failed")
            return jsonify({"ok": False, "error": str(e)}), 500

    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "JSON body must be an object"}), 400

        # Update local runtime thresholds used by GuardianCore.
        if "GAS_THRESHOLD" in payload:
            value = float(payload["GAS_THRESHOLD"])
            setattr(config, "GAS_THRESHOLD", value)
            if core is not None:
                core.gas_threshold = value
            if guardian is not None:
                guardian.gas_threshold = value

        if "TEMPERATURE_THRESHOLD" in payload:
            value = float(payload["TEMPERATURE_THRESHOLD"])
            setattr(config, "TEMP_THRESHOLD", value)
            if core is not None:
                core.temp_threshold = value
            if guardian is not None:
                guardian.temp_threshold = value

        # Re-point ESP32 client if the dashboard IP changed.
        global esp32_client
        if payload.get("ESP32_IP"):
            esp32_client = ESP32Client(payload["ESP32_IP"])
            setattr(config, "ESP32_IP", payload["ESP32_IP"])
            setattr(config, "ESP32_BASE_URL", esp32_client.base_url)
            setattr(config, "ESP32_BASE", esp32_client.base_url)
            if core is not None:
                core.esp32_client = esp32_client
            if guardian is not None:
                guardian.esp32_client = esp32_client

        esp32_payload = {}
        if "GAS_THRESHOLD" in payload:
            esp32_payload["gas_threshold"] = payload["GAS_THRESHOLD"]
        if "TEMPERATURE_THRESHOLD" in payload:
            esp32_payload["temp_threshold"] = payload["TEMPERATURE_THRESHOLD"]

        esp32_result = None
        esp32_status = None
        if esp32_payload:
            esp32_result, esp32_status = safe_esp32_send_config(esp32_payload)

        return jsonify({
            "ok": True,
            "success": True,
            "status": "Configuration updated",
            "received": payload,
            "esp32_payload": esp32_payload,
            "esp32_status": esp32_status,
            "esp32_result": esp32_result,
        })
    except Exception as e:
        logger.exception("Config save failed")
        return jsonify({"ok": False, "success": False, "error": str(e)}), 500


@app.route("/api/wakeword/enable", methods=["POST"])
def wakeword_enable():
    shared_state.wakeword_active = True
    return jsonify({
        "ok": True,
        "success": True,
        "status": "Wakeword flag enabled (browser push-to-talk is the active voice mode).",
        "wakeword_active": True,
    })


@app.route("/api/wakeword/disable", methods=["POST"])
def wakeword_disable():
    shared_state.wakeword_active = False
    return jsonify({
        "ok": True,
        "success": True,
        "status": "Wakeword flag disabled.",
        "wakeword_active": False,
    })


@app.errorhandler(404)
def not_found(error):
    return make_error("Not found", 404)


@app.errorhandler(500)
def internal_error(error):
    request_id = getattr(g, "request_id", "")
    logger.exception(f"Internal server error request_id={request_id}")
    return make_error("Internal server error", 500)


@app.route("/api/guardian/start", methods=["POST"])
def start_guardian():
    try:
        result = guardian.start_guardian()
        return jsonify(result)
    except Exception as e:
        logger.exception("Guardian start failed")
        return jsonify({"ok": False, "success": False, "error": str(e)}), 500


@app.route("/api/guardian/stop", methods=["POST"])
def stop_guardian():
    try:
        result = guardian.stop_guardian()
        return jsonify(result)
    except Exception as e:
        logger.exception("Guardian stop failed")
        return jsonify({"ok": False, "success": False, "error": str(e)}), 500


@app.route("/api/guardian/status", methods=["GET"])
def guardian_status():
    return jsonify({
        "ok": True,
        "guardian_running": guardian.is_running() if guardian else False,
        "shared_state": shared_state.snapshot(),
    })


@app.route("/api/stt/start", methods=["POST"])
def stt_start():
    try:
        shared_state.stt_active = True
        logger.info("[STT] Started")
        return jsonify({"success": True, "message": "STT started", "stt_active": True})
    except Exception as e:
        logger.exception("STT start failed")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/stt/stop", methods=["POST"])
def stt_stop():
    try:
        shared_state.stt_active = False
        logger.info("[STT] Stopped")
        return jsonify({"success": True, "message": "STT stopped", "stt_active": False})
    except Exception as e:
        logger.exception("STT stop failed")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    host = getattr(config, "FLASK_HOST", "127.0.0.1")
    port = int(getattr(config, "FLASK_PORT", "5000"))

    logger.info(f"[Flask] Starting server on http://{host}:{port}")
    logger.info(f"[Flask] ESP32 base URL: {getattr(config, 'ESP32_BASE', None)}")

    app.run(
        host=host,
        port=port,
        debug=getattr(config, "FLASK_DEBUG", True),
        use_reloader=False,
    )
