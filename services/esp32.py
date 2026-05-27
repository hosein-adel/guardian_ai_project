import requests
import json
import time
import threading


class ESP32Client:
    def __init__(self, esp32_ip=None, port=80, timeout=5):
        """
        Client برای ارتباط Flask Backend با ESP32 MicroPython.

        esp32_ip می‌تواند یکی از این‌ها باشد:
        - "192.168.43.219"
        - "http://192.168.43.219"
        - "http://192.168.43.219:80"

        اگر esp32_ip داده نشود، تلاش می‌کند از config.py مقدار ESP32_IP را بخواند.
        """

        if esp32_ip is None:
            try:
                import config
                esp32_ip = getattr(config, "ESP32_IP", "192.168.43.219")
            except Exception:
                esp32_ip = "192.168.43.219"

        self.port = port
        self.timeout = timeout
        self.error_log_interval = 30
        self._last_error_log = {}
        self._last_online = None
        self._log_lock = threading.Lock()
        self.last_error = ""
        self.last_error_detail = ""
        self.last_error_at = None
        # ESP32 is a local LAN device; never send it through system/VPN HTTP proxies.
        self.proxies = {"http": "", "https": ""}

        esp32_ip = str(esp32_ip).strip()

        # اگر کاربر به‌اشتباه URL کامل داد، هندلش کن
        if esp32_ip.startswith("http://") or esp32_ip.startswith("https://"):
            self.base_url = esp32_ip.rstrip("/")
        else:
            self.base_url = f"http://{esp32_ip}:{self.port}"

        print(f"[ESP32Client] Base URL: {self.base_url}")

    def _log_issue(self, key, message, detail=""):
        """Throttle repeated offline/error messages so dashboard/guardian polling does not spam logs."""
        now = time.time()
        with self._log_lock:
            self.last_error = key
            self.last_error_detail = str(detail or "")
            self.last_error_at = now
            last = self._last_error_log.get(key, 0)
            if now - last >= self.error_log_interval:
                suffix = f" | detail: {detail}" if detail else ""
                print(f"{message}{suffix}")
                self._last_error_log[key] = now

    def _mark_online(self):
        if self._last_online is False:
            print(f"[ESP32Client] ESP32 connection restored: {self.base_url}")
        self._last_online = True
        self.last_error = ""
        self.last_error_detail = ""
        self.last_error_at = None

    def _mark_offline(self):
        self._last_online = False

    def is_online(self):
        """
        تست اتصال به خود ESP32.
        طبق main.py مسیر / وجود دارد و باید پاسخ بدهد.
        """

        try:
            response = requests.get(
                f"{self.base_url}/",
                timeout=self.timeout,
                proxies=self.proxies,
            )

            online = response.status_code == 200
            if online:
                self._mark_online()
            else:
                self._mark_offline()
            return online

        except requests.exceptions.RequestException as e:
            self._mark_offline()
            self._log_issue("is_online", "[ESP32Client] ESP32 is offline or unreachable", e)
            return False

    def get_sensor_data(self):
        """
        گرفتن داده سنسورها از ESP32.

        ESP32 طبق main.py مسیر زیر را دارد:
        GET /data

        خروجی مورد انتظار از ESP32:
        {
            "mq9": ...,
            "temperature": ...,
            "gas_leak": ...,
            "motion": ...,
            "door_open": ...
        }
        """

        url = f"{self.base_url}/data"

        try:
            response = requests.get(url, timeout=self.timeout, proxies=self.proxies)
            response.raise_for_status()

            data = response.json()

            # نرمال‌سازی داده‌ها برای اینکه هم با نام‌های ESP32 کار کند،
            # هم با نام‌هایی که ممکن است در dashboard یا app.py استفاده شده باشند.
            normalized_data = {
                # فیلدهای اصلی ESP32
                "mq9": data.get("mq9", 0),
                "temperature": data.get("temperature", 0),
                "gas_leak": data.get("gas_leak", 0),
                "motion": data.get("motion", 0),
                "door_open": data.get("door_open", 0),

                # فیلدهای سازگار برای بخش‌های دیگر پروژه
                "gas": data.get("gas", data.get("mq9", 0)),
                "door": data.get("door", data.get("door_open", 0)),
                "flame": data.get("flame", 0),
                "alarm": data.get("alarm", False),
                "alarm_muted": data.get("alarm_muted", False),
                "guardian_active": data.get("guardian_active", True),

                # وضعیت ارتباط
                "esp32_online": True,
                "source": "esp32"
            }

            # اگر ESP32 فیلدهای اضافه‌ای هم فرستاد، حذف نشوند
            normalized_data.update(data)
            self._mark_online()

            return normalized_data

        except requests.exceptions.Timeout as e:
            self._mark_offline()
            self._log_issue("timeout", f"[ESP32Client] Timeout while requesting: {url}", e)
            return self._offline_data("timeout", detail=e)

        except requests.exceptions.ConnectionError as e:
            self._mark_offline()
            self._log_issue("connection_error", f"[ESP32Client] Connection error. Cannot reach ESP32 at: {url}", e)
            return self._offline_data("connection_error", detail=e)

        except requests.exceptions.HTTPError as e:
            self._mark_offline()
            self._log_issue("http_error", f"[ESP32Client] HTTP error from ESP32: {e}", e)
            return self._offline_data("http_error", detail=e)

        except json.JSONDecodeError as e:
            self._mark_offline()
            self._log_issue("invalid_json", f"[ESP32Client] Invalid JSON received from ESP32: {url}", e)
            return self._offline_data("invalid_json", detail=e)

        except Exception as e:
            self._mark_offline()
            self._log_issue("unknown_error", f"[ESP32Client] Unexpected error: {e}", e)
            return self._offline_data("unknown_error", detail=e)


    def get_data(self):
        """
        Backward-compatible alias for old code.
        Some parts of the project may still call get_data().
        """
        return self.get_sensor_data()


    def send_config(self, config_data):
        """
        ارسال تنظیمات به ESP32.

        طبق main.py شما مسیر زیر وجود دارد:
        POST /config
        """

        url = f"{self.base_url}/config"

        try:
            response = requests.post(
                url,
                json=config_data,
                timeout=self.timeout,
                proxies=self.proxies,
            )

            response.raise_for_status()

            try:
                return response.json()
            except json.JSONDecodeError:
                return {
                    "success": True,
                    "message": response.text
                }

        except requests.exceptions.RequestException as e:
            self._mark_offline()
            self._log_issue("send_config", "[ESP32Client] Error sending config to ESP32", e)
            return {
                "success": False,
                "error": str(e),
                "esp32_last_error": "send_config",
                "esp32_last_error_detail": str(e),
            }

    def _offline_data(self, reason="offline", detail=""):
        """
        خروجی امن وقتی ESP32 در دسترس نیست.
        این باعث می‌شود Flask یا dashboard کرش نکند.
        """

        return {
            "mq9": 0,
            "temperature": 0,
            "gas_leak": 0,
            "motion": 0,
            "door_open": 0,

            "gas": 0,
            "door": 0,
            "flame": 0,
            "alarm": False,
            "alarm_muted": False,
            "guardian_active": False,

            "esp32_online": False,
            "esp32_base_url": self.base_url,
            "esp32_last_error": reason,
            "esp32_last_error_detail": str(detail or self.last_error_detail or ""),
            "source": "fallback",
            "error": reason
        }


if __name__ == "__main__":
    client = ESP32Client()
    print("ESP32 online:", client.is_online())
    print("Sensor data:")
    print(client.get_sensor_data())
