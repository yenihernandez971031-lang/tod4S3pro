import asyncio
from types import SimpleNamespace
from pathlib import Path

import secondary_ai_bot as secondary


class FakeBot:
    def __init__(self, statuses):
        self.statuses = statuses

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.statuses[chat_id], is_member=True)


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))


class FakeUpdate:
    def __init__(self, bot, user_id=123):
        self._bot = bot
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(type="private")
        self.effective_message = FakeMessage()

    def get_bot(self):
        return self._bot


def test_membership_requires_both_channels():
    async def run():
        bot = FakeBot({secondary.CHANNEL_ONE: "member", secondary.CHANNEL_TWO: "left"})
        allowed, failed, missing = await secondary._membership_status(bot, 123)
        assert not allowed
        assert not failed
        assert missing == ["Canal secundario"]

    asyncio.run(run())


def test_membership_allows_user_in_both_channels():
    async def run():
        bot = FakeBot({secondary.CHANNEL_ONE: "member", secondary.CHANNEL_TWO: "administrator"})
        allowed, failed, missing = await secondary._membership_status(bot, 123)
        assert allowed
        assert not failed
        assert missing == []

    asyncio.run(run())


def test_chat_is_blocked_until_both_memberships_are_confirmed():
    async def run():
        bot = FakeBot({secondary.CHANNEL_ONE: "member", secondary.CHANNEL_TWO: "left"})
        update = FakeUpdate(bot)
        assert not await secondary._require_access(update)
        assert "Canal secundario" in update.effective_message.calls[0][0]

    asyncio.run(run())


def test_join_keyboard_contains_two_channel_links_and_verify_button():
    callbacks = [
        button.callback_data
        for row in secondary._join_keyboard().inline_keyboard
        for button in row
        if button.callback_data
    ]
    urls = [
        button.url
        for row in secondary._join_keyboard().inline_keyboard
        for button in row
        if button.url
    ]
    assert callbacks == ["secondary:verify"]
    assert secondary.CHANNEL_ONE_URL in urls
    assert secondary.CHANNEL_TWO_URL in urls


def test_secondary_bot_refuses_admin_token(monkeypatch):
    monkeypatch.setattr(secondary, "TOKEN", "same-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "same-token")
    assert secondary.start_secondary_ai_bot() is None


def test_secondary_bot_does_not_read_ai_provider_key():
    source = Path(secondary.__file__).read_text(encoding="utf-8")
    assert "IASINCENSURA" not in source
    assert "dispatch_telegram_ai_chat" in source
