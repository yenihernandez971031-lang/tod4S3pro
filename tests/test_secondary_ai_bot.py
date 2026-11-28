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
        self.memberships = {}

    async def send_chat_action(self, chat_id, action):
        self.actions.append((chat_id, action))

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.memberships.get(chat_id, "member"), is_member=True)

    async def get_me(self):
        return SimpleNamespace(username="ai_test_bot")


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
    assert any("verificar" in button.text for button in buttons)
    assert [button.style for button in buttons] == ["primary", "success", "success"]


def test_unconfirmed_chat_requires_both_channels_and_shows_styled_panel():
    async def run():
        user_id = 123
        secondary._JOIN_CONFIRMED.discard(user_id)
        update = FakeUpdate(user_id)

        assert not await secondary._require_access(update)
        text, options = update.effective_message.calls[0]
        assert "AiTod4S3 · Acceso" in text
        assert "dos canales" in text
        assert options["reply_markup"] is not None

    asyncio.run(run())


def test_confirmed_member_unlocks_bot_after_both_memberships_are_checked():
    async def run():
        user_id = 456
        secondary._JOIN_CONFIRMED.discard(user_id)
        query = FakeQuery()
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=user_id),
        )

        await secondary._verify(update, SimpleNamespace(bot=FakeBot()))

        assert query.answers
        assert user_id in secondary._JOIN_CONFIRMED
        assert "Acceso confirmado" in query.edited[0][0]
        assert await secondary._require_access(FakeUpdate(user_id))

    asyncio.run(run())


def test_legacy_visual_confirmations_are_not_trusted_after_upgrade(monkeypatch, tmp_path):
    user_id = 458
    state_file = tmp_path / "legacy_state.json"
    state_file.write_text(json.dumps({"join_confirmed": [user_id], "users": {}}), encoding="utf-8")
    monkeypatch.setattr(secondary, "STATE_FILE", state_file)
    secondary._JOIN_CONFIRMED.discard(user_id)
    secondary._load_state()
    assert user_id not in secondary._JOIN_CONFIRMED


def test_verify_keeps_user_blocked_if_either_channel_membership_is_missing():
    async def run():
        user_id = 457
        secondary._JOIN_CONFIRMED.discard(user_id)
        bot = FakeBot()
        bot.memberships[secondary.CHANNEL_TWO] = "left"
        query = FakeQuery()
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=user_id),
        )
        await secondary._verify(update, SimpleNamespace(bot=bot))
        assert user_id not in secondary._JOIN_CONFIRMED
        assert "No pude comprobar tu acceso" in query.edited[0][0]
        assert query.edited[0][1]["reply_markup"] is not None

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
        def fake_dispatch(_user_id, _history, **kwargs):
            kwargs["on_delta"]("Primer fragmento")
            return {"ok": True, "reply": "Respuesta de prueba"}, 200

        monkeypatch.setattr(secondary, "dispatch_telegram_ai_chat", fake_dispatch)
        bot = FakeBot()
        update = FakeUpdate(user_id, chat_id=chat_id, chat_type="group", text="¿Qué tal?")

        await secondary._chat(update, SimpleNamespace(bot=bot))

        assert bot.actions == [(chat_id, secondary.ChatAction.TYPING)]
        progress = update.effective_message.sent[0]
        assert "está trabajando" in progress.text
        assert any(text.startswith("✍️\nPrimer fragmento") for text, _ in progress.edits)
        assert progress.edits[-1][0].startswith("Respuesta de prueba")
        assert "Te quedan" in progress.edits[-1][0]
        assert any(
            button.callback_data == "secondary:user:quota"
            for row in progress.edits[-1][1]["reply_markup"].inline_keyboard
            for button in row
        )
        assert (chat_id, user_id) in secondary._HISTORY

    asyncio.run(run())


def test_admin_panel_is_private_and_only_available_to_configured_admin():
    async def run():
        admin_id = next(iter(secondary.ADMIN_USER_IDS))
        admin_update = FakeUpdate(admin_id)
        await secondary._admin(admin_update, SimpleNamespace())
        admin_text, admin_options = admin_update.effective_message.calls[0]
        assert "Administración · AiTod4S3" in admin_text
        assert admin_options["reply_markup"] is not None
        assert "pausar" in admin_options["reply_markup"].inline_keyboard[0][0].text.lower()

        other_update = FakeUpdate(333)
        await secondary._admin(other_update, SimpleNamespace())
        other_text = other_update.effective_message.calls[0][0]
        assert "No tienes permiso" in other_text
        assert "Administración · AiTod4S3" not in other_text

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
        assert "Estadísticas · AiTod4S3" in query.edited[0][0]
        assert "Usuarios registrados" in query.edited[0][0]
        back = query.edited[0][1]["reply_markup"].inline_keyboard[0][0]
        assert back.callback_data == "secondary:admin:refresh"

    asyncio.run(run())


