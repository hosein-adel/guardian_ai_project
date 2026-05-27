import os
import tempfile
import threading

try:
    from playsound import playsound
except Exception as exc:
    playsound = None
    import logging
    logging.getLogger("guardian.voice.tts").warning(f"playsound not available: {exc}")

from openai import OpenAI


class TextToSpeechEngine:
    """
    Text-to-Speech using OpenAI TTS API via GapGPT bridge.
    Model: gpt-4o-mini-tts
    Supports voices: alloy, echo, fable, onyx, nova, shimmer.
    Falls back to print-only if API key is missing.
    """

    def __init__(self, config=None):
        self.config = config
        self.enabled = True
        self.lock = threading.Lock()
        api_key = getattr(config, "OPENAI_API_KEY", "")
        base_url = getattr(config, "OPENAI_BASE_URL", "https://api.gapgpt.app/v1")
        self.model = getattr(config, "OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        self.voice = getattr(config, "OPENAI_TTS_VOICE", "shimmer")
        self.client = OpenAI(api_key=api_key, base_url=base_url) if api_key else None
        print("OpenAI TextToSpeechEngine initialized." if self.client else "[TTS] OpenAI key missing - TTS disabled.")

    def speak(self, text):
        if not self.enabled:
            print(f"[TTS] Disabled. Text was: {text}")
            return
        if not text:
            return
        if self.client is None:
            print(f"[TTS] {text}")
            return

        text = str(text).strip()
        if not text:
            return

        try:
            with self.lock:
                print(f"[TTS] Speaking: {text}")
                response = self.client.audio.speech.create(
                    model=self.model,
                    voice=self.voice,
                    input=text,
                )

                # Write to temporary mp3 and play
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    temp_path = f.name
                    response.stream_to_file(temp_path)

                if playsound:
                    playsound(temp_path)
                else:
                    print(f"[TTS] playsound not available, saved to {temp_path}")

                try:
                    os.remove(temp_path)
                except Exception:
                    pass

        except Exception as e:
            print(f"[TTS] OpenAI TTS speak failed: {e}")

    def stop(self):
        print("[TTS] Stop requested (no-op for streamed audio).")

    def enable(self):
        self.enabled = True
        print("[TTS] Enabled.")

    def disable(self):
        self.enabled = False
        print("[TTS] Disabled.")

    def is_enabled(self):
        return self.enabled


class DummyTTS:
    """
    Fallback if every TTS engine fails to initialize.
    """

    def __init__(self, *args, **kwargs):
        self.enabled = False

    def speak(self, text):
        print(f"[DummyTTS] {text}")

    def stop(self):
        pass

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False

    def is_enabled(self):
        return self.enabled
