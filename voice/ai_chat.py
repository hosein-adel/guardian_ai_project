import json
import os
from openai import OpenAI


class AIChatError(RuntimeError):
    """Raised when the LLM chat provider cannot return a valid response."""


class AIChatEngine:
    """
    OpenAI Chat Completions client for Guardian AI.
    Uses GPT-5-mini / GPT-4o via the GapGPT API bridge.
    """

    def __init__(self, config=None):
        self.config = config
        api_key = getattr(config, "OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
        base_url = getattr(config, "OPENAI_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.gapgpt.app/v1"))
        self.model = getattr(config, "OPENAI_CHAT_MODEL", os.getenv("OPENAI_CHAT_MODEL", "gpt-5-mini"))
        self.timeout = float(getattr(config, "OPENAI_CHAT_TIMEOUT", os.getenv("OPENAI_CHAT_TIMEOUT", "20")))
        self.max_retries = int(getattr(config, "OPENAI_MAX_RETRIES", os.getenv("OPENAI_MAX_RETRIES", "0")))
        self.system_prompt = self._load_system_prompt(config)
        self.client = (
            OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
            if api_key else None
        )

    def _load_system_prompt(self, config):
        path = getattr(config, "SYSTEM_PROMPT_PATH", "prompts/system.txt")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return (
                "You are Guardian AI, an intelligent home security and monitoring assistant. "
                "Answer concisely in the user's language (Persian or English)."
            )

    def chat(self, user_text: str, sensor_context: dict = None, raise_errors: bool = False) -> str:
        if self.client is None:
            message = "OpenAI API key not configured."
            if raise_errors:
                raise AIChatError(message)
            return f"[AIChatEngine] {message}"

        messages = [
            {"role": "system", "content": self.system_prompt}
        ]

        if sensor_context:
            # Keep context compact and avoid sending raw repeated blobs to speed up chat.
            context = sensor_context.get("hardware_sensors", sensor_context) if isinstance(sensor_context, dict) else sensor_context
            ctx = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            messages.append({"role": "system", "content": f"Current sensor context: {ctx}"})

        messages.append({"role": "user", "content": user_text})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.4,
                max_tokens=300,
            )
            content = response.choices[0].message.content
            if not content or not str(content).strip():
                raise AIChatError("LLM returned an empty response.")
            return str(content).strip()
        except Exception as e:
            if isinstance(e, AIChatError):
                message = str(e)
            else:
                message = f"LLM provider connection/error: {str(e)}"
            print(f"[AIChatEngine] OpenAI error: {message}")
            if raise_errors:
                raise AIChatError(message) from e
            return f"خطا در ارتباط با AI: {message}"

    def summarize_alarms(self, reasons: list, lang: str = "fa") -> str:
        """
        Generate a natural alarm announcement using OpenAI.
        """
        if self.client is None:
            return ", ".join(reasons)

        prompt = f"Alarms active: {', '.join(reasons)}. Language: {lang}."
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are Guardian AI. Announce alarms urgently and clearly in the requested language. Keep it under 2 sentences."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=180,
            )
            return response.choices[0].message.content.strip()
        except Exception:
            return ", ".join(reasons)
