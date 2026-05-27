import pytest
from core.state import SharedState


def test_shared_state_initial_values():
    s = SharedState()
    assert s.get_guardian_active() is False
    assert s.get_guardian_running() is False
    assert s.get_alarm_muted() is False
    assert s.get_sensor_data() == {}


def test_shared_state_set_guardian_active():
    s = SharedState()
    s.set_guardian_active(True)
    assert s.get_guardian_active() is True


def test_shared_state_snapshot():
    s = SharedState()
    s.set_last_command("test command")
    snap = s.snapshot()
    assert snap["last_command"] == "test command"
    assert "guardian_active" in snap


def test_shared_state_thread_safety():
    import threading
    s = SharedState()
    errors = []

    def worker():
        try:
            for i in range(100):
                s.set_alarm_muted(i % 2 == 0)
                s.set_guardian_active(i % 3 == 0)
                s.set_sensor_data({"temp": i})
                s.get_sensor_data()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
