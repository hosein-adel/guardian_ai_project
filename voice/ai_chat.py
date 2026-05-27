import json
import os
from openai import OpenAI


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
        self.system_prompt = self._load_system_prompt(config)
        self.client = OpenAI(api_key=api_key, base_url=base_url) if api_key else None

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

    def chat(self, user_text: str, sensor_context: dict = None) -> str:
        if self.client is None:
            return "[AIChatEngine] OpenAI API key not configured."

        messages = [
            {"role": "system", "content": self.system_prompt}
        ]

        if sensor_context:
            ctx = json.dumps(sensor_context, ensure_ascii=False, indent=0)
            messages.append({"role": "system", "content": f"Current sensor context: {ctx}"})

        messages.append({"role": "user", "content": user_text})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=512,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[AIChatEngine] OpenAI error: {e}")
            return f"خطا در ارتباط با AI: {str(e)}"

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
                temperature=0.5,
                max_tokens=256,
            )
            return response.choices[0].message.content.strip()
        except Exception:
            return ", ".join(reasons)
