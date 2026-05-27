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
        "humidity": 0,
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
