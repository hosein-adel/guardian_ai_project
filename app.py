import os
import io
import tempfile
import wave
from flask import Flask, render_template, jsonify, request
import threading
import traceback
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
    from voice.ai_chat import AIChatEngine
except Exception as exc:
    get_logger("app").warning(f"AIChat import failed: {exc}")
    AIChatEngine = None

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

        self._set_running(True)
        self._stop_requested = False
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

        missing = self._dependencies_ready()
        if missing:
            return {
                "ok": False,
                "status": "dependencies_missing",
                "missing": missing,
                "message": "Guardian dependencies are not fully initialized.",
            }

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

        for attr_name in ("running", "_running", "active", "_active", "stop_requested"):
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
        "error": str(message),
    }

    if extra:
        payload.update(extra)

    return jsonify(payload), status_code


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


def normalize_sensor_data(raw):
    if not isinstance(raw, dict):
        raw = {}

    temperature = raw.get("temperature", raw.get("temp", raw.get("t", 0)))
    humidity = raw.get("humidity", raw.get("hum", raw.get("h", 0)))
    pressure = raw.get("pressure", raw.get("bmp_pressure", 0))

    mq9 = raw.get("mq9", raw.get("gas_value", raw.get("gas", 0)))

    gas_leak = raw.get("gas_leak", raw.get("gasLeak", raw.get("gas_detected", False)))
    if isinstance(gas_leak, (int, float)) and gas_leak not in [0, 1]:
        gas_leak = False

    flame = bool(raw.get("flame", raw.get("fire", raw.get("flame_alarm", False))))
    motion = bool(raw.get("motion", raw.get("pir", raw.get("motion_alarm", False))))
    door_open = bool(raw.get("door_open", raw.get("door", raw.get("door_alarm", False))))

    alarm_reasons_raw = raw.get("alarm_reasons", {})
    active_alarm_reasons = []

    if isinstance(alarm_reasons_raw, dict):
        active_alarm_reasons = [
            key for key, value in alarm_reasons_raw.items()
            if bool(value)
        ]
    elif isinstance(alarm_reasons_raw, list):
        active_alarm_reasons = [str(x) for x in alarm_reasons_raw]

    alarm_status = bool(raw.get("alarm", raw.get("alarm_status", False)))

    if not alarm_status:
        alarm_status = any([
            flame,
            motion,
            door_open,
            bool(gas_leak),
            len(active_alarm_reasons) > 0
        ])

    return {
        "temperature": temperature,
        "temp": temperature,
        "humidity": humidity,
        "hum": humidity,
        "pressure": pressure,
        "mq9": mq9,
        "gas_value": mq9,
        "gas": bool(gas_leak),
        "gas_leak": bool(gas_leak),
        "gasLeak": bool(gas_leak),
        "gas_detected": bool(gas_leak),
        "flame": flame,
        "motion": motion,
        "pir": motion,
        "door_open": door_open,
        "door": door_open,
        "alarm": alarm_status,
        "alarm_status": alarm_status,
        "alarm_muted": bool(raw.get("alarm_muted", False)),
        "alarm_reasons": active_alarm_reasons,
        "alarm_reasons_raw": alarm_reasons_raw,
        "guardian_active": bool(raw.get("guardian_active", False)),
        "device_name": raw.get("device_name", "Guardian ESP32"),
        "timestamp": raw.get("timestamp"),
        "ip": raw.get("ip"),
        "source": raw.get("source", "esp32"),
        "raw": raw
    }


