import pytest


def test_index_route(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Guardian" in response.data


def test_health_route(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert "services" in data


def test_data_route_offline_fallback(client):
    # ESP32 likely not connected in test environment
    response = client.get("/api/data")
    assert response.status_code in (200, 500)
    data = response.get_json()
    assert "ok" in data


def test_alarm_mute(client):
    response = client.post("/api/alarm/mute")
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["muted"] is True


def test_alarm_unmute(client):
    response = client.post("/api/alarm/unmute")
    assert response.status_code == 200
    data = response.get_json()
    assert data["muted"] is False


def test_guardian_status(client):
    response = client.get("/api/guardian/status")
    assert response.status_code == 200
    data = response.get_json()
    assert "guardian_running" in data


def test_tts_speak_no_text(client):
    response = client.post("/api/tts/speak", json={})
    assert response.status_code == 400


def test_tts_speak_with_text(client):
    response = client.post("/api/tts/speak", json={"text": "سلام"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True


def test_chat_no_text(client):
    response = client.post("/api/guardian/chat", json={})
    assert response.status_code == 400


def test_chat_with_text(client):
    response = client.post("/api/guardian/chat", json={"text": "وضعیت چطوره"})
    assert response.status_code in (200, 501, 503)


def test_guardian_command_no_body(client):
    response = client.post("/api/guardian/handle_command", json={})
    assert response.status_code == 400


def test_guardian_command_with_text(client):
    response = client.post("/api/guardian/handle_command", json={"command": "وضعیت"})
    assert response.status_code == 200


def test_voice_transcribe_no_audio(client):
    response = client.post("/api/voice/transcribe")
    assert response.status_code == 400


def test_guardian_start_stop_routes_use_wrapper_methods(client, monkeypatch):
    import app as app_module

    calls = {"start": 0, "stop": 0}

    class FakeGuardian:
        def start_guardian(self):
            calls["start"] += 1
            return {"ok": True, "status": "started"}

        def stop_guardian(self):
            calls["stop"] += 1
            return {"ok": True, "status": "stop_requested"}

        def is_running(self):
            return False

    monkeypatch.setattr(app_module, "guardian", FakeGuardian())

    start = client.post("/api/guardian/start")
    assert start.status_code == 200
    assert start.get_json()["status"] == "started"

    stop = client.post("/api/guardian/stop")
    assert stop.status_code == 200
    assert stop.get_json()["status"] == "stop_requested"

    assert calls == {"start": 1, "stop": 1}


def test_compat_chat_endpoint(client, monkeypatch):
    import app as app_module

    class FakeCore:
        def chat(self, text):
            return f"reply: {text}"

    monkeypatch.setattr(app_module, "core", FakeCore())

    response = client.post("/api/chat", json={"command": "سلام"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["response"] == "reply: سلام"
    assert data["reply"] == "reply: سلام"


def test_compat_chat_requires_text(client):
    response = client.post("/api/chat", json={})
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_config_endpoint_get_and_post(client, monkeypatch):
    import app as app_module

    # Avoid network call to ESP32 while testing config save.
    monkeypatch.setattr(app_module, "safe_esp32_send_config", lambda payload: ({"success": True, "config": payload}, 200))

    get_response = client.get("/api/config")
    assert get_response.status_code == 200
    assert get_response.get_json()["ok"] is True
    assert "config" in get_response.get_json()

    post_response = client.post("/api/config", json={"GAS_THRESHOLD": 450, "TEMPERATURE_THRESHOLD": 55})
    assert post_response.status_code == 200
    data = post_response.get_json()
    assert data["ok"] is True
    assert data["esp32_payload"] == {"gas_threshold": 450, "temp_threshold": 55}


def test_wakeword_compat_routes(client):
    enable = client.post("/api/wakeword/enable")
    assert enable.status_code == 200
    assert enable.get_json()["wakeword_active"] is True

    disable = client.post("/api/wakeword/disable")
    assert disable.status_code == 200
    assert disable.get_json()["wakeword_active"] is False


def test_favicon_no_404(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 204


def test_dashboard_uses_api_online_and_alarm_muted_fields():
    from pathlib import Path

    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert "setConnection(true);" not in html
    assert "const espOnline = data.online !== undefined ? boolish(data.online) : boolish(data.esp32_connected);" in html
    assert "state.localAlarmMuted = boolish(data.alarm_muted);" in html


def test_normalize_sensor_data_preserves_hardware_fields():
    from app import normalize_sensor_data

    raw = {
        "mq9": 612,
        "temperature": 27.5,
        "gas_leak": True,
        "flame": False,
        "motion": True,
        "door_open": False,
        "alarm": True,
        "alarm_reasons": {"gas_alarm": True, "motion_alarm": True, "door_alarm": False},
        "warmup_done": True,
        "ip": "192.168.1.10",
    }

    data = normalize_sensor_data(raw)
    assert data["mq9"] == 612
    assert data["gas_value"] == 612
    assert "gas_ppm" not in data
    assert data["gas_leak"] is True
    assert data["gas"] is True
    assert data["motion"] is True
    assert data["door_open"] is False
    assert data["alarm_status"] is True
    assert data["alarm_reasons"] == ["gas_alarm", "motion_alarm"]
    assert data["hardware_sensors"]["mq9"] == 612
    assert "humidity" not in data
    assert "pressure" not in data
    assert "humidity" not in data["hardware_sensors"]
    assert "pressure" not in data["hardware_sensors"]


def test_api_state_does_not_override_sensor_alarm(client, monkeypatch):
    import app as app_module

    class FakeESP32:
        def get_data(self):
            return {
                "mq9": 900,
                "temperature": 30,
                "gas_leak": True,
                "alarm": True,
                "alarm_reasons": {"gas_alarm": True},
                "esp32_online": True,
            }

    monkeypatch.setattr(app_module, "esp32_client", FakeESP32())
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.get_json()
    assert data["online"] is True
    assert data["mq9"] == 900
    assert data["alarm_status"] is True
    assert data["alarm_reasons"] == ["gas_alarm"]
    assert data["sensors"]["mq9"] == 900


def test_alarm_mute_reflected_in_api_data(client, monkeypatch):
    import app as app_module

    class FakeESP32:
        def get_data(self):
            return {"mq9": 100, "esp32_online": True, "alarm_muted": False}

    monkeypatch.setattr(app_module, "esp32_client", FakeESP32())
    client.post("/api/alarm/mute")
    response = client.get("/api/data")
    assert response.status_code == 200
    assert response.get_json()["alarm_muted"] is True


def test_voice_transcribe_empty_audio(client):
    import io

    response = client.post(
        "/api/voice/transcribe",
        data={"audio": (io.BytesIO(b""), "empty.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data["ok"] is False
    assert "empty" in data["error"].lower()


def test_voice_transcribe_without_stt_api_key_returns_503(client, monkeypatch):
    import io
    import app as app_module

    class FakeSTTNoClient:
        model = "whisper-1"
        client = None

    class FakeCore:
        pass

    monkeypatch.setattr(app_module, "stt_engine", FakeSTTNoClient())
    monkeypatch.setattr(app_module, "core", FakeCore())

    response = client.post(
        "/api/voice/transcribe",
        data={"audio": (io.BytesIO(b"fake-webm-audio"), "recording.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 503
    data = response.get_json()
    assert data["ok"] is False
    assert "api key" in data["error"].lower()


def test_voice_transcribe_keeps_browser_audio_extension(client, monkeypatch):
    import io
    import app as app_module

    captured = {}

    class FakeTranscript:
        text = "سلام گاردین"

    class FakeTranscriptions:
        def create(self, model, file, language):
            captured["model"] = model
            captured["file_name"] = file.name
            captured["language"] = language
            return FakeTranscript()

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeClient:
        audio = FakeAudio()

    class FakeSTT:
        model = "whisper-1"
        client = FakeClient()

    class FakeCore:
        def chat(self, text):
            captured["heard_by_core"] = text
            return "پاسخ تستی"

    monkeypatch.setattr(app_module, "stt_engine", FakeSTT())
    monkeypatch.setattr(app_module, "core", FakeCore())
    monkeypatch.setattr(app_module, "tts_engine", None)

    response = client.post(
        "/api/voice/transcribe",
        data={"audio": (io.BytesIO(b"fake-webm-audio"), "recording.webm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["heard"] == "سلام گاردین"
    assert data["reply"] == "پاسخ تستی"
    assert captured["file_name"].endswith(".webm")
    assert captured["language"] == "fa"
    assert captured["heard_by_core"] == "سلام گاردین"



def test_voice_transcribe_no_audio_includes_request_id(client):
    response = client.post("/api/voice/transcribe")
    assert response.status_code == 400
    data = response.get_json()
    assert data["ok"] is False
    assert data["request_id"].startswith("voice_")


def test_voice_transcribe_success_includes_request_id(client, monkeypatch):
    import io
    import app as app_module

    class FakeTranscript:
        text = "سلام"

    class FakeTranscriptions:
        def create(self, model, file, language):
            return FakeTranscript()

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeClient:
        audio = FakeAudio()

    class FakeSTT:
        model = "whisper-1"
        client = FakeClient()

    class FakeCore:
        def chat(self, text):
            return "جواب"

    monkeypatch.setattr(app_module, "stt_engine", FakeSTT())
    monkeypatch.setattr(app_module, "core", FakeCore())
    monkeypatch.setattr(app_module, "tts_engine", None)

    response = client.post(
        "/api/voice/transcribe",
        data={"audio": (io.BytesIO(b"fake-webm-audio"), "recording.webm")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["request_id"].startswith("voice_")
    assert data["heard"] == "سلام"
    assert data["reply"] == "جواب"


def test_voice_transcribe_asks_core_not_to_speak_and_runs_route_tts_once(client, monkeypatch):
    import io
    import time
    import app as app_module

    events = {"core_spoke": 0, "route_tts": 0, "speak_arg": None}

    class FakeTranscript:
        text = "سلام"

    class FakeTranscriptions:
        def create(self, model, file, language):
            return FakeTranscript()

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    class FakeClient:
        audio = FakeAudio()

    class FakeSTT:
        model = "whisper-1"
        client = FakeClient()

    class FakeCore:
        def chat(self, text, speak=True):
            events["speak_arg"] = speak
            if speak:
                events["core_spoke"] += 1
            return "جواب"

    class FakeTTS:
        def speak(self, text):
            events["route_tts"] += 1

    monkeypatch.setattr(app_module, "stt_engine", FakeSTT())
    monkeypatch.setattr(app_module, "core", FakeCore())
    monkeypatch.setattr(app_module, "tts_engine", FakeTTS())

    response = client.post(
        "/api/voice/transcribe",
        data={"audio": (io.BytesIO(b"fake-webm-audio"), "recording.webm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert events["speak_arg"] is False
    assert events["core_spoke"] == 0

    deadline = time.time() + 1
    while events["route_tts"] < 1 and time.time() < deadline:
        time.sleep(0.01)
    assert events["route_tts"] == 1


def test_text_chat_routes_do_not_auto_speak(client, monkeypatch):
    import app as app_module

    calls = []

    class FakeCore:
        def chat(self, text, speak=True):
            calls.append({"text": text, "speak": speak})
            return "جواب متنی"

    monkeypatch.setattr(app_module, "core", FakeCore())

    compat = client.post("/api/chat", json={"command": "سلام"})
    assert compat.status_code == 200
    assert compat.get_json()["reply"] == "جواب متنی"

    guardian = client.post("/api/guardian/chat", json={"text": "وضعیت"})
    assert guardian.status_code == 200
    assert guardian.get_json()["reply"] == "جواب متنی"

    assert calls == [
        {"text": "سلام", "speak": False},
        {"text": "وضعیت", "speak": False},
    ]


def test_voice_button_targets_chat_compose_not_first_compose():
    from pathlib import Path

    html = Path("templates/index.html").read_text(encoding="utf-8")
    js = Path("static/voice.js").read_text(encoding="utf-8")

    assert html.count('id="chat-compose"') == 1
    assert 'id="chat-compose" class="chat-compose"' in html
    assert 'document.getElementById("chat-compose")' in js
    assert 'document.querySelector(".chat-compose")' not in js


def test_every_backend_response_has_request_id_header(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID", "").startswith("req_")


def test_backend_accepts_incoming_request_id_header(client):
    response = client.get("/api/health", headers={"X-Request-ID": "frontend_trace_123"})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == "frontend_trace_123"


def test_404_error_body_includes_request_id(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    data = response.get_json()
    assert data["ok"] is False
    assert data["request_id"] == response.headers.get("X-Request-ID")


def test_frontend_sends_request_id_and_displays_errors_with_id():
    from pathlib import Path

    html = Path("templates/index.html").read_text(encoding="utf-8")
    voice_js = Path("static/voice.js").read_text(encoding="utf-8")

    assert "function newRequestId" in html
    assert "'X-Request-ID': requestId" in html
    assert "errorWithRequestId" in html
    assert "headers: { \"X-Request-ID\": requestId }" in voice_js
    assert "withRequestId" in voice_js


def test_voice_request_body_id_matches_response_header(client):
    response = client.post("/api/voice/transcribe")
    data = response.get_json()
    assert response.status_code == 400
    assert data["request_id"] == response.headers.get("X-Request-ID")
    assert data["request_id"].startswith("voice_")


def test_voice_request_uses_incoming_request_id(client):
    response = client.post("/api/voice/transcribe", headers={"X-Request-ID": "voice_ui_test123"})
    data = response.get_json()
    assert response.status_code == 400
    assert response.headers.get("X-Request-ID") == "voice_ui_test123"
    assert data["request_id"] == "voice_ui_test123"


def assert_standard_error_schema(response):
    data = response.get_json()
    assert data["ok"] is False
    assert data["success"] is False
    assert data["error"]
    assert data["request_id"] == response.headers.get("X-Request-ID")


def test_tts_empty_text_uses_standard_error_schema(client):
    response = client.post("/api/tts/speak", json={})
    assert response.status_code == 400
    assert_standard_error_schema(response)


def test_compat_chat_empty_text_uses_standard_error_schema(client):
    response = client.post("/api/chat", json={})
    assert response.status_code == 400
    assert_standard_error_schema(response)


def test_guardian_chat_empty_text_uses_standard_error_schema(client):
    response = client.post("/api/guardian/chat", json={})
    assert response.status_code == 400
    assert_standard_error_schema(response)


def test_api_data_exposes_esp32_connection_metadata(client, monkeypatch):
    import app as app_module

    class FakeESP32:
        def get_data(self):
            return {
                "esp32_online": False,
                "esp32_base_url": "http://192.168.10.55",
                "esp32_last_error": "connection_error",
                "esp32_last_error_detail": "No route to host",
                "error": "connection_error",
                "source": "fallback",
            }

    monkeypatch.setattr(app_module, "esp32_client", FakeESP32())
    response = client.get("/api/data")

    assert response.status_code == 200
    data = response.get_json()
    assert data["online"] is False
    assert data["esp32_connected"] is False
    assert data["esp32_base_url"] == "http://192.168.10.55"
    assert data["esp32_error"] == "connection_error"
    assert data["esp32_error_detail"] == "No route to host"


def test_removed_unused_dashboard_controls_do_not_leave_js_references():
    from pathlib import Path

    html = Path("templates/index.html").read_text(encoding="utf-8")
    removed_ids_and_functions = [
        "listenAndChat",
        "chatBox",
        "langSelect",
        "tts-text-input",
        "tts-speak-btn",
        "speakText",
        "wakeword-enable-btn",
        "wakeword-disable-btn",
        "wakeword-sensitivity-input",
    ]
    for item in removed_ids_and_functions:
        assert item not in html


def test_chat_frontend_accepts_response_reply_result_or_message():
    from pathlib import Path

    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert "data.response || data.reply || data.result || data.message || t('no_response')" in html


def test_voice_js_records_wav_not_webm_and_limits_duration():
    from pathlib import Path

    js = Path("static/voice.js").read_text(encoding="utf-8")
    assert "MAX_RECORDING_MS = 25000" in js
    assert "encodeWav" in js
    assert "audio/wav" in js
    assert "recording.wav" in js
    assert "MediaRecorder" not in js
    assert "recording.webm" not in js


def test_chat_ai_error_returns_error_not_fake_success(client, monkeypatch):
    import app as app_module
    from voice.ai_chat import AIChatError

    class FakeCore:
        def chat(self, text, speak=True, raise_errors=False):
            raise AIChatError("LLM provider connection/error: Connection error.")

    monkeypatch.setattr(app_module, "core", FakeCore())

    response = client.post("/api/chat", json={"command": "سلام"})
    assert response.status_code == 502
    data = response.get_json()
    assert data["ok"] is False
    assert data["success"] is False
    assert data["error_type"] == "ai_chat_error"
    assert "Connection error" in data["error"]
    assert data["request_id"] == response.headers.get("X-Request-ID")


def test_guardian_chat_missing_ai_key_returns_503(client, monkeypatch):
    import app as app_module
    from voice.ai_chat import AIChatError

    class FakeCore:
        def chat(self, text, speak=True, raise_errors=False):
            raise AIChatError("OpenAI API key not configured.")

    monkeypatch.setattr(app_module, "core", FakeCore())

    response = client.post("/api/guardian/chat", json={"text": "سلام"})
    assert response.status_code == 503
    data = response.get_json()
    assert data["ok"] is False
    assert data["error_type"] == "ai_chat_error"
    assert "api key" in data["error"].lower()


def test_dashboard_no_wakeword_status_and_thresholds_match_esp32_defaults():
    from pathlib import Path

    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert "wakeword-status-pill" not in html
    assert "wakeword_label" not in html
    assert "GAS_THRESHOLD: 2000" in html
    assert 'id="gas-threshold-input" type="number" step="0.1" value="2000"' in html
    assert "GET /api/config در بک‌اند فعلی وجود ندارد" not in html


def test_config_threshold_defaults_match_esp32():
    import importlib
    import config

    reloaded = importlib.reload(config)
    assert reloaded.ALARM_THRESHOLD_MQ9 == 2000
    assert reloaded.GAS_THRESHOLD == 2000
    assert reloaded.ALARM_THRESHOLD_TEMP == 50
    assert reloaded.TEMP_THRESHOLD == 50
