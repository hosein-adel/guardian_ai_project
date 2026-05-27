import io
import wave
import tempfile
import queue

try:
    import sounddevice as sd
except Exception as exc:
    sd = None
    import logging
    logging.getLogger("guardian.voice.stt").warning(f"sounddevice not available: {exc}")

from openai import OpenAI


class SpeechToTextEngine:
    """
    Speech-to-Text using OpenAI Whisper API via GapGPT bridge.
    Falls back to a dummy transcription if API key is missing.
    """

    def __init__(self, config=None):
        self.config = config
        self.sample_rate = 16000
        self.channels = 1
        api_key = getattr(config, "OPENAI_API_KEY", "")
        base_url = getattr(config, "OPENAI_BASE_URL", "https://api.gapgpt.app/v1")
        self.model = getattr(config, "OPENAI_STT_MODEL", "whisper-1")
        self.client = OpenAI(api_key=api_key, base_url=base_url) if api_key else None

    def _record_audio(self, duration: int = 5):
        """
        Record audio from microphone and return as in-memory WAV bytes.
        """
        if sd is None:
            raise RuntimeError("sounddevice is not available on this system.")

        q = queue.Queue()

        def callback(indata, frames, time_info, status):
            if status:
                print("[STT STATUS]", status)
            q.put(indata.copy())

        buffer = []
        with sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=8000,
            dtype="int16",
            channels=self.channels,
            callback=callback
        ):
            import time
            start = time.time()
            while time.time() - start < duration:
                buffer.append(q.get())

        audio_data = b"".join([b.tobytes() for b in buffer])

        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_data)
        wav_io.seek(0)
        return wav_io

    def listen_once(self, duration=5, lang=None):
        """
        Record audio for `duration` seconds and transcribe via Whisper.
        Returns transcribed text (or empty string on failure).
        """
        if self.client is None:
            print("[STT] OpenAI API key not configured. Returning empty.")
            return ""

        try:
            wav_buffer = self._record_audio(duration)

            # OpenAI Whisper expects a file-like object with a filename
            wav_buffer.name = "audio.wav"

            transcript = self.client.audio.transcriptions.create(
                model=self.model,
                file=wav_buffer,
                language="fa" if (lang or "").lower().startswith("fa") else "en",
            )
            text = transcript.text.strip()
            print(f"[STT] Heard: {text}")
            return text

        except Exception as e:
            print(f"[STT] Whisper transcription failed: {e}")
            return ""
