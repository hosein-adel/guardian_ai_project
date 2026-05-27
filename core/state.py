import threading
import time


class SharedState:
    """
    Thread-safe shared state for Guardian AI.
    Shared between Flask routes, Guardian core, alarm logic, and UI.
    """

    def __init__(self):
        self._lock = threading.RLock()

        self.guardian_active = False
        self.guardian_running = False
        self.alarm_muted = False
        self.alarm_status = "clear"
        self.alarm_reasons = []

        self.last_command = ""
        self.last_response = ""

        self.last_sensor_data = {}
        self.last_sensor_update = None

        self.current_data = {}
        self.current_data_lock = threading.Lock()

        self.last_error = ""

    def set_guardian_active(self, value: bool):
        with self._lock:
            self.guardian_active = bool(value)

    def get_guardian_active(self) -> bool:
        with self._lock:
            return bool(self.guardian_active)

    def set_guardian_running(self, value: bool):
        with self._lock:
            self.guardian_running = bool(value)

    def get_guardian_running(self) -> bool:
        with self._lock:
            return bool(self.guardian_running)

    def set_alarm_muted(self, value: bool):
        with self._lock:
            self.alarm_muted = bool(value)

    def get_alarm_muted(self) -> bool:
        with self._lock:
            return bool(self.alarm_muted)

    def set_last_command(self, command: str):
        with self._lock:
            self.last_command = command or ""

    def get_last_command(self) -> str:
        with self._lock:
            return self.last_command

    def set_last_response(self, response: str):
        with self._lock:
            self.last_response = response or ""

    def get_last_response(self) -> str:
        with self._lock:
            return self.last_response

    def set_sensor_data(self, data: dict):
        with self._lock:
            self.last_sensor_data = dict(data or {})
            self.last_sensor_update = time.time()

    def get_sensor_data(self) -> dict:
        with self._lock:
            return dict(self.last_sensor_data or {})

    def set_last_error(self, error: str):
        with self._lock:
            self.last_error = error or ""

    def get_last_error(self) -> str:
        with self._lock:
            return self.last_error

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "guardian_active": self.guardian_active,
                "guardian_running": self.guardian_running,
                "alarm_muted": self.alarm_muted,
                "alarm_status": self.alarm_status,
                "alarm_reasons": list(self.alarm_reasons),
                "last_command": self.last_command,
                "last_response": self.last_response,
                "last_sensor_data": dict(self.last_sensor_data or {}),
                "last_sensor_update": self.last_sensor_update,
                "last_error": self.last_error,
            }
