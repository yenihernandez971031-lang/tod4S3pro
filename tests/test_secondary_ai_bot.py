import asyncio
from types import SimpleNamespace
from pathlib import Path

import secondary_ai_bot as secondary


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))


class FakeUpdate:
    def __init__(self, user_id=123):
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(type="private")
        self.effective_message = FakeMessage()


class FakeQuery:
    def __init__(self):
        self.answered = False
        self.edited = []

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        self.edited.append((text, kwargs))


def test_join_keyboard_contains_two_channel_links_and_visual_confirmation_button():
    buttons = [
        button
        for row in secondary._join_keyboard().inline_keyboard
        for button in row
    ]
    urls = [button.url for button in buttons if button.url]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert callbacks == ["secondary:verify"]
    assert secondary.CHANNEL_ONE_URL in urls
    assert secondary.CHANNEL_TWO_URL in urls


def test_chat_shows_visual_join_prompt_until_confirmation():
    async def run():
        user_id = 123
        secondary._JOIN_CONFIRMED.discard(user_id)
        update = FakeUpdate(user_id)

        assert not await secondary._require_access(update)
        text, options = update.effective_message.calls[0]
        assert "Únete a los dos canales" in text
        assert options["reply_markup"] is not None

    asyncio.run(run())


def test_pressing_already_joined_unlocks_bot_without_membership_lookup():
    async def run():
        user_id = 456
        secondary._JOIN_CONFIRMED.discard(user_id)
        query = FakeQuery()
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=user_id),
        )

        await secondary._verify(update, SimpleNamespace())

        assert query.answered
        assert user_id in secondary._JOIN_CONFIRMED
        assert "Ya puedes usar la IA" in query.edited[0][0]
        assert await secondary._require_access(FakeUpdate(user_id))

    asyncio.run(run())


def test_secondary_bot_refuses_admin_token(monkeypatch):
    monkeypatch.setattr(secondary, "TOKEN", "same-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "same-token")
    assert secondary.start_secondary_ai_bot() is None


def test_secondary_bot_does_not_read_ai_provider_key():
    source = Path(secondary.__file__).read_text(encoding="utf-8")
    assert "IASINCENSURA" not in source
    assert "dispatch_telegram_ai_chat" in source
