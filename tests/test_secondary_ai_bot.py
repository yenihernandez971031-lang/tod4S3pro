import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import secondary_ai_bot as secondary
import web_store


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
    def __init__(self, data=""):
        self.data = data
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


class FakeBroadcaster(FakeBot):
    def __init__(self):
        super().__init__()
        self.sent_messages = []

    async def send_message(self, chat_id, text):
        self.sent_messages.append((chat_id, text))


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
    assert [button.style for button in buttons] == ["primary", "success", "success"]


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
        assert "pausar" in admin_options["reply_markup"].inline_keyboard[0][0].text.lower()

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


def test_admin_statistics_button_opens_a_distinct_statistics_view():
    async def run():
        admin_id = next(iter(secondary.ADMIN_USER_IDS))
        button = next(
            button
            for row in secondary._admin_keyboard().inline_keyboard
            for button in row
            if button.text == "📊 Estadísticas"
        )
        assert button.callback_data == "secondary:admin:stats"
        query = FakeQuery(button.callback_data)
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=admin_id),
            effective_chat=SimpleNamespace(type="private"),
        )
        await secondary._admin_callback(update, SimpleNamespace(user_data={}, bot=FakeBroadcaster()))
        assert "Estadísticas del bot" in query.edited[0][0]
        assert "Usuarios registrados" in query.edited[0][0]
        back = query.edited[0][1]["reply_markup"].inline_keyboard[0][0]
        assert back.callback_data == "secondary:admin:refresh"

    asyncio.run(run())


def test_start_shows_admin_panel_entry_only_to_owner_in_private_chat():
    async def run():
        admin_id = next(iter(secondary.ADMIN_USER_IDS))
        admin_update = FakeUpdate(admin_id)
        await secondary._start(admin_update, SimpleNamespace())
        text, options = admin_update.effective_message.calls[0]
        button = options["reply_markup"].inline_keyboard[0][0]
        assert "administrador" in text
        assert button.callback_data == "secondary:admin:open"
        assert button.style == "primary"

        stranger_update = FakeUpdate(654)
        await secondary._start(stranger_update, SimpleNamespace(bot=FakeBot()))
        assert stranger_update.effective_message.calls[0][0].startswith("✨ <b>¡Bienvenido")
        assert "secondary:admin:open" not in str(stranger_update.effective_message.calls[0][1])

    asyncio.run(run())


