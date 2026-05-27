import os
from dotenv import load_dotenv

# Load .env variables into environment
load_dotenv()


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config():
    """
    Load all uppercase configuration variables from this module.
    """
    return {
        key: value
        for key, value in globals().items()
        if key.isupper() and not key.startswith("_")
    }


# =========================
# Flask configuration
# =========================
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = _get_bool_env("FLASK_DEBUG", True)

MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(16 * 1000 * 1000)))
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
ALLOWED_EXTENSIONS = {"wav", "mp3", "ogg", "png", "jpg", "jpeg", "gif"}


# =========================
# OpenAI / GapGPT Bridge configuration
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.gapgpt.app/v1")
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-5-mini")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "shimmer")
OPENAI_STT_MODEL = os.getenv("OPENAI_STT_MODEL", "whisper-1")


# =========================
# Guardian AI configuration (Legacy / Optional)
# =========================
WAKE_WORD_PATH = None
WAKE_WORD = "porcupine"

PORCUPINE_ACCESS_KEY = os.getenv("PORCUPINE_ACCESS_KEY", "")
PORCUPINE_KEYWORD_PATH = os.getenv("PORCUPINE_KEYWORD_PATH", "")

WAKE_WORD_KEYWORD_PATHS = [PORCUPINE_KEYWORD_PATH] if PORCUPINE_KEYWORD_PATH else []
WAKE_WORD_SENSITIVITIES = [float(os.getenv("WAKE_WORD_SENSITIVITY", "0.5"))]


# =========================
# ESP32 configuration
# =========================
ESP32_IP = os.getenv("ESP32_IP", "192.168.43.219")
ESP32_BASE_URL = os.getenv("ESP32_BASE_URL", f"http://{ESP32_IP}")
ESP32_BASE = ESP32_BASE_URL
ESP32_TIMEOUT = int(os.getenv("ESP32_TIMEOUT", "3"))


# =========================
# Alarm thresholds
# =========================
ALARM_THRESHOLD_MQ9 = int(os.getenv("ALARM_THRESHOLD_MQ9", "500"))
ALARM_THRESHOLD_TEMP = float(os.getenv("ALARM_THRESHOLD_TEMP", "100"))
FLAME_ACTIVE_VALUE = os.getenv("FLAME_ACTIVE_VALUE", "1")


# =========================
# Prompts / System config
# =========================
SYSTEM_PROMPT_PATH = os.getenv("SYSTEM_PROMPT_PATH", "prompts/system.txt")
