import pytest
from voice.ai_chat import AIChatEngine
from unittest.mock import patch, MagicMock


class FakeConfig:
    OPENAI_API_KEY = "test"
    OPENAI_BASE_URL = "https://api.gapgpt.app/v1"
    OPENAI_CHAT_MODEL = "gpt-5-mini"
    SYSTEM_PROMPT_PATH = "prompts/system.txt"


@patch("voice.ai_chat.OpenAI")
def test_ai_chat_basic(mock_openai_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "پاسخ تست"
    mock_client.chat.completions.create.return_value = mock_response
    mock_openai_cls.return_value = mock_client

    engine = AIChatEngine(FakeConfig())
    reply = engine.chat("سلام")
    assert reply == "پاسخ تست"


def test_ai_chat_no_key():
    class NoKeyConfig:
        OPENAI_API_KEY = ""
    engine = AIChatEngine(NoKeyConfig())
    reply = engine.chat("سلام")
    assert "API key not configured" in reply


def test_ai_chat_summarize_alarms(mock_openai):
    with patch("voice.ai_chat.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "آلارم فعال است"
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        engine = AIChatEngine(FakeConfig())
        text = engine.summarize_alarms(["Gas Leak"], lang="fa")
        assert text == "آلارم فعال است"


def test_ai_chat_raise_errors_on_no_key():
    from voice.ai_chat import AIChatError

    class NoKeyConfig:
        OPENAI_API_KEY = ""

    engine = AIChatEngine(NoKeyConfig())
    with pytest.raises(AIChatError):
        engine.chat("سلام", raise_errors=True)