def test_start_shows_admin_panel_and_user_preferences_to_owner_in_private_chat():
    async def run():
        admin_id = next(iter(secondary.ADMIN_USER_IDS))
        admin_update = FakeUpdate(admin_id)
        await secondary._start(admin_update, SimpleNamespace())
        text, options = admin_update.effective_message.calls[0]
        button = options["reply_markup"].inline_keyboard[0][0]
        assert "administrador" in text
        assert button.callback_data == "secondary:admin:open"
        assert button.style == "primary"
        callbacks = {
            item.callback_data
            for row in options["reply_markup"].inline_keyboard
            for item in row
        }
        assert "secondary:user:persona" in callbacks
        assert "secondary:user:temperature" in callbacks

        stranger_update = FakeUpdate(654)
        await secondary._start(stranger_update, SimpleNamespace(bot=FakeBot()))
        assert stranger_update.effective_message.calls[0][0].startswith("🤖 <b>AiTod4S3 · Acceso")
        assert "secondary:admin:open" not in str(stranger_update.effective_message.calls[0][1])

    asyncio.run(run())


def test_secondary_bot_refuses_admin_token(monkeypatch):
    monkeypatch.setattr(secondary, "TOKEN", "same-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "same-token")
    assert secondary.start_secondary_ai_bot() is None


def test_secondary_application_processes_updates_concurrently(monkeypatch):
    monkeypatch.setattr(secondary, "TOKEN", "123456:FAKE-TOKEN-ONLY-FOR-TESTS")
    application = secondary._build_secondary_application()
    assert application.update_processor.max_concurrent_updates == 16


def test_secondary_child_uses_the_backup_state_file(monkeypatch, tmp_path):
    monkeypatch.setattr(secondary, "TOKEN", "secondary-test-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "primary-test-token")
    state_file = tmp_path / "secondary_ai_bot_state.json"
    admin_file = tmp_path / "users.json"
    captured = {}

    def fake_popen(command, env):
        captured["command"] = command
        captured["env"] = env
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(secondary.subprocess, "Popen", fake_popen)
    process = secondary.start_secondary_ai_bot(state_file, admin_file)
    assert process.pid == 123
    assert captured["env"]["SECONDARY_AI_BOT_STATE_FILE"] == str(state_file)
    assert captured["env"]["USERS_DATA_FILE"] == str(admin_file)


def test_secondary_bot_does_not_read_ai_provider_key():
    source = Path(secondary.__file__).read_text(encoding="utf-8")
    assert 'os.getenv("IASINCENSURA"' not in source
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
    extra, status = web_store.dispatch_telegram_ai_chat(959, messages, question_limit=11)
    assert status == 200 and extra["ok"]
    assert captured["body"]["max_tokens"] == 512
    assert "solución ejecutable" in captured["body"]["messages"][0]["content"]
    for _ in range(12):
        payload, status = web_store.dispatch_telegram_ai_chat(959, messages, bypass_quota=True)
        assert status == 200 and payload["ok"]
    assert len(web_store._AI_TELEGRAM_REQUESTS_BY_USER[959]) == 11

    clock[0] += secondary.QUESTION_WINDOW_SECONDS + 1
    payload, status = web_store.dispatch_telegram_ai_chat(959, messages)
    assert status == 200 and payload["ok"]


def test_telegram_ai_streams_response_fragments_as_they_arrive(monkeypatch):
    monkeypatch.setenv("IASINCENSURA", "sk-notrack-test-key-for-telegram")
    monkeypatch.setattr(web_store, "_AI_TELEGRAM_REQUESTS_BY_USER", {})
    captured = {}
    fragments = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            for text in ("Hola", " mundo"):
                event = {"choices": [{"delta": {"content": text}}]}
                yield ("data: " + json.dumps(event) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setattr(web_store.urllib.request, "urlopen", fake_urlopen)
    payload, status = web_store.dispatch_telegram_ai_chat(
        959, [{"role": "user", "content": "Saluda"}], bypass_quota=True,
        on_delta=fragments.append,
    )
    assert status == 200
    assert payload == {"ok": True, "reply": "Hola mundo"}
    assert fragments == ["Hola", " mundo"]
    assert captured["body"]["stream"] is True
    assert captured["body"]["max_tokens"] == 192


def test_telegram_backend_refunds_quota_when_provider_fails(monkeypatch):
    monkeypatch.setenv("IASINCENSURA", "sk-notrack-test-key-for-telegram")
    monkeypatch.setattr(web_store, "_AI_TELEGRAM_REQUESTS_BY_USER", {})

    def fail_request(*_args, **_kwargs):
        raise web_store.urllib.error.URLError("provider unavailable")

    monkeypatch.setattr(web_store.urllib.request, "urlopen", fail_request)
    payload, status = web_store.dispatch_telegram_ai_chat(961, [{"role": "user", "content": "hola"}])
    assert status == 503
    assert payload["error"] == "ai_unavailable"
    assert 961 not in web_store._AI_TELEGRAM_REQUESTS_BY_USER


def test_chat_trims_long_history_before_sending_it_to_backend(monkeypatch, tmp_path):
    async def run():
        user_id = 962
        monkeypatch.setattr(secondary, "STATE_FILE", tmp_path / "bot_state.json")
        secondary._JOIN_CONFIRMED.add(user_id)
        secondary._HISTORY[(user_id, user_id)] = [
            {"role": "user", "content": "a" * 3_900},
            {"role": "assistant", "content": "b" * 3_900},
            {"role": "user", "content": "c" * 3_900},
            {"role": "assistant", "content": "d" * 3_900},
            {"role": "user", "content": "e" * 3_900},
            {"role": "assistant", "content": "f" * 4_500},
        ]
        monkeypatch.setattr(secondary, "_rate_allowed", lambda _user_id: True)
        sent_history = {}

        def fake_dispatch(_user_id, messages, **_kwargs):
            sent_history["messages"] = messages
            return {"ok": True, "reply": "Respuesta"}, 200

        monkeypatch.setattr(secondary, "dispatch_telegram_ai_chat", fake_dispatch)
        update = FakeUpdate(user_id, text="Pregunta actual")
        await secondary._chat(update, SimpleNamespace(bot=FakeBot()))

        messages = sent_history["messages"]
        assert sum(len(item["content"]) for item in messages) <= secondary.MAX_CONTEXT_CHARS
        assert messages[-1] == {"role": "user", "content": "Pregunta actual"}
        assert all(len(item["content"]) <= 4000 for item in messages)
        assert update.effective_message.sent[0].edits[-1][0].startswith("Respuesta")

    asyncio.run(run())


def test_admin_bypasses_both_local_and_backend_question_quotas(monkeypatch, tmp_path):
    async def run():
        admin_id = next(iter(secondary.ADMIN_USER_IDS))
        monkeypatch.setattr(secondary, "STATE_FILE", tmp_path / "bot_state.json")
        secondary._JOIN_CONFIRMED.add(admin_id)
        old_questions = list(secondary._QUESTIONS.get(admin_id, []))
        old_style = secondary._USERS.get(admin_id, {}).get("response_style")
        secondary._USERS.setdefault(admin_id, {})["response_style"] = "detailed"
        secondary._QUESTIONS[admin_id] = [secondary.time.time()] * secondary.QUESTION_LIMIT
        monkeypatch.setattr(
            secondary,
            "_rate_allowed",
            lambda _user_id: (_ for _ in ()).throw(AssertionError("El admin no debe consumir la cuota local")),
        )
        observed = {}

        def fake_dispatch(_user_id, _messages, **kwargs):
            observed.update(kwargs)
            return {"ok": True, "reply": "Admin responde"}, 200

        monkeypatch.setattr(secondary, "dispatch_telegram_ai_chat", fake_dispatch)
        update = FakeUpdate(admin_id, text="Consulta del admin")
        await secondary._chat(update, SimpleNamespace(bot=FakeBot()))

        assert observed["bypass_quota"] is True
        assert observed["response_style"] == "detailed"
        assert len(secondary._QUESTIONS[admin_id]) == secondary.QUESTION_LIMIT
        assert update.effective_message.sent[0].edits[-1][0] == "Admin responde"
        secondary._QUESTIONS[admin_id] = old_questions
        if old_style is None:
            secondary._USERS[admin_id].pop("response_style", None)
        else:
            secondary._USERS[admin_id]["response_style"] = old_style

    asyncio.run(run())


def test_failed_ai_request_refunds_local_quota_and_removes_unsent_prompt(monkeypatch, tmp_path):
    async def run():
        user_id = 963
        monkeypatch.setattr(secondary, "STATE_FILE", tmp_path / "bot_state.json")
        secondary._JOIN_CONFIRMED.add(user_id)
        secondary._QUESTIONS.pop(user_id, None)
        secondary._HISTORY.pop((user_id, user_id), None)
        monkeypatch.setattr(
            secondary,
            "dispatch_telegram_ai_chat",
            lambda *_args, **_kwargs: ({"ok": False, "error": "conversation_too_long"}, 413),
        )
        update = FakeUpdate(user_id, text="Pregunta que falla")
        await secondary._chat(update, SimpleNamespace(bot=FakeBot()))

        assert secondary._QUESTIONS[user_id] == []
        assert secondary._HISTORY[(user_id, user_id)] == []
        assert "no está disponible" in update.effective_message.sent[0].edits[-1][0]

    asyncio.run(run())


def test_delegated_main_bot_admin_is_loaded_for_quota_exemption(monkeypatch, tmp_path):
    admin_id = 987_654_321
    admins_file = tmp_path / "users.json"
    admins_file.write_text(json.dumps({"admin_users": {str(admin_id): "delegated"}}), encoding="utf-8")
    original_admin_ids = set(secondary.ADMIN_USER_IDS)
    monkeypatch.setattr(secondary, "ADMIN_USER_IDS", set(original_admin_ids))
    monkeypatch.setattr(secondary, "_ADMIN_IDS_LAST_REFRESH", 0.0)
    monkeypatch.setenv("USERS_DATA_FILE", str(admins_file))

    secondary._refresh_admin_user_ids()

    assert admin_id in secondary.ADMIN_USER_IDS


def test_referral_reward_is_unique_and_adds_one_question_up_to_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(secondary, "STATE_FILE", tmp_path / "bot_state.json")
    inviter, invited = 987_001, 987_002
    secondary._USERS[inviter] = {"referral_bonus": secondary.MAX_REFERRAL_BONUS - 1, "referrals": []}
    secondary._USERS[invited] = {"pending_referrer_id": inviter}
    assert secondary._complete_referral(invited)
    assert not secondary._complete_referral(invited)
    assert secondary._USERS[inviter]["referral_bonus"] == secondary.MAX_REFERRAL_BONUS
    assert secondary._USERS[inviter]["referrals"] == [invited]
    assert secondary._question_limit(inviter) == secondary.QUESTION_LIMIT + secondary.MAX_REFERRAL_BONUS
    secondary._USERS.pop(inviter, None)
    secondary._USERS.pop(invited, None)
    secondary._load_state()
    assert secondary._USERS[inviter]["referrals_count"] == 1
    assert secondary._USERS[inviter]["referral_bonus"] == secondary.MAX_REFERRAL_BONUS


def test_chat_history_is_persisted_in_bot_state_and_shown_to_admin(monkeypatch, tmp_path):
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(secondary, "STATE_FILE", state_file)
    user_id = 987_003
    secondary._HISTORY[(user_id, user_id)] = [
        {"role": "user", "content": "<script>hola</script>"},
        {"role": "assistant", "content": "Respuesta"},
    ]
    with secondary._RATE_LOCK:
        secondary._save_state_locked()
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["history"][f"{user_id}:{user_id}"][0]["content"] == "<script>hola</script>"
    secondary._HISTORY.pop((user_id, user_id), None)
    secondary._load_state()
    assert secondary._HISTORY[(user_id, user_id)][-1]["content"] == "Respuesta"
    text, _ = secondary._admin_user_history(user_id)
    assert "&lt;script&gt;hola&lt;/script&gt;" in text
    assert "<script>" not in text


def test_quota_status_includes_referral_questions():
    user_id = 987_004
    old_user = secondary._USERS.get(user_id)
    old_questions = secondary._QUESTIONS.get(user_id)
    secondary._USERS[user_id] = {"referral_bonus": 2}
    secondary._QUESTIONS[user_id] = [secondary.time.time()] * secondary.QUESTION_LIMIT
    try:
        assert secondary._quota_status(user_id)[0] == 2
    finally:
        if old_user is None:
            secondary._USERS.pop(user_id, None)
        else:
            secondary._USERS[user_id] = old_user
        if old_questions is None:
            secondary._QUESTIONS.pop(user_id, None)
        else:
            secondary._QUESTIONS[user_id] = old_questions


def test_user_buttons_open_remaining_quota_and_referral_share_link():
    async def run():
        user_id = 987_005
        secondary._JOIN_CONFIRMED.add(user_id)
        secondary._USERS[user_id] = {"referrals_count": 3, "referral_bonus": 2}
        secondary._QUESTIONS.pop(user_id, None)
        menu_labels = [
            button.text
            for row in secondary._user_keyboard().inline_keyboard
            for button in row
        ]
        assert "🎟️ Mis tokens" in menu_labels
        assert "💳 Mi saldo" not in menu_labels
        for action in ("quota", "referrals"):
            query = FakeQuery(f"secondary:user:{action}")
            update = SimpleNamespace(
                callback_query=query,
                effective_user=SimpleNamespace(id=user_id),
                effective_chat=SimpleNamespace(type="private"),
            )
            await secondary._user_callback(update, SimpleNamespace(bot=FakeBot()))
            assert query.edited
            text, options = query.edited[0]
            assert options["reply_markup"] is not None
            if action == "quota":
                assert "<b>Tus tokens</b>" in text
                assert "Te quedan <b>12/12 tokens</b>" in text
            else:
                assert "Invitaciones confirmadas: <b>3</b>" in text
                assert "Tokens extra: <b>2/10</b>" in text
                assert any(
                    button.url and "t.me/share/url" in button.url
                    for row in options["reply_markup"].inline_keyboard
                    for button in row
                )

    asyncio.run(run())


def test_user_can_choose_brief_or_detailed_responses(monkeypatch, tmp_path):
    async def run():
        user_id = 987_015
        monkeypatch.setattr(secondary, "STATE_FILE", tmp_path / "state.json")
        secondary._JOIN_CONFIRMED.add(user_id)
        query = FakeQuery("secondary:user:response_style")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(type="private"),
        )
        await secondary._user_callback(update, SimpleNamespace(bot=FakeBot()))
        assert "Longitud de las respuestas" in query.edited[0][0]
        detailed = next(
            button
            for row in query.edited[0][1]["reply_markup"].inline_keyboard
            for button in row
            if button.callback_data == "secondary:user:response_style:set:detailed"
        )
        assert detailed.text == "📚 Detallada"
        query = FakeQuery("secondary:user:response_style:set:detailed")
        update.callback_query = query
        await secondary._user_callback(update, SimpleNamespace(bot=FakeBot()))
        assert secondary._USERS[user_id]["response_style"] == "detailed"
        assert "Estilo actualizado" in query.edited[0][0]
        secondary._JOIN_CONFIRMED.discard(user_id)
        secondary._USERS.pop(user_id, None)

    asyncio.run(run())


def test_admin_token_adjustments_are_temporary_and_persisted(monkeypatch, tmp_path):
    user_id = 987_016
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(secondary, "STATE_FILE", state_file)
    old_user = secondary._USERS.get(user_id)
    old_questions = secondary._QUESTIONS.get(user_id)
    secondary._QUESTIONS[user_id] = []
    secondary._USERS[user_id] = {}
    try:
        applied, remaining = secondary._adjust_admin_tokens(user_id, 4)
        assert (applied, remaining) == (4, secondary.QUESTION_LIMIT + 4)
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        assert saved["users"][str(user_id)]["admin_token_bonus"] == 4
        applied, remaining = secondary._adjust_admin_tokens(user_id, -2)
        assert (applied, remaining) == (-2, secondary.QUESTION_LIMIT + 2)
    finally:
        if old_user is None:
            secondary._USERS.pop(user_id, None)
        else:
            secondary._USERS[user_id] = old_user
        if old_questions is None:
            secondary._QUESTIONS.pop(user_id, None)
        else:
            secondary._QUESTIONS[user_id] = old_questions


def test_temporary_admin_tokens_do_not_delay_renewal_quota(monkeypatch, tmp_path):
    user_id = 987_019
    monkeypatch.setattr(secondary, "STATE_FILE", tmp_path / "renewal-state.json")
    old_user = secondary._USERS.get(user_id)
    old_questions = secondary._QUESTIONS.get(user_id)
    was_renewal = user_id in secondary._RENEWAL_USERS
    secondary._USERS[user_id] = {
        "admin_token_bonus": 4,
        "admin_token_bonus_expires_at": secondary.time.time() + 3600,
    }
    secondary._QUESTIONS[user_id] = [secondary.time.time()] * secondary.QUESTION_LIMIT
    secondary._RENEWAL_USERS.discard(user_id)
    try:
        secondary._commit_question(user_id)
        assert user_id not in secondary._RENEWAL_USERS
        assert secondary._question_limit(user_id) == secondary.QUESTION_LIMIT + 4
        assert secondary._rate_allowed(user_id) is True
        secondary._USERS[user_id]["admin_token_bonus_expires_at"] = secondary.time.time() - 1
        secondary._quota_status(user_id)
        assert user_id in secondary._RENEWAL_USERS
        assert secondary._active_admin_token_bonus(user_id) == 0
        assert secondary._question_limit(user_id) == secondary.RENEWAL_QUESTION_LIMIT
    finally:
        if old_user is None:
            secondary._USERS.pop(user_id, None)
        else:
            secondary._USERS[user_id] = old_user
        if old_questions is None:
            secondary._QUESTIONS.pop(user_id, None)
        else:
            secondary._QUESTIONS[user_id] = old_questions
        if was_renewal:
            secondary._RENEWAL_USERS.add(user_id)
        else:
            secondary._RENEWAL_USERS.discard(user_id)


def test_admin_search_and_user_filters_find_matching_users():
    user_id = 987_017
    old_user = secondary._USERS.get(user_id)
    old_banned = user_id in secondary._BANNED_USERS
    secondary._USERS[user_id] = {
        "first_name": "PruebaFiltro",
        "username": "prueba_filtro",
        "last_seen": secondary.time.time(),
    }
    try:
        text, markup = secondary._admin_user_search("@prueba_filtro")
        assert "Coincidencias: 1" in text
        assert any(
            button.callback_data == f"secondary:admin:user:{user_id}"
            for row in markup.inline_keyboard for button in row
        )
        _text, active_markup = secondary._admin_users_page(0, "active")
        assert any(
            button.callback_data == f"secondary:admin:user:{user_id}"
            for row in active_markup.inline_keyboard for button in row
        )
        secondary._BANNED_USERS.add(user_id)
        _text, blocked_markup = secondary._admin_users_page(0, "blocked")
        assert any(
            button.callback_data == f"secondary:admin:user:{user_id}"
            for row in blocked_markup.inline_keyboard for button in row
        )
    finally:
        if old_user is None:
            secondary._USERS.pop(user_id, None)
        else:
            secondary._USERS[user_id] = old_user
        if not old_banned:
            secondary._BANNED_USERS.discard(user_id)


def test_only_owner_can_confirm_adding_and_removing_managed_admins(monkeypatch, tmp_path):
    async def run():
        owner_id = 5_721_542_653
        target_id = 987_018
        state_file = tmp_path / "admin-state.json"
        monkeypatch.setattr(secondary, "STATE_FILE", state_file)
        monkeypatch.setattr(secondary, "_ADMIN_IDS_LAST_REFRESH", 0.0)
        old_managed = set(secondary._MANAGED_ADMIN_IDS)
        secondary._MANAGED_ADMIN_IDS.discard(target_id)
        context = SimpleNamespace(user_data={})
        def update_for(user_id, text="", callback_data=None):
            query = FakeQuery(callback_data) if callback_data else None
            update = FakeUpdate(user_id, text=text)
            if query:
                update.callback_query = query
            return update

        try:
            owner_update = update_for(owner_id, callback_data="secondary:admin:admins:add")
            await secondary._admin_callback(owner_update, context)
            assert context.user_data["admin_awaiting_admin_change"] == "add"

            preview_update = update_for(owner_id, str(target_id))
            await secondary._admin_text(preview_update, context)
            assert "Confirmar cambio" in preview_update.effective_message.calls[0][0]
            assert context.user_data["admin_change_draft"] == {"operation": "add", "user_id": target_id}

            confirm_update = update_for(owner_id, callback_data="secondary:admin:admins:confirm")
            await secondary._admin_callback(confirm_update, context)
            assert target_id in secondary._MANAGED_ADMIN_IDS
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            assert target_id in saved["managed_admin_ids"]

            # Una persona administradora delegada no puede iniciar otro cambio de roles.
            non_owner_update = update_for(target_id, callback_data="secondary:admin:admins:remove")
            await secondary._admin_callback(non_owner_update, context)
            assert "Solo el propietario" in non_owner_update.callback_query.edited[0][0]

            context.user_data["admin_awaiting_admin_change"] = "remove"
            await secondary._admin_text(update_for(owner_id, str(target_id)), context)
            remove_update = update_for(owner_id, callback_data="secondary:admin:admins:confirm")
            await secondary._admin_callback(remove_update, context)
            assert target_id not in secondary._MANAGED_ADMIN_IDS
        finally:
            secondary._MANAGED_ADMIN_IDS.clear()
            secondary._MANAGED_ADMIN_IDS.update(old_managed)
            secondary.ADMIN_USER_IDS.discard(target_id)
            secondary._ADMIN_IDS_LAST_REFRESH = 0.0

    asyncio.run(run())


def test_admin_error_panel_explains_fallback_error_without_exposing_secrets():
    previous = list(secondary._RECENT_ERRORS)
    try:
        secondary._RECENT_ERRORS[:] = [
            "2026-11-28 20:02:45 UTC · RuntimeError · ai_fallback_unavailable:503"
        ]
        text = secondary._admin_errors_text()
        assert "ai_fallback_unavailable:503" in text
        assert "IASINCENSURA" in text
        assert "NoTrack" in text
        assert "no publiques ni compartas su valor" in text
    finally:
        secondary._RECENT_ERRORS[:] = previous


def test_initial_quota_transitions_to_five_only_after_tenth_success(monkeypatch, tmp_path):
    async def run():
        user_id = 9_870_090
        state_file = tmp_path / "quota-transition.json"
        monkeypatch.setattr(secondary, "STATE_FILE", state_file)
        secondary._QUESTIONS.pop(user_id, None)
        secondary._RENEWAL_USERS.discard(user_id)
        secondary._JOIN_CONFIRMED.add(user_id)
        monkeypatch.setattr(
            secondary,
            "dispatch_telegram_ai_chat",
            lambda *_args, **_kwargs: ({"ok": True, "reply": "Listo"}, 200),
        )

        for index in range(10):
            update = FakeUpdate(user_id, text=f"Pregunta {index}")
            await secondary._chat(update, SimpleNamespace(bot=FakeBot()))
            assert update.effective_message.sent[0].edits[-1][0].startswith("Listo")
            if index < 9:
                assert user_id not in secondary._RENEWAL_USERS
        assert user_id in secondary._RENEWAL_USERS
        assert secondary._question_limit(user_id) == 5
        assert len(secondary._QUESTIONS[user_id]) == 10

        # The phase survives a process restart through the persistent state file.
        secondary._QUESTIONS.pop(user_id, None)
        secondary._RENEWAL_USERS.discard(user_id)
        secondary._load_state()
        assert user_id in secondary._RENEWAL_USERS
        assert secondary._question_limit(user_id) == 5

    try:
        asyncio.run(run())
    finally:
        secondary._QUESTIONS.pop(9_870_090, None)
        secondary._RENEWAL_USERS.discard(9_870_090)
        secondary._HISTORY.pop((9_870_090, 9_870_090), None)
        secondary._JOIN_CONFIRMED.discard(9_870_090)


def test_identity_questions_are_answered_without_calling_ai_backend(monkeypatch):
    async def run():
        user_id = 9_870_091
        secondary._JOIN_CONFIRMED.add(user_id)
        monkeypatch.setattr(
            secondary,
            "dispatch_telegram_ai_chat",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("No debe llamar a la IA")),
        )
        for question in (
            "¿Quién creó el bot?",
            "¿Quién es el creador?",
            "¿Quién es el que mueve el bot?",
            "¿Quién la creó originalmente?",
            "¿Qué proyecto te creó?",
            "¿Qué modelo es?",
        ):
            update = FakeUpdate(user_id, text=question)
            await secondary._chat(update, SimpleNamespace(bot=FakeBot()))
            response = update.effective_message.calls[0][0]
            assert "@Niku_Blade" in response
            assert "Dark Zone" in response
            assert "NoTrack" not in response
            assert "OpenAI" not in response
            assert "proyecto" not in response.lower()

    try:
        asyncio.run(run())
    finally:
        secondary._JOIN_CONFIRMED.discard(9_870_091)


def test_personality_selector_saves_choice_to_shared_persistent_state(monkeypatch, tmp_path):
    async def run():
        user_id = 9_870_092
        state_file = tmp_path / "bot_state.json"
        monkeypatch.setattr(secondary, "STATE_FILE", state_file)
        secondary._JOIN_CONFIRMED.add(user_id)
        secondary._USERS[user_id] = {}

        menu_query = FakeQuery("secondary:user:persona")
        menu_update = SimpleNamespace(
            callback_query=menu_query,
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(type="private"),
        )
        await secondary._user_callback(menu_update, SimpleNamespace())
        assert "Personalidad de la IA" in menu_query.edited[0][0]
        writer_button = next(
            button
            for row in menu_query.edited[0][1]["reply_markup"].inline_keyboard
            for button in row
            if button.callback_data == "secondary:user:persona:set:writer"
        )
        assert "Escritor" in writer_button.text

        choose_query = FakeQuery(writer_button.callback_data)
        choose_update = SimpleNamespace(
            callback_query=choose_query,
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(type="private"),
        )
        await secondary._user_callback(choose_update, SimpleNamespace())
        assert secondary._USERS[user_id]["persona"] == "writer"
        assert "Escritor" in choose_query.edited[0][0]

        temperature_menu = FakeQuery("secondary:user:temperature")
        temperature_update = SimpleNamespace(
            callback_query=temperature_menu,
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(type="private"),
        )
        await secondary._user_callback(temperature_update, SimpleNamespace())
        creative_button = next(
            button
            for row in temperature_menu.edited[0][1]["reply_markup"].inline_keyboard
            for button in row
            if button.callback_data == "secondary:user:temperature:set:creative"
        )
        assert "0.8" in creative_button.text
        temperature_choice = FakeQuery(creative_button.callback_data)
        temperature_update.callback_query = temperature_choice
        await secondary._user_callback(temperature_update, SimpleNamespace())
        assert secondary._USERS[user_id]["temperature"] == "creative"

        secondary._USERS.pop(user_id, None)
        secondary._load_state()
        assert secondary._USERS[user_id]["persona"] == "writer"
        assert secondary._USERS[user_id]["temperature"] == "creative"

    try:
        asyncio.run(run())
    finally:
        secondary._USERS.pop(9_870_092, None)
        secondary._JOIN_CONFIRMED.discard(9_870_092)


def test_personality_instructions_are_applied_by_ai_backend(monkeypatch):
    monkeypatch.setenv("IASINCENSURA", "sk-notrack-test-key-for-telegram")
    monkeypatch.setattr(web_store, "_AI_TELEGRAM_REQUESTS_BY_USER", {})
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({"choices": [{"message": {"content": "Explicación"}}]}).encode()

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setattr(web_store.urllib.request, "urlopen", fake_urlopen)
    payload, status = web_store.dispatch_telegram_ai_chat(
        9_870_093, [{"role": "user", "content": "Explícame las fracciones"}],
        bypass_quota=True, persona="teacher", temperature_profile="creative",
        response_style="detailed",
    )
    assert status == 200 and payload["ok"]
    assert "Enseña con paciencia" in captured["body"]["messages"][0]["content"]
    assert "más variedad e ideas originales" in captured["body"]["messages"][0]["content"]
    assert "explicación amplia, ordenada y didáctica" in captured["body"]["messages"][0]["content"]
    assert captured["body"]["temperature"] == 0.8


def test_secondary_bot_starts_independently_of_primary_post_init():
    import bot as primary

    source = Path(primary.__file__).read_text(encoding="utf-8")
    post_init = source[source.index("async def post_init("):source.index("def main()")]
    main = source[source.index("def main()") :]
    assert "_launch_secondary_ai_process" not in post_init
    launch_index = main.index("_launch_secondary_ai_process()")
    polling_index = main.index("application.run_polling(")
    assert launch_index < polling_index
    assert "SECONDARY_AI_BOT_STATE_FILE, USERS_DATA_FILE" in source