# ============================================================
# Routes
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


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
        raw = esp32_client.get_data()

        if not isinstance(raw, dict):
            raw = {}

        data = normalize_sensor_data(raw)

        return jsonify({
            "ok": True,
            "online": True,
            "esp32_connected": True,
            **data
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
            "humidity": 0,
            "hum": 0,
            "pressure": 0,
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

        response = {
            "ok": True,
            "online": True if raw else False,
            "esp32_connected": True if raw else False,
            **sensors,
            "alarm_muted": getattr(shared_state, "alarm_muted", False),
            "alarm_status": False,
            "alarm_reasons": [],
            "wakeword_active": False,
            "stt_active": False,
            "current_activity": "idle",
            "state": {
                "alarm_muted": getattr(shared_state, "alarm_muted", False),
                "alarm_status": False,
                "alarm_reasons": [],
                "wakeword_active": False,
                "stt_active": False,
                "current_activity": "idle"
            },
            "sensors": sensors
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
            "humidity": 0,
            "hum": 0,
            "pressure": 0,
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
    """
    try:
        if stt_engine is None:
            return jsonify({"ok": False, "error": "STT engine not initialized"}), 503

        if core is None:
            return jsonify({"ok": False, "error": "Guardian core not initialized"}), 503

        audio_file = request.files.get("audio")
        if not audio_file:
            return jsonify({"ok": False, "error": "No audio file provided"}), 400

        # Save incoming blob to temp WAV
        audio_bytes = audio_file.read()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        logger.info(f"[VOICE] Received audio: {len(audio_bytes)} bytes")

        # Transcribe via Whisper
        try:
            with open(tmp_path, "rb") as f:
                transcript = stt_engine.client.audio.transcriptions.create(
                    model=stt_engine.model,
                    file=f,
                    language="fa",
                )
            heard = transcript.text.strip()
        except Exception as e:
            logger.error(f"[VOICE] Whisper transcription failed: {e}")
            return jsonify({"ok": False, "error": f"Transcription failed: {str(e)}"}), 500
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        if not heard:
            return jsonify({
                "ok": True,
                "heard": "",
                "reply": "",
                "message": "No speech detected"
            })

        logger.info(f"[VOICE] Heard: {heard}")

        # Chat with GPT
        if hasattr(core, "chat"):
            reply = core.chat(heard)
        elif hasattr(core, "handle_text"):
            reply = core.handle_text(heard)
        else:
            return jsonify({
                "ok": False,
                "error": "No chat method found on guardian_core"
            }), 501

        logger.info(f"[VOICE] Reply: {reply}")

        # Speak reply via TTS (async in thread to not block response)
        def _speak():
            try:
                if tts_engine:
                    tts_engine.speak(reply)
            except Exception as ex:
                logger.error(f"[VOICE] TTS failed: {ex}")

        threading.Thread(target=_speak, daemon=True).start()

        return jsonify({
            "ok": True,
            "heard": heard,
            "reply": reply,
        })

    except Exception as e:
        logger.exception("Voice transcribe failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/guardian/chat", methods=["POST"])
def api_guardian_chat():
    try:
        if core is None:
            return jsonify({"ok": False, "error": "Guardian core not initialized"}), 503

        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()

        if not text:
            return jsonify({"ok": False, "error": "text is required"}), 400

        if hasattr(core, "chat"):
            reply = core.chat(text)
        elif hasattr(core, "handle_text"):
            reply = core.handle_text(text)
        else:
            return jsonify({
                "ok": False,
                "error": "No chat method found on guardian_core"
            }), 501

        return jsonify({
            "ok": True,
            "user_text": text,
            "reply": reply
        })
    except Exception as e:
        logger.exception("Guardian chat failed")
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route("/api/tts/speak", methods=["POST"])
def api_tts_speak():
    try:
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()

        if not text:
            return jsonify({"ok": False, "error": "text is required"}), 400

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
        return jsonify({"ok": False, "error": str(e)}), 500


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


@app.errorhandler(404)
def not_found(error):
    return jsonify({"ok": False, "error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error")
    return jsonify({"ok": False, "error": "Internal server error"}), 500


@app.route("/api/guardian/start", methods=["POST"])
def start_guardian():
    try:
        result = guardian.start()
        return jsonify(result)
    except Exception as e:
        logger.exception("Guardian start failed")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/guardian/stop", methods=["POST"])
def stop_guardian():
    try:
        result = guardian.stop()
        return jsonify(result)
    except Exception as e:
        logger.exception("Guardian stop failed")
        return jsonify({"success": False, "error": str(e)}), 500


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
