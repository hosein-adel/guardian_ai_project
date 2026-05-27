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
