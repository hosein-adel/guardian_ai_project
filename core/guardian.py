import time
import threading
import json
import os

try:
    from voice.tts import TextToSpeechEngine, DummyTTS
except Exception as exc:
    print(f"[core.guardian] TTS import failed: {exc}")
    TextToSpeechEngine = None
    DummyTTS = None

try:
    from voice.ai_chat import AIChatEngine
except Exception as exc:
    print(f"[core.guardian] AIChat import failed: {exc}")
    AIChatEngine = None


class AlertService:
    def __init__(self, shared_state):
        self.shared_state = shared_state
        self._alarm_prompts = self._load_alarm_prompts()

    def _load_alarm_prompts(self):
        path = "prompts/alarm_responses.json"
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _get_prompt(self, key, lang="fa"):
        item = self._alarm_prompts.get(key, {})
        return item.get(lang, item.get("en", key))

    def trigger_alarm(self, reasons):
        self.shared_state.alarm_status = "triggered"
        self.shared_state.alarm_reasons = reasons if isinstance(reasons, list) else [reasons]

        if not getattr(self.shared_state, "alarm_muted", False):
            print("ALARM TRIGGERED:", reasons)

    def clear_alarm(self):
        self.shared_state.alarm_status = "clear"
        self.shared_state.alarm_reasons = []

    def mute_alarm(self):
        self.shared_state.alarm_muted = True

    def unmute_alarm(self):
        self.shared_state.alarm_muted = False


class GuardianCore(threading.Thread):
    def __init__(
        self,
        config,
        shared_state,
        alert_service,
        esp32_client=None,
        wakeword_detector=None,
        stt_engine=None,
        tts_engine=None,
        ai_chat=None,
    ):
        super().__init__(daemon=True)

        self.config = config
        self.shared_state = shared_state
        self.alert_service = alert_service
        self.esp32_client = esp32_client
        self.wakeword_detector = wakeword_detector
        self.stt_engine = stt_engine
        self.tts_engine = tts_engine
        self.ai_chat = ai_chat

        self._guardian_running = True
        self.tts_engine = self.tts_engine or self._init_tts_engine()
        self.ai_chat = self.ai_chat or self._init_ai_chat()

        self.gas_threshold = self.get_config_value(
            "GAS_THRESHOLD",
            self.get_config_value("ALARM_THRESHOLD_MQ9", 2000)
        )
        self.temp_threshold = self.get_config_value(
            "TEMP_THRESHOLD",
            self.get_config_value("ALARM_THRESHOLD_TEMP", 50)
        )
        self.monitor_interval = self.get_config_value("MONITOR_INTERVAL", 5)

    def _init_tts_engine(self):
        if TextToSpeechEngine is None:
            return DummyTTS() if DummyTTS else _FallbackDummyTTS()
        try:
            return TextToSpeechEngine(self.config)
        except Exception as e:
            print(f"Could not initialize TextToSpeechEngine: {e}")
            return DummyTTS() if DummyTTS else _FallbackDummyTTS()

    def _init_ai_chat(self):
        if AIChatEngine is None:
            return None
        try:
            return AIChatEngine(self.config)
        except Exception as e:
            print(f"Could not initialize AIChatEngine: {e}")
            return None

    def get_config_value(self, key, default=None):
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def get_esp32_data(self):
        try:
            if self.esp32_client is not None:
                return self.esp32_client.get_data()
            return {}
        except Exception as e:
            print(f"Error getting ESP32 data: {e}")
            return {}

    def evaluate_alarm(self, data):
        if not data:
            return False, []

        reasons = []

        gas_value = data.get("gas", 0)
        if gas_value is not None and gas_value > self.gas_threshold:
            reasons.append("Gas Leak Detected")

        flame_value = data.get("flame", False)
        if flame_value:
            reasons.append("Flame Detected")

        motion_value = data.get("motion", False)
        if motion_value:
            reasons.append("Motion Detected")

        door_value = data.get("door", False)
        if door_value:
            reasons.append("Door Opened")

        temperature_value = data.get("temperature", 0)
        if temperature_value is not None and temperature_value > self.temp_threshold:
            reasons.append("High Temperature")

        return len(reasons) > 0, reasons

    def _announce_alarm(self, reasons):
        """
        Use AI to generate natural alarm speech, or fallback to simple TTS.
        """
        if self.tts_engine is None:
            print("ALARM:", reasons)
            return

        if self.ai_chat is not None:
            try:
                text = self.ai_chat.summarize_alarms(reasons, lang="fa")
                self.tts_engine.speak(text)
                return
            except Exception as e:
                print(f"[GuardianCore] AI alarm announcement failed: {e}")

        # Fallback simple announcement
        message = f"Alert! {' and '.join(reasons)} detected!"
        self.tts_engine.speak(message)

    def run(self):
        try:
            self.tts_engine.speak("Guardian AI is now online.")
        except Exception:
            pass

        self.shared_state.guardian_active = True

        while self._guardian_running:
            try:
                data = self.get_esp32_data()

                if data:
                    with self.shared_state.current_data_lock:
                        self.shared_state.current_data = data

                    alarm_triggered, reasons = self.evaluate_alarm(data)

                    if alarm_triggered:
                        self.alert_service.trigger_alarm(reasons)
                        self._announce_alarm(reasons)
                    else:
                        self.alert_service.clear_alarm()

                time.sleep(self.monitor_interval)

            except Exception as e:
                print(f"GuardianCore error: {e}")
                time.sleep(2)

    def stop(self):
        self._guardian_running = False
        self.shared_state.guardian_active = False

    def handle_text(self, text):
        text = text.lower().strip()
        if self.ai_chat is not None:
            try:
                ctx = self.shared_state.current_data if hasattr(self.shared_state, "current_data") else {}
                return self.ai_chat.chat(text, sensor_context=ctx)
            except Exception as e:
                print(f"[GuardianCore.handle_text] AI chat failed: {e}")

        if "وضعیت" in text:
            return "همه چیز عادی است"
        if "alarm" in text:
            return "هشدار بررسی شد"
        return f"پیام دریافت شد: {text}"

    def chat(self, text, speak=True, raise_errors=False):
        if self.ai_chat is not None:
            try:
                ctx = self.shared_state.current_data if hasattr(self.shared_state, "current_data") else {}
                try:
                    reply = self.ai_chat.chat(text, sensor_context=ctx, raise_errors=raise_errors)
                except TypeError:
                    reply = self.ai_chat.chat(text, sensor_context=ctx)
                if speak and self.tts_engine:
                    try:
                        self.tts_engine.speak(reply)
                    except Exception as e:
                        print(f"[GuardianCore.chat] TTS speak failed: {e}")
                return reply
            except Exception as e:
                print(f"[GuardianCore.chat] AI chat failed: {e}")
                if raise_errors:
                    raise

        reply_text = "پاسخ Guardian آماده است."
        if speak and self.tts_engine:
            try:
                self.tts_engine.speak(reply_text)
            except Exception:
                pass
        return reply_text


class _FallbackDummyTTS:
    def speak(self, text):
        print(f"[DummyTTS] {text}")

    def stop(self):
        pass

    def enable(self):
        pass

    def disable(self):
        pass
