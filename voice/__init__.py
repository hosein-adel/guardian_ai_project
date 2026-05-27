# Voice pipeline package
# Lazy/robust imports so missing audio libraries don't break the whole app.

from .ai_chat import AIChatEngine

try:
    from .stt import SpeechToTextEngine
except Exception as exc:
    SpeechToTextEngine = None
    import logging
    logging.getLogger("guardian.voice").warning(f"STT import failed: {exc}")

try:
    from .tts import TextToSpeechEngine, DummyTTS
except Exception as exc:
    TextToSpeechEngine = None
    DummyTTS = None
    import logging
    logging.getLogger("guardian.voice").warning(f"TTS import failed: {exc}")
