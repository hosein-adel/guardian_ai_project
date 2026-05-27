import pytest
from core.guardian import AlertService, GuardianCore, DummyTTS, _FallbackDummyTTS
from core.state import SharedState


class FakeConfig:
    OPENAI_API_KEY = "test-key"
    OPENAI_BASE_URL = "https://api.gapgpt.app/v1"
    OPENAI_CHAT_MODEL = "gpt-5-mini"
    SYSTEM_PROMPT_PATH = "prompts/system.txt"
    GAS_THRESHOLD = 300
    TEMP_THRESHOLD = 45
    MONITOR_INTERVAL = 1


@pytest.fixture
def fake_config():
    return FakeConfig()


@pytest.fixture
def fake_shared_state():
    return SharedState()


def test_alert_service_trigger(fake_shared_state):
    svc = AlertService(fake_shared_state)
    svc.trigger_alarm(["Gas Leak Detected"])
    assert fake_shared_state.alarm_status == "triggered"
    assert "Gas Leak Detected" in fake_shared_state.alarm_reasons


def test_alert_service_clear(fake_shared_state):
    svc = AlertService(fake_shared_state)
    svc.trigger_alarm(["Fire"])
    svc.clear_alarm()
    assert fake_shared_state.alarm_status == "clear"
    assert fake_shared_state.alarm_reasons == []


def test_alert_service_mute(fake_shared_state):
    svc = AlertService(fake_shared_state)
    svc.mute_alarm()
    assert fake_shared_state.alarm_muted is True


def test_guardian_evaluate_alarm(fake_config, fake_shared_state):
    svc = AlertService(fake_shared_state)
    core = GuardianCore(
        config=fake_config,
        shared_state=fake_shared_state,
        alert_service=svc,
    )
    # Gas above threshold
    triggered, reasons = core.evaluate_alarm({"gas": 500, "temperature": 20, "flame": False})
    assert triggered is True
    assert any("Gas" in r for r in reasons)

    # Normal
    triggered, reasons = core.evaluate_alarm({"gas": 100, "temperature": 20, "flame": False})
    assert triggered is False
    assert reasons == []


def test_guardian_data_missing_fields(fake_config, fake_shared_state):
    svc = AlertService(fake_shared_state)
    core = GuardianCore(
        config=fake_config,
        shared_state=fake_shared_state,
        alert_service=svc,
    )
    triggered, reasons = core.evaluate_alarm({})
    assert triggered is False


def test_dummy_tts():
    d = DummyTTS()
    assert d.enabled is False
    d.speak("hello")  # should not raise


def test_fallback_dummy_tts():
    d = _FallbackDummyTTS()
    d.speak("hello")  # should not raise


def test_guardian_chat_can_disable_tts(fake_config, fake_shared_state):
    svc = AlertService(fake_shared_state)

    class FakeAIChat:
        def chat(self, text, sensor_context=None):
            return "پاسخ تستی"

    class FakeTTS:
        def __init__(self):
            self.calls = []

        def speak(self, text):
            self.calls.append(text)

    tts = FakeTTS()
    core = GuardianCore(
        config=fake_config,
        shared_state=fake_shared_state,
        alert_service=svc,
        tts_engine=tts,
        ai_chat=FakeAIChat(),
    )

    reply = core.chat("سلام", speak=False)
    assert reply == "پاسخ تستی"
    assert tts.calls == []

    reply = core.chat("سلام", speak=True)
    assert reply == "پاسخ تستی"
    assert tts.calls == ["پاسخ تستی"]
