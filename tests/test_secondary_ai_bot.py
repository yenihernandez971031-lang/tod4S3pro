import asyncio
from pathlib import Path
from types import SimpleNamespace

import secondary_ai_bot as secondary


class FakeSentMessage:
    def __init__(self, text):
        self.text = text
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeMessage:
    def __init__(self, text="hola", chat_id=123):
        self.text = text
        self.chat_id = chat_id
        self.calls = []
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        sent = FakeSentMessage(text)
        self.sent.append(sent)
        return sent


class FakeUpdate:
    def __init__(self, user_id=123, chat_id=None, chat_type="private", text="hola"):
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(
            id=chat_id if chat_id is not None else user_id,
            type=chat_type,
        )
        self.effective_message = FakeMessage(text, self.effective_chat.id)


class FakeQuery:
    def __init__(self):
        self.answers = []
        self.edited = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edited.append((text, kwargs))


class FakeBot:
    def __init__(self):
        self.actions = []

    async def send_chat_action(self, chat_id, action):
        self.actions.append((chat_id, action))


def test_join_keyboard_has_two_channel_links_and_visual_continue_button():
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
    assert any("continuar" in button.text for button in buttons)


def test_unconfirmed_chat_shows_optional_styled_channel_panel():
    async def run():
        user_id = 123
        secondary._JOIN_CONFIRMED.discard(user_id)
        update = FakeUpdate(user_id)

        assert not await secondary._require_access(update)
        text, options = update.effective_message.calls[0]
        assert "¡Bienvenido a la IA!" in text
        assert "opcionales" in text
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

        assert query.answers
        assert user_id in secondary._JOIN_CONFIRMED
        assert "Acceso listo" in query.edited[0][0]
        assert await secondary._require_access(FakeUpdate(user_id))

    asyncio.run(run())


def test_authorized_user_can_use_bot_in_a_group():
    async def run():
        user_id = 789
        secondary._JOIN_CONFIRMED.add(user_id)
        update = FakeUpdate(user_id, chat_id=-100123, chat_type="supergroup")

        assert await secondary._require_access(update)

    asyncio.run(run())


def test_group_ai_response_shows_typing_and_visible_working_message(monkeypatch):
    async def run():
        user_id = 987
        chat_id = -100456
        secondary._JOIN_CONFIRMED.add(user_id)
        monkeypatch.setattr(secondary, "_rate_allowed", lambda _user_id: True)
        monkeypatch.setattr(
            secondary,
            "dispatch_telegram_ai_chat",
            lambda _user_id, _history: ({"ok": True, "reply": "Respuesta de prueba"}, 200),
        )
        bot = FakeBot()
        update = FakeUpdate(user_id, chat_id=chat_id, chat_type="group", text="¿Qué tal?")

        await secondary._chat(update, SimpleNamespace(bot=bot))

        assert bot.actions == [(chat_id, secondary.ChatAction.TYPING)]
        progress = update.effective_message.sent[0]
        assert "está trabajando" in progress.text
        assert progress.edits[-1][0] == "Respuesta de prueba"
        assert (chat_id, user_id) in secondary._HISTORY

    asyncio.run(run())


def test_admin_panel_is_private_and_only_available_to_configured_admin():
    async def run():
        admin_id = next(iter(secondary.ADMIN_USER_IDS))
        admin_update = FakeUpdate(admin_id)
        await secondary._admin(admin_update, SimpleNamespace())
        admin_text, admin_options = admin_update.effective_message.calls[0]
        assert "Panel de administración" in admin_text
        assert admin_options["reply_markup"] is not None
        assert "actual" in admin_options["reply_markup"].inline_keyboard[0][0].text.lower()

        other_update = FakeUpdate(333)
        await secondary._admin(other_update, SimpleNamespace())
        other_text = other_update.effective_message.calls[0][0]
        assert "No tienes permiso" in other_text
        assert "Panel de administración" not in other_text

        group_update = FakeUpdate(admin_id, chat_id=-1001, chat_type="group")
        await secondary._admin(group_update, SimpleNamespace())
        assert group_update.effective_message.calls == []

    asyncio.run(run())


def test_admin_refresh_callback_denies_other_users():
    async def run():
        query = FakeQuery()
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=333),
            effective_chat=SimpleNamespace(type="private"),
        )

        await secondary._admin_callback(update, SimpleNamespace())

        assert query.answers[0][1]["show_alert"] is True
        assert query.edited == []

    asyncio.run(run())


def test_secondary_bot_refuses_admin_token(monkeypatch):
    monkeypatch.setattr(secondary, "TOKEN", "same-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "same-token")
    assert secondary.start_secondary_ai_bot() is None


def test_secondary_bot_does_not_read_ai_provider_key():
    source = Path(secondary.__file__).read_text(encoding="utf-8")
    assert "IASINCENSURA" not in source
    assert "dispatch_telegram_ai_chat" in source
