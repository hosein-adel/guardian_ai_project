import pytest
import sys
from pathlib import Path

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as app_config


@pytest.fixture(scope="session")
def app():
    """Create Flask app test client."""
    from app import app as flask_app
    flask_app.testing = True
    flask_app.debug = False
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def mock_openai(monkeypatch):
    """Mock OpenAI SDK to avoid real API calls during tests."""
    class FakeChoice:
        def __init__(self, text):
            self.message = type("Msg", (), {"content": text})()

    class FakeCompletion:
        def __init__(self, text):
            self.choices = [FakeChoice(text)]

    class FakeAudio:
        def transcriptions_create(self, **kwargs):
            class FakeTranscript:
                text = "سلام گاردین"
            return FakeTranscript()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        class chat:
            @staticmethod
            def completions_create(*args, **kwargs):
                return FakeCompletion("پاسخ تستی")

        class audio:
            transcriptions = FakeAudio()

            @staticmethod
            def speech_create(**kwargs):
                class FakeSpeech:
                    def stream_to_file(self, path):
                        Path(path).write_bytes(b"FAKE_MP3")
                return FakeSpeech()

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    return FakeClient