def test_secondary_bot_refuses_admin_token(monkeypatch):
    monkeypatch.setattr(secondary, "TOKEN", "same-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "same-token")
    assert secondary.start_secondary_ai_bot() is None


def test_secondary_child_uses_the_backup_state_file(monkeypatch, tmp_path):
    monkeypatch.setattr(secondary, "TOKEN", "secondary-test-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "primary-test-token")
    state_file = tmp_path / "secondary_ai_bot_state.json"
    captured = {}

    def fake_popen(command, env):
        captured["command"] = command
        captured["env"] = env
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(secondary.subprocess, "Popen", fake_popen)
    process = secondary.start_secondary_ai_bot(state_file)
    assert process.pid == 123
    assert captured["env"]["SECONDARY_AI_BOT_STATE_FILE"] == str(state_file)


def test_secondary_bot_does_not_read_ai_provider_key():
    source = Path(secondary.__file__).read_text(encoding="utf-8")
    assert "IASINCENSURA" not in source
    assert "dispatch_telegram_ai_chat" in source


def test_question_quota_is_ten_questions_per_rolling_ten_hours(monkeypatch, tmp_path):
    user_id = 734
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(secondary, "STATE_FILE", state_file)
    secondary._QUESTIONS.pop(user_id, None)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(secondary.time, "time", lambda: clock[0])

    assert all(secondary._rate_allowed(user_id) for _ in range(10))
    assert not secondary._rate_allowed(user_id)
    assert secondary._quota_status(user_id)[0] == 0

    clock[0] += secondary.QUESTION_WINDOW_SECONDS - 1
    assert not secondary._rate_allowed(user_id)
    clock[0] += 2
    assert secondary._rate_allowed(user_id)


def test_quota_is_persisted_between_bot_restarts(monkeypatch, tmp_path):
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(secondary, "STATE_FILE", state_file)
    user_id = 735
    secondary._QUESTIONS[user_id] = [time_value for time_value in [1_800_000_001.0]]
    with secondary._RATE_LOCK:
        secondary._save_state_locked()
    secondary._QUESTIONS.pop(user_id, None)

    secondary._load_state()

    assert secondary._QUESTIONS[user_id] == [1_800_000_001.0]


def test_admin_toggle_pauses_and_resumes_ai(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setattr(secondary, "STATE_FILE", tmp_path / "bot_state.json")
        old_enabled = secondary._AI_ENABLED
        try:
            admin = next(iter(secondary.ADMIN_USER_IDS))
            update = SimpleNamespace(
                callback_query=FakeQuery("secondary:admin:toggle"),
                effective_user=SimpleNamespace(id=admin),
                effective_chat=SimpleNamespace(type="private"),
            )
            context = SimpleNamespace(user_data={}, bot=FakeBroadcaster())
            await secondary._admin_callback(update, context)
            assert secondary._AI_ENABLED is (not old_enabled)
            assert ("pausada" if old_enabled else "activa") in update.callback_query.edited[0][0]
        finally:
            secondary._AI_ENABLED = old_enabled

    asyncio.run(run())


def test_admin_can_ban_and_unban_user_with_interactive_buttons(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setattr(secondary, "STATE_FILE", tmp_path / "bot_state.json")
        admin = next(iter(secondary.ADMIN_USER_IDS))
        target = 891
        for action in ("ban", "unban"):
            update = SimpleNamespace(
                callback_query=FakeQuery(f"secondary:admin:{action}:{target}"),
                effective_user=SimpleNamespace(id=admin),
                effective_chat=SimpleNamespace(type="private"),
            )
            await secondary._admin_callback(update, SimpleNamespace(user_data={}, bot=FakeBroadcaster()))
            assert (target in secondary._BANNED_USERS) is (action == "ban")

    asyncio.run(run())


def test_paused_ai_rejects_user_before_calling_backend(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setattr(secondary, "STATE_FILE", tmp_path / "bot_state.json")
        user_id = 893
        old_enabled = secondary._AI_ENABLED
        secondary._AI_ENABLED = False
        secondary._JOIN_CONFIRMED.add(user_id)
        monkeypatch.setattr(
            secondary,
            "dispatch_telegram_ai_chat",
            lambda *_args: (_ for _ in ()).throw(AssertionError("IA no debe ejecutarse mientras está pausada")),
        )
        try:
            update = FakeUpdate(user_id)
            await secondary._chat(update, SimpleNamespace(bot=FakeBot()))
            assert "pausada" in update.effective_message.calls[0][0]
        finally:
            secondary._AI_ENABLED = old_enabled
            secondary._JOIN_CONFIRMED.discard(user_id)

    asyncio.run(run())


def test_admin_can_reset_selected_user_conversation(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setattr(secondary, "STATE_FILE", tmp_path / "bot_state.json")
        admin = next(iter(secondary.ADMIN_USER_IDS))
        target = 894
        secondary._HISTORY[(1, target)] = [{"role": "user", "content": "borrar"}]
        update = SimpleNamespace(
            callback_query=FakeQuery(f"secondary:admin:reset:{target}"),
            effective_user=SimpleNamespace(id=admin),
            effective_chat=SimpleNamespace(type="private"),
        )
        await secondary._admin_callback(update, SimpleNamespace(user_data={}, bot=FakeBroadcaster()))
        assert (1, target) not in secondary._HISTORY
        assert "Historial de conversación reiniciado" in update.callback_query.edited[0][0]

    asyncio.run(run())


def test_user_list_shows_registered_people_with_ban_status():
    user_id = 892
    secondary._USERS.clear()
    secondary._USERS[user_id] = {"first_name": "Prueba", "last_seen": 1}
    text, markup = secondary._admin_users_page()
    assert "Total:" in text
    assert any(str(user_id) in button.text for row in markup.inline_keyboard for button in row)


def test_admin_announcement_requires_preview_confirmation(monkeypatch):
    async def run():
        admin = next(iter(secondary.ADMIN_USER_IDS))
        target_chat = 876
        secondary._USERS.clear()
        secondary._USERS[target_chat] = {"private_chat_id": target_chat}
        query = FakeQuery("secondary:admin:announce")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=admin),
            effective_chat=SimpleNamespace(type="private"),
        )
        context = SimpleNamespace(user_data={}, bot=FakeBroadcaster())
        await secondary._admin_callback(update, context)
        assert context.user_data["admin_awaiting_announcement"]

        message_update = FakeUpdate(admin, text="Mantenimiento esta noche")
        await secondary._admin_text(message_update, context)
        preview, options = message_update.effective_message.calls[0]
        assert "Vista previa" in preview
        confirm = options["reply_markup"].inline_keyboard[0][0]
        assert confirm.callback_data == "secondary:admin:announce:send"

        confirm_update = SimpleNamespace(
            callback_query=FakeQuery(confirm.callback_data),
            effective_user=SimpleNamespace(id=admin),
            effective_chat=SimpleNamespace(type="private"),
        )
        await secondary._admin_callback(confirm_update, context)
        assert context.bot.sent_messages == [(target_chat, "Mantenimiento esta noche")]

    asyncio.run(run())


def test_markdown_code_answers_are_rendered_as_copyable_telegram_code_blocks():
    rendered = secondary._format_answer_for_telegram(
        "Ejemplo:\n```python\nprint('<hola>')\n```"
    )
    assert "<pre><code class=\"language-python\">print('&lt;hola&gt;')" in rendered
    assert "</code></pre>" in rendered


def test_telegram_ai_backend_uses_code_instructions_and_ten_hour_cap(monkeypatch):
    monkeypatch.setenv("IASINCENSURA", "sk-notrack-test-key-for-telegram")
    monkeypatch.setattr(web_store, "_AI_TELEGRAM_REQUESTS_BY_USER", {})
    clock = [1_800_100_000.0]
    monkeypatch.setattr(web_store.time, "monotonic", lambda: clock[0])
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({"choices": [{"message": {"content": "```python\nprint('ok')\n```"}}]}).encode()

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setattr(web_store.urllib.request, "urlopen", fake_urlopen)
    messages = [{"role": "user", "content": "Escribe un ejemplo en Python"}]
    for _ in range(10):
        payload, status = web_store.dispatch_telegram_ai_chat(959, messages)
        assert status == 200 and payload["ok"]
    limited, status = web_store.dispatch_telegram_ai_chat(959, messages)
    assert status == 429
    assert limited["error"] == "ai_10_hour_limit"
    assert captured["body"]["max_tokens"] == 768
    assert "solución completa y ejecutable" in captured["body"]["messages"][0]["content"]

    clock[0] += secondary.QUESTION_WINDOW_SECONDS + 1
    payload, status = web_store.dispatch_telegram_ai_chat(959, messages)
    assert status == 200 and payload["ok"]
