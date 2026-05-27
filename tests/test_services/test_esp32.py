import pytest
import json
from unittest.mock import patch, MagicMock
from services.esp32 import ESP32Client


@patch("services.esp32.requests.get")
def test_esp32_get_sensor_data_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "mq9": 150,
        "temperature": 24.5,
        "gas_leak": 0,
        "motion": 0,
        "door_open": 0,
    }
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    client = ESP32Client("192.168.1.1")
    data = client.get_sensor_data()

    assert data["mq9"] == 150
    assert data["temperature"] == 24.5
    assert "humidity" not in data
    assert data["esp32_online"] is True


@patch("services.esp32.requests.get")
def test_esp32_offline_fallback(mock_get):
    from requests.exceptions import ConnectionError
    mock_get.side_effect = ConnectionError("No connection")

    client = ESP32Client("192.168.1.1")
    data = client.get_sensor_data()

    assert data["esp32_online"] is False
    assert data["mq9"] == 0


def test_esp32_normalize_ip():
    client = ESP32Client("http://192.168.1.1")
    assert client.base_url == "http://192.168.1.1"


@patch("services.esp32.requests.get")
def test_esp32_connection_error_log_is_throttled(mock_get, monkeypatch):
    from requests.exceptions import ConnectionError
    import services.esp32 as esp32_module

    mock_get.side_effect = ConnectionError("No connection")
    printed = []
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(map(str, args))))

    current_time = {"value": 1000.0}
    monkeypatch.setattr(esp32_module.time, "time", lambda: current_time["value"])

    client = ESP32Client("192.168.1.1")
    client.get_sensor_data()
    client.get_sensor_data()

    connection_logs = [line for line in printed if "Connection error" in line]
    assert len(connection_logs) == 1

    current_time["value"] += client.error_log_interval + 1
    client.get_sensor_data()
    connection_logs = [line for line in printed if "Connection error" in line]
    assert len(connection_logs) == 2


def test_esp32_offline_data_contains_connection_metadata():
    client = ESP32Client("http://192.168.10.55")
    data = client._offline_data("connection_error", detail="No route to host")

    assert data["esp32_online"] is False
    assert data["esp32_base_url"] == "http://192.168.10.55"
    assert data["esp32_last_error"] == "connection_error"
    assert data["esp32_last_error_detail"] == "No route to host"
