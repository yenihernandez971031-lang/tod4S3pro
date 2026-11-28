"""Segundo bot de Telegram para usar AiTod4S3 con acceso por confirmación visual.

El token se configura únicamente mediante TELEGRAM_AI_BOT_TOKEN.
Este bot es independiente del bot administrador, pero delega la IA al backend
compartido que ya utiliza la aplicación.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import InvalidToken
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from web_store import dispatch_telegram_ai_chat

log = logging.getLogger("secondary-ai-bot")

def _configured_token() -> str:
    for name in ("TELEGRAM_AI_BOT_TOKEN", "SECONDARY_TELEGRAM_BOT_TOKEN"):
        value = os.getenv(name, "").strip().strip("\"'").strip()
        if value:
            return value
    return ""


TOKEN = _configured_token()
# Enlaces promocionados en el panel visual del bot; no requieren variables nuevas.
CHANNEL_ONE = "@Dark_Zone_666"
CHANNEL_TWO = "@darzPrbo"
CHANNEL_ONE_URL = "https://t.me/Dark_Zone_666"
CHANNEL_TWO_URL = "https://t.me/darzPrbo"
MAX_HISTORY = 6
MAX_STORED_MESSAGES = 20
MAX_STORED_MESSAGE_CHARS = 1_200
MAX_CONTEXT_CHARS = 8_000
QUESTION_LIMIT = 10
RENEWAL_QUESTION_LIMIT = 5
QUESTION_WINDOW_SECONDS = 10 * 60 * 60
MAX_REFERRAL_BONUS = 10
MAX_TRACKED_REFERRALS = 500
AI_PERSONAS = {
    "normal": ("🤖 IA Normal", "Responde con claridad y equilibrio, sin adoptar un rol especial."),
    "writer": ("✍️ Escritor", "Ayuda a crear y mejorar textos; adapta el estilo y conserva la intención del usuario."),
    "programmer": ("💻 Programador", "Explica programación paso a paso y entrega ejemplos completos, ejecutables y seguros."),
    "teacher": ("🧑‍🏫 Profesor", "Enseña de forma paciente, con explicaciones sencillas, ejemplos y preguntas de comprobación."),
    "friend": ("🤝 Amigo", "Usa un tono cálido, respetuoso y alentador; no afirmes ser una persona ni un profesional clínico."),
    "creative": ("🎭 Creativo", "Adopta un tono imaginativo para ideas, historias y juegos de rol claramente ficticios."),
}
AI_TEMPERATURES = {
    "precise": ("🎯 Precisa", 0.2),
    "balanced": ("⚖️ Equilibrada", 0.5),
    "creative": ("✨ Creativa", 0.8),
}
STATE_FILE = Path(os.getenv("SECONDARY_AI_BOT_STATE_FILE", "/data/secondary_ai_bot_state.json"))
_RATE_LOCK = threading.RLock()
_RECENT = defaultdict(list)
_QUESTIONS: dict[int, list[float]] = defaultdict(list)
_RENEWAL_USERS: set[int] = set()
_HISTORY: dict[tuple[int, int], list[dict[str, str]]] = defaultdict(list)
_JOIN_CONFIRMED: set[int] = set()
ADMIN_USER_IDS = {5721542653}
_ADMIN_IDS_LOCK = threading.Lock()
_ADMIN_IDS_LAST_REFRESH = 0.0
_USERS: dict[int, dict[str, Any]] = {}
_BANNED_USERS: set[int] = set()
_AI_ENABLED = True
_RECENT_ERRORS: list[str] = []


def _stored_history(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Bound persisted transcripts so multi-user backups remain practical."""
    clean = []
    for item in items[-MAX_STORED_MESSAGES:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        if len(content) > MAX_STORED_MESSAGE_CHARS:
            content = content[:MAX_STORED_MESSAGE_CHARS] + "\n[texto recortado]"
        clean.append({"role": item["role"], "content": content})
    return clean


def _save_state_locked() -> None:
    data = {
        "questions": {str(user_id): values for user_id, values in _QUESTIONS.items()},
        "renewal_users": sorted(_RENEWAL_USERS),
        "users": {str(user_id): value for user_id, value in _USERS.items()},
        "history": {f"{chat_id}:{user_id}": _stored_history(items) for (chat_id, user_id), items in _HISTORY.items()},
        "banned_users": sorted(_BANNED_USERS),
        "join_confirmed": sorted(_JOIN_CONFIRMED),
        "membership_check_version": 1,
        "ai_enabled": _AI_ENABLED,
    }
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(STATE_FILE)
    except OSError as exc:
        log.warning("No se pudo guardar el estado del bot (%s)", type(exc).__name__)


def _load_state() -> None:
    global _AI_ENABLED
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        with _RATE_LOCK:
            for key, values in data.get("questions", {}).items():
                _QUESTIONS[int(key)] = [float(value) for value in values if isinstance(value, (int, float))]
            renewal_users = data.get("renewal_users")
            if isinstance(renewal_users, list):
                _RENEWAL_USERS.update(
                    int(value) for value in renewal_users
                    if str(value).isdigit() and int(value) > 0
                )
            for key, value in data.get("users", {}).items():
                _USERS[int(key)] = value if isinstance(value, dict) else {}
            if not isinstance(renewal_users, list):
                now = time.time()
                for user_id, values in _QUESTIONS.items():
                    recent = [value for value in values if now - value < QUESTION_WINDOW_SECONDS]
                    if len(recent) >= _initial_question_limit(user_id):
                        _RENEWAL_USERS.add(user_id)
            for key, items in data.get("history", {}).items():
                chat_id, user_id = (int(part) for part in key.split(":", 1))
                if isinstance(items, list):
                    _HISTORY[(chat_id, user_id)] = _stored_history(items)
            _BANNED_USERS.update(int(value) for value in data.get("banned_users", []))
            if data.get("membership_check_version") == 1:
                _JOIN_CONFIRMED.update(int(value) for value in data.get("join_confirmed", []))
            _AI_ENABLED = bool(data.get("ai_enabled", True))
    except FileNotFoundError:
        return
    except (OSError, ValueError, TypeError) as exc:
        log.warning("No se pudo cargar el estado del bot (%s)", type(exc).__name__)


def _refresh_admin_user_ids() -> None:
    """Incluye al dueño y los admins delegados en el bot principal."""
    global _ADMIN_IDS_LAST_REFRESH
    now = time.monotonic()
    if now - _ADMIN_IDS_LAST_REFRESH < 30:
        return
    with _ADMIN_IDS_LOCK:
        now = time.monotonic()
        if now - _ADMIN_IDS_LAST_REFRESH < 30:
            return
        admin_ids = {5721542653}
        for env_name in ("OWNER_TELEGRAM_ID", "SECONDARY_AI_ADMIN_IDS"):
            for raw_id in os.getenv(env_name, "").replace(";", ",").split(","):
                try:
                    admin_id = int(raw_id.strip())
                except ValueError:
                    continue
                if admin_id > 0:
                    admin_ids.add(admin_id)
        admin_file = Path(os.getenv("USERS_DATA_FILE", "/data/users.json"))
        try:
            data = json.loads(admin_file.read_text(encoding="utf-8"))
            raw_admins = data.get("admin_users", {}) if isinstance(data, dict) else {}
            values = raw_admins.keys() if isinstance(raw_admins, dict) else raw_admins if isinstance(raw_admins, list) else []
            for value in values:
                try:
                    admin_id = int(value)
                except (TypeError, ValueError):
                    continue
                if admin_id > 0:
                    admin_ids.add(admin_id)
        except (OSError, ValueError, TypeError):
            pass
        ADMIN_USER_IDS.clear()
        ADMIN_USER_IDS.update(admin_ids)
        _ADMIN_IDS_LAST_REFRESH = now


def _register_user(user, chat) -> None:
    with _RATE_LOCK:
        info = _USERS.setdefault(user.id, {})
        info.update(
            {
                "first_name": str(getattr(user, "first_name", "") or "")[:80],
                "username": str(getattr(user, "username", "") or "")[:80],
                "last_seen": time.time(),
            }
        )
        if getattr(chat, "type", None) == "private":
            info["private_chat_id"] = chat.id
        _save_state_locked()


_load_state()
_refresh_admin_user_ids()


def _join_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if CHANNEL_ONE_URL:
        rows.append([InlineKeyboardButton("📢 Canal oficial", url=CHANNEL_ONE_URL, style="primary")])
    if CHANNEL_TWO_URL:
        rows.append([InlineKeyboardButton("📰 Canal de novedades", url=CHANNEL_TWO_URL, style="success")])
    rows.append([InlineKeyboardButton("✅ Ya me uní · verificar", callback_data="secondary:verify", style="success")])
    return InlineKeyboardMarkup(rows)


def _user_keyboard(referral_share_url: str | None = None) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("💳 Mi saldo", callback_data="secondary:user:quota", style="primary"),
        InlineKeyboardButton("👥 Referidos", callback_data="secondary:user:referrals", style="success"),
    ]]
    rows.append([InlineKeyboardButton("🧠 Personalidad", callback_data="secondary:user:persona", style="primary")])
    rows.append([InlineKeyboardButton("🌡️ Temperatura", callback_data="secondary:user:temperature", style="primary")])
    if referral_share_url:
        rows.append([InlineKeyboardButton("🔗 Invitar", url=referral_share_url, style="primary")])
    rows.append([InlineKeyboardButton("📢 Canal oficial", url=CHANNEL_ONE_URL, style="primary")])
    return InlineKeyboardMarkup(rows)


def _persona_keyboard(current: str) -> InlineKeyboardMarkup:
    rows = []
    options = list(AI_PERSONAS.items())
    for offset in range(0, len(options), 2):
        row = []
        for key, (label, _instruction) in options[offset:offset + 2]:
            prefix = "✅ " if key == current else ""
            row.append(InlineKeyboardButton(
                prefix + label,
                callback_data=f"secondary:user:persona:set:{key}",
                style="success" if key == current else "primary",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Volver al menú", callback_data="secondary:user:quota", style="primary")])
    return InlineKeyboardMarkup(rows)


def _temperature_keyboard(current: str) -> InlineKeyboardMarkup:
    rows = []
    for key, (label, value) in AI_TEMPERATURES.items():
        prefix = "✅ " if key == current else ""
        rows.append([InlineKeyboardButton(
            f"{prefix}{label} · {value:.1f}",
            callback_data=f"secondary:user:temperature:set:{key}",
            style="success" if key == current else "primary",
        )])
    rows.append([InlineKeyboardButton("⬅️ Volver al menú", callback_data="secondary:user:quota", style="primary")])
    return InlineKeyboardMarkup(rows)


def _join_text() -> str:
    return (
        "🤖 <b>AiTod4S3 · Acceso</b>\n\n"
        "Para comenzar, únete a los dos canales y pulsa <b>«Ya me uní · verificar»</b>. "
        "Comprobaré tu acceso y habilitaré el chat.\n\n"
        "👤 Creador: <a href=\"https://t.me/Niku_Blade\">@Niku_Blade</a>\n"
        "📢 Novedades: <a href=\"https://t.me/Dark_Zone_666\">Dark Zone</a>"
    )


async def _require_access(update: Update, bot=None) -> bool:
    _refresh_admin_user_ids()
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat
    if not user or not message or not chat or getattr(chat, "type", None) not in {"private", "group", "supergroup"}:
        return False
    _register_user(user, chat)
    if user.id in _BANNED_USERS:
        await message.reply_text("⛔ Tu acceso a este bot está bloqueado.")
        return False
    if user.id not in _JOIN_CONFIRMED and user.id not in ADMIN_USER_IDS:
        await message.reply_text(_join_text(), reply_markup=_join_keyboard(), parse_mode=ParseMode.HTML)
        return False
    return True


async def _keep_typing(bot, chat_id: int) -> None:
    """Refresh Telegram's short-lived typing indicator while the AI is working."""
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception as exc:
            log.debug("No se pudo mostrar typing en %s (%s)", chat_id, type(exc).__name__)
            return
        await asyncio.sleep(4)


async def _stream_answer_preview(message, chunks: asyncio.Queue) -> None:
    """Muestra fragmentos de la respuesta conforme llegan, con ediciones agrupadas."""
    parts: list[str] = []
    while True:
        chunk = await chunks.get()
        if chunk is None:
            return
        parts.append(chunk)
        await asyncio.sleep(0.8)
        finished = False
        while not chunks.empty():
            extra = chunks.get_nowait()
            if extra is None:
                finished = True
                break
            parts.append(extra)
        preview = "".join(parts)[:3700]
        if preview:
            try:
                await message.edit_text("✍️\n" + preview, parse_mode=None)
            except Exception as exc:
                log.debug("No se pudo actualizar la respuesta parcial (%s)", type(exc).__name__)
        if finished:
            return


def _rate_allowed(user_id: int) -> bool:
    now = time.time()
    with _RATE_LOCK:
        _RECENT[user_id] = [value for value in _RECENT[user_id] if now - value < 60]
        _QUESTIONS[user_id] = [value for value in _QUESTIONS[user_id] if now - value < QUESTION_WINDOW_SECONDS]
        if len(_QUESTIONS[user_id]) >= _question_limit(user_id):
            return False
        _RECENT[user_id].append(now)
        _QUESTIONS[user_id].append(now)
        _save_state_locked()
        return True


def _refund_question(user_id: int) -> None:
    """Devuelve el cupo reservado si la IA no produjo una respuesta."""
    with _RATE_LOCK:
        if _QUESTIONS.get(user_id):
            _QUESTIONS[user_id].pop()
        if _RECENT.get(user_id):
            _RECENT[user_id].pop()
        _save_state_locked()


def _commit_question(user_id: int) -> None:
    """Marca como consumada la transición a cuota renovada tras una respuesta exitosa."""
    with _RATE_LOCK:
        now = time.time()
        values = [value for value in _QUESTIONS.get(user_id, [])
                  if now - value < QUESTION_WINDOW_SECONDS]
        _QUESTIONS[user_id] = values
        if user_id not in _RENEWAL_USERS and len(values) >= _initial_question_limit(user_id):
            _RENEWAL_USERS.add(user_id)
            _save_state_locked()


def _bounded_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Conserva el contexto reciente dentro de los límites del dispatcher."""
    selected: list[dict[str, str]] = []
    total_chars = 0
    for item in reversed(history[-MAX_HISTORY:]):
        role = item.get("role", "user")
        content = str(item.get("content", ""))
        if len(content) > 3_800:
            content = content[:3_800] + "\n[contenido anterior recortado]"
        if total_chars + len(content) > MAX_CONTEXT_CHARS:
            break
        selected.append({"role": role, "content": content})
        total_chars += len(content)
    selected.reverse()
    return selected


def _quota_status(user_id: int) -> tuple[int, float]:
    now = time.time()
    with _RATE_LOCK:
        values = [value for value in _QUESTIONS.get(user_id, []) if now - value < QUESTION_WINDOW_SECONDS]
        _QUESTIONS[user_id] = values
        limit = _question_limit(user_id)
        if len(values) < limit:
            return limit - len(values), 0
        return 0, max(0, QUESTION_WINDOW_SECONDS - (now - min(values)))


def _referral_bonus(user_id: int) -> int:
    try:
        return max(0, min(MAX_REFERRAL_BONUS, int(_USERS.get(user_id, {}).get("referral_bonus", 0))))
    except (TypeError, ValueError):
        return 0


def _initial_question_limit(user_id: int) -> int:
    return QUESTION_LIMIT + _referral_bonus(user_id)


def _question_limit(user_id: int) -> int:
    base = RENEWAL_QUESTION_LIMIT if user_id in _RENEWAL_USERS else QUESTION_LIMIT
    return base + _referral_bonus(user_id)


def _complete_referral(user_id: int) -> bool:
    """Credit one bounded extra question after an invitee confirms access; one reward per account."""
    with _RATE_LOCK:
        invited = _USERS.get(user_id, {})
        referrer_id = invited.get("pending_referrer_id")
        if not isinstance(referrer_id, int) or referrer_id <= 0 or referrer_id == user_id:
            return False
        if invited.get("referral_rewarded"):
            return False
        inviter = _USERS.get(referrer_id)
        if inviter is None:
            return False
        invitees = inviter.setdefault("referrals", [])
        if not isinstance(invitees, list):
            invitees = inviter["referrals"] = []
        if user_id in invitees:
            invited["referral_rewarded"] = True
            return False
        try:
            referral_count = max(len(invitees), int(inviter.get("referrals_count", len(invitees)) or 0))
        except (TypeError, ValueError):
            referral_count = len(invitees)
        invitees.append(user_id)
        inviter["referrals"] = invitees[-MAX_TRACKED_REFERRALS:]
        inviter["referrals_count"] = referral_count + 1
        try:
            current_bonus = max(0, int(inviter.get("referral_bonus", 0) or 0))
        except (TypeError, ValueError):
            current_bonus = 0
        inviter["referral_bonus"] = min(MAX_REFERRAL_BONUS, current_bonus + 1)
        invited["referral_rewarded"] = True
        _save_state_locked()
        return True


def _record_error(error: Exception) -> None:
    detail = re.match(r"[A-Za-z0-9_-]{1,64}:\d{3}", str(error))
    safe_detail = f" · {detail.group(0)}" if detail else ""
    entry = f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} · {type(error).__name__}{safe_detail}"
    _RECENT_ERRORS.append(entry)
    del _RECENT_ERRORS[:-20]


def _format_answer_for_telegram(answer: str) -> str:
    """Convert Markdown fenced code to Telegram HTML pre/code blocks safely."""
    code_pattern = re.compile(r"```([A-Za-z0-9_+#.-]{0,24})\s*\n(.*?)```", re.DOTALL)
    parts: list[str] = []
    cursor = 0
    for match in code_pattern.finditer(answer):
        prose = escape(answer[cursor:match.start()], quote=False)
        prose = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", prose)
        prose = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", prose)
        parts.append(prose)
        language = match.group(1)
        language_attr = f' class="language-{escape(language, quote=False)}"' if language else ""
        parts.append(f"<pre><code{language_attr}>{escape(match.group(2).rstrip(), quote=False)}</code></pre>")
        cursor = match.end()
    tail = escape(answer[cursor:], quote=False)
    tail = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", tail)
    tail = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", tail)
    parts.append(tail)
    return "".join(parts)


def _admin_keyboard() -> InlineKeyboardMarkup:
    toggle_text = "⏸️ Pausar IA" if _AI_ENABLED else "▶️ Activar IA"
    toggle_style = "danger" if _AI_ENABLED else "success"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_text, callback_data="secondary:admin:toggle", style=toggle_style)],
            [
                InlineKeyboardButton("📊 Estadísticas", callback_data="secondary:admin:stats", style="primary"),
                InlineKeyboardButton("👥 Usuarios", callback_data="secondary:admin:users:0", style="primary"),
            ],
            [
                InlineKeyboardButton("💌 Enviar aviso", callback_data="secondary:admin:announce", style="success"),
                InlineKeyboardButton("⚠️ Errores", callback_data="secondary:admin:errors", style="danger"),
            ],
        ]
    )


def _admin_entry_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("⚙️ Administración", callback_data="secondary:admin:open", style="primary")]]
    rows.extend(_user_keyboard().inline_keyboard)
    return InlineKeyboardMarkup(rows)


def _admin_panel_text() -> str:
    now = time.time()
    window_start = now - QUESTION_WINDOW_SECONDS
    minute_ago = now - 60
    with _RATE_LOCK:
        active_users = 0
        window_requests = 0
        recent_requests = 0
        for user_id, values in list(_QUESTIONS.items()):
            valid = [value for value in values if value > window_start]
            _QUESTIONS[user_id] = valid
            if valid:
                active_users += 1
                window_requests += len(valid)
        for user_id, values in list(_RECENT.items()):
            valid = [value for value in values if value > minute_ago]
            _RECENT[user_id] = valid
            recent_requests += len(valid)

    return (
        "⚙️ <b>Administración · AiTod4S3</b>\n\n"
        f"{'🟢' if _AI_ENABLED else '🔴'} <b>IA:</b> {'activa' if _AI_ENABLED else 'pausada'}\n"
        f"👥 <b>Usuarios registrados:</b> {len(_USERS)}\n"
        f"🚫 <b>Usuarios bloqueados:</b> {len(_BANNED_USERS)}\n"
        f"📈 <b>Usuarios con tokens usados en las últimas 10 h:</b> {active_users}\n"
        f"💬 <b>Tokens consumidos en las últimas 10 h:</b> {window_requests}\n"
        f"⚡ <b>Tokens usados en el último minuto:</b> {recent_requests}\n"
        f"🎯 <b>Saldo:</b> {QUESTION_LIMIT} tokens iniciales; después {RENEWAL_QUESTION_LIMIT} por cada ventana móvil de 10 h\n\n"
        "Los tokens extra por referidos se suman al saldo. El límite se cuenta por usuario, también en grupos."
    )


def _admin_stats_text() -> str:
    now = time.time()
    window_start = now - QUESTION_WINDOW_SECONDS
    minute_ago = now - 60
    with _RATE_LOCK:
        active_users = sum(
            1 for values in _QUESTIONS.values()
            if any(value > window_start for value in values)
        )
        window_requests = sum(
            sum(value > window_start for value in values)
            for values in _QUESTIONS.values()
        )
        recent_requests = sum(
            sum(value > minute_ago for value in values)
            for values in _RECENT.values()
        )
    return (
        "📊 <b>Estadísticas · AiTod4S3</b>\n\n"
        f"👥 Usuarios registrados: <b>{len(_USERS)}</b>\n"
        f"🚫 Usuarios bloqueados: <b>{len(_BANNED_USERS)}</b>\n"
        f"🟢 Usuarios que usaron tokens en las últimas 10 h: <b>{active_users}</b>\n"
        f"💬 Tokens consumidos en las últimas 10 h: <b>{window_requests}</b>\n"
        f"⚡ Tokens usados en el último minuto: <b>{recent_requests}</b>\n"
        f"🎯 Saldo individual: <b>{QUESTION_LIMIT} tokens iniciales; luego {RENEWAL_QUESTION_LIMIT} cada 10 h</b>\n"
        f"🤖 Estado de IA: <b>{'activa' if _AI_ENABLED else 'pausada'}</b>"
    )


def _admin_users_page(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    page = max(0, page)
    per_page = 6
    users = sorted(_USERS.items(), key=lambda item: item[1].get("last_seen", 0), reverse=True)
    total_pages = max(1, (len(users) + per_page - 1) // per_page)
    page = min(page, total_pages - 1)
    rows = []
    for user_id, info in users[page * per_page : (page + 1) * per_page]:
        name = info.get("username") or info.get("first_name") or str(user_id)
        state = "🚫" if user_id in _BANNED_USERS else "🟢"
        rows.append([
            InlineKeyboardButton(
                f"{state} {str(name)[:28]} · {user_id}",
                callback_data=f"secondary:admin:user:{user_id}",
                style="danger" if user_id in _BANNED_USERS else "success",
            )
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"secondary:admin:users:{page - 1}", style="primary"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"secondary:admin:users:{page + 1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Menú admin", callback_data="secondary:admin:refresh", style="primary")])
    return f"👥 <b>Usuarios</b> · página {page + 1}/{total_pages}\nTotal: {len(users)}\nSelecciona uno para administrarlo:", InlineKeyboardMarkup(rows)


def _admin_user_detail(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    info = _USERS.get(user_id, {})
    name = escape(str(info.get("first_name") or info.get("username") or "Usuario"), quote=False)
    username = escape(str(info.get("username") or "sin usuario"), quote=False)
    remaining, wait_seconds = _quota_status(user_id)
    minutes = int(wait_seconds // 60)
    text = (
        f"👤 <b>{name}</b> (@{username})\n"
        f"🆔 <code>{user_id}</code>\n"
        f"📊 Tokens disponibles: {remaining}/{_question_limit(user_id)}\n"
        + (f"⏳ Próximo token en aproximadamente {minutes} min\n" if remaining == 0 else "")
        + f"🚦 Estado: {'bloqueado' if user_id in _BANNED_USERS else 'activo'}"
    )
    if user_id in _BANNED_USERS:
        action = InlineKeyboardButton("✅ Desbloquear", callback_data=f"secondary:admin:unban:{user_id}", style="success")
    else:
        action = InlineKeyboardButton("🚫 Bloquear", callback_data=f"secondary:admin:ban:{user_id}", style="danger")
    keyboard = InlineKeyboardMarkup(
        [
            [action],
            [InlineKeyboardButton("💬 Ver historial", callback_data=f"secondary:admin:history:{user_id}", style="primary")],
            [InlineKeyboardButton("🧹 Reiniciar conversación", callback_data=f"secondary:admin:reset:{user_id}", style="primary")],
            [InlineKeyboardButton("⬅️ Lista de usuarios", callback_data="secondary:admin:users:0", style="primary")],
        ]
    )
    return text, keyboard


def _admin_user_history(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    conversations = [
        (chat_id, items) for (chat_id, owner_id), items in _HISTORY.items()
        if owner_id == user_id and items
    ]
    conversations.sort(key=lambda row: row[0] != user_id)
    lines = []
    for chat_id, items in conversations:
        lines.append(f"<b>Chat {chat_id}</b>")
        for item in items[-MAX_STORED_MESSAGES:]:
            role = "Usuario" if item.get("role") == "user" else "IA"
            content = escape(str(item.get("content", "")), quote=False)
            lines.append(f"<b>{role}:</b> {content}")
    if lines:
        selected = []
        length = 0
        for line in reversed(lines):
            if length + len(line) + 1 > 3200:
                break
            selected.append(line)
            length += len(line) + 1
        body = "\n".join(reversed(selected))
    else:
        body = "Todavía no hay mensajes guardados para esta persona."
    return (
        f"💬 <b>Historial reciente</b> · <code>{user_id}</code>\n\n{body}",
        InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver al usuario", callback_data=f"secondary:admin:user:{user_id}", style="primary")]]),
    )


def _admin_errors_text() -> str:
    if not _RECENT_ERRORS:
        return "⚠️ <b>Errores recientes</b>\n\nNo hay errores registrados en esta sesión."
    entries = []
    for item in _RECENT_ERRORS[-10:]:
        safe_item = escape(item, quote=False)
        entries.append(f"• <code>{safe_item}</code>")
        if "ai_fallback_unavailable:503" in item:
            entries.append(
                "🩺 <b>Qué revisar:</b> el backend no tiene una clave <code>IASINCENSURA</code> "
                "válida o el respaldo web de NoTrack no está respondiendo. Comprueba en Railway "
                "que la variable exista y tenga una clave vigente; no publiques ni compartas su valor."
            )
    return "⚠️ <b>Errores recientes</b>\n\n" + "\n".join(entries)


def _admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menú admin", callback_data="secondary:admin:refresh", style="primary")]])


async def _admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _refresh_admin_user_ids()
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message
    if not user or not chat or not message or chat.type != "private":
        return
    if user.id not in ADMIN_USER_IDS:
        await message.reply_text("⛔ No tienes permiso para abrir este panel.")
        return
    await message.reply_text(
        _admin_panel_text(),
        reply_markup=_admin_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def _admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _refresh_admin_user_ids()
    global _AI_ENABLED
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query:
        return
    if not user or user.id not in ADMIN_USER_IDS or not chat or chat.type != "private":
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    data = str(query.data or "")
    action = data.removeprefix("secondary:admin:")
    if action != "announce" and not action.startswith("announce:"):
        context.user_data["admin_awaiting_announcement"] = False
        context.user_data.pop("admin_announcement_draft", None)
    if action in {"open", "refresh"}:
        text, keyboard = _admin_panel_text(), _admin_keyboard()
    elif action == "stats":
        text, keyboard = _admin_stats_text(), _admin_back_keyboard()
    elif action == "toggle":
        with _RATE_LOCK:
            _AI_ENABLED = not _AI_ENABLED
            _save_state_locked()
        text, keyboard = _admin_panel_text(), _admin_keyboard()
    elif action.startswith("users:"):
        try:
            page = int(action.split(":", 1)[1])
        except ValueError:
            page = 0
        text, keyboard = _admin_users_page(page)
    elif action.startswith("history:"):
        try:
            text, keyboard = _admin_user_history(int(action.split(":", 1)[1]))
        except (ValueError, IndexError):
            text, keyboard = "ID de usuario no válido.", _admin_back_keyboard()
    elif action.startswith("user:"):
        try:
            user_id = int(action.split(":", 1)[1])
            text, keyboard = _admin_user_detail(user_id)
        except (ValueError, IndexError):
            text, keyboard = "No se encontró ese usuario.", _admin_back_keyboard()
    elif action.startswith(("ban:", "unban:", "reset:")):
        verb, raw_user_id = action.split(":", 1)
        try:
            user_id = int(raw_user_id)
        except ValueError:
            await query.edit_message_text("ID de usuario no válido.", reply_markup=_admin_back_keyboard())
            return
        if verb in {"ban", "unban"} and user_id in ADMIN_USER_IDS:
            await query.edit_message_text("No puedes cambiar el acceso de un administrador.", reply_markup=_admin_back_keyboard())
            return
        if verb == "ban":
            _BANNED_USERS.add(user_id)
            with _RATE_LOCK:
                _save_state_locked()
        elif verb == "unban":
            _BANNED_USERS.discard(user_id)
            with _RATE_LOCK:
                _save_state_locked()
        else:
            for key in [key for key in _HISTORY if key[1] == user_id]:
                _HISTORY.pop(key, None)
            with _RATE_LOCK:
                _save_state_locked()
        text, keyboard = _admin_user_detail(user_id)
        if verb == "reset":
            text = f"✅ Historial de conversación reiniciado.\n\n{text}"
    elif action == "errors":
        text, keyboard = _admin_errors_text(), _admin_back_keyboard()
    elif action == "announce":
        context.user_data["admin_awaiting_announcement"] = True
        text = "📣 <b>Nuevo aviso</b>\n\nEscribe el texto que quieres enviar. Luego podrás revisarlo y confirmar el envío."
        keyboard = _admin_back_keyboard()
    elif action in {"announce:send", "announce:cancel"}:
        context.user_data["admin_awaiting_announcement"] = False
        if action == "announce:cancel":
            context.user_data.pop("admin_announcement_draft", None)
            text, keyboard = "Envío cancelado.", _admin_keyboard()
        else:
            draft = str(context.user_data.pop("admin_announcement_draft", "")).strip()
            if not draft:
                text, keyboard = "No hay ningún aviso pendiente.", _admin_keyboard()
            else:
                recipients = [
                    int(info["private_chat_id"])
                    for target_id, info in _USERS.items()
                    if target_id not in ADMIN_USER_IDS and info.get("private_chat_id")
                ]
                sent = 0
                failed = 0
                for chat_id in recipients:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=draft)
                        sent += 1
                    except Exception:
                        failed += 1
                    await asyncio.sleep(0.04)
                text = f"📣 Aviso terminado.\n✅ Enviados: {sent}\n⚠️ No entregados: {failed}"
                keyboard = _admin_keyboard()
    else:
        text, keyboard = _admin_panel_text(), _admin_keyboard()
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def _admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat
    if (
        not user or user.id not in ADMIN_USER_IDS or not message or not chat
        or chat.type != "private" or not context.user_data.get("admin_awaiting_announcement")
    ):
        await _chat(update, context)
        return
    draft = str(message.text or "").strip()
    context.user_data["admin_awaiting_announcement"] = False
    if not draft:
        await message.reply_text("El aviso está vacío. Pulsa Enviar aviso para volver a intentarlo.")
        return
    if len(draft) > 3500:
        await message.reply_text("El aviso es demasiado largo. Usa hasta 3500 caracteres.")
        return
    context.user_data["admin_announcement_draft"] = draft
    eligible = sum(
        1 for target_id, info in _USERS.items()
        if target_id not in ADMIN_USER_IDS and info.get("private_chat_id")
    )
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Confirmar envío", callback_data="secondary:admin:announce:send", style="success"),
            InlineKeyboardButton("❌ Cancelar", callback_data="secondary:admin:announce:cancel", style="danger"),
        ]]
    )
    await message.reply_text(
        f"📣 <b>Vista previa del aviso</b> · destinatarios privados: {eligible}\n\n{escape(draft, quote=False)}\n\n¿Confirmas el envío?",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.info("El bot IA recibió /start")
    _refresh_admin_user_ids()
    if not update.effective_message or not update.effective_user or not update.effective_chat:
        return
    _register_user(update.effective_user, update.effective_chat)
    args = getattr(context, "args", []) or []
    if args:
        match = re.fullmatch(r"ref_(\d{1,20})", str(args[0]))
        if match:
            referrer_id = int(match.group(1))
            if referrer_id != update.effective_user.id:
                with _RATE_LOCK:
                    info = _USERS.setdefault(update.effective_user.id, {})
                    if not info.get("referral_rewarded") and not info.get("pending_referrer_id"):
                        info["pending_referrer_id"] = referrer_id
                        _save_state_locked()
    if update.effective_chat.type == "private" and update.effective_user.id in ADMIN_USER_IDS:
        await update.effective_message.reply_text(
            "👋 <b>Hola, administrador.</b> Usa los botones para abrir el panel o ajustar tus preferencias.",
            reply_markup=_admin_entry_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    if await _require_access(update, context.bot):
        rewarded = _complete_referral(update.effective_user.id)
        await update.effective_message.reply_text(
            "✅ <b>Acceso confirmado.</b> Envíame un mensaje y te responderé con la IA."
            + ("\n🎁 Tu invitación fue registrada; el invitador recibió un token extra." if rewarded else ""),
            reply_markup=_user_keyboard() if update.effective_chat.type == "private" else None,
            parse_mode=ParseMode.HTML,
        )


async def _verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    missing = []
    for channel in (CHANNEL_ONE, CHANNEL_TWO):
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=update.effective_user.id)
            status = str(getattr(member, "status", "")).lower()
            is_restricted_member = status == "restricted" and bool(getattr(member, "is_member", False))
            if status not in {"member", "administrator", "creator"} and not is_restricted_member:
                missing.append(channel)
        except Exception as exc:
            log.warning("No se pudo verificar canal %s (%s)", channel, type(exc).__name__)
            missing.append(channel)
    if missing:
        await query.edit_message_text(
            "⚠️ <b>No pude comprobar tu acceso todavía.</b>\n\n"
            "Confirma que te uniste a los dos canales y vuelve a tocar «Ya me uní · continuar». "
            "Si ya estás dentro, espera un momento y prueba otra vez.",
            reply_markup=_join_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    with _RATE_LOCK:
        _JOIN_CONFIRMED.add(update.effective_user.id)
        _save_state_locked()
    rewarded = _complete_referral(update.effective_user.id)
    await query.edit_message_text(
            "✅ <b>Acceso confirmado.</b> Ya puedes escribir aquí; cada mensaje usa tokens."
        + ("\n🎁 La invitación quedó confirmada; tu invitador recibió un token extra." if rewarded else ""),
        reply_markup=(
            _user_keyboard()
            if getattr(getattr(update, "effective_chat", None), "type", None) == "private"
            else None
        ),
        parse_mode=ParseMode.HTML,
    )


async def _reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.effective_chat:
        with _RATE_LOCK:
            _HISTORY.pop((update.effective_chat.id, update.effective_user.id), None)
            _save_state_locked()
    if update.effective_message:
        await update.effective_message.reply_text("✅ Conversación reiniciada.")


def _quota_text(user_id: int) -> str:
    remaining, wait_seconds = _quota_status(user_id)
    phase = (
        f"Renovación: <b>{RENEWAL_QUESTION_LIMIT} tokens</b> cada 10 horas"
        if user_id in _RENEWAL_USERS
        else f"Saldo inicial: <b>{QUESTION_LIMIT} tokens</b>; después, <b>{RENEWAL_QUESTION_LIMIT} tokens</b> cada 10 horas"
    )
    bonus = _referral_bonus(user_id)
    if bonus:
        phase += f" + {bonus} extra{'' if bonus == 1 else 's'} por referidos"
    text = (
        "💳 <b>Saldo de tokens</b>\n\n"
        f"🎟️ Te quedan <b>{remaining}/{_question_limit(user_id)} tokens</b>.\n"
        f"↻ {phase}.\n<i>Un token permite enviar un mensaje a la IA.</i>"
    )
    if remaining == 0:
        minutes = int(wait_seconds // 60)
        text += f"\n⏳ Próximo token disponible en aproximadamente {minutes // 60} h {minutes % 60} min."
    return text


async def _referral_view(user_id: int, bot) -> tuple[str, InlineKeyboardMarkup]:
    info = _USERS.get(user_id, {})
    invitees = info.get("referrals", [])
    try:
        count = max(0, int(info.get("referrals_count", len(invitees) if isinstance(invitees, list) else 0) or 0))
    except (TypeError, ValueError):
        count = len(invitees) if isinstance(invitees, list) else 0
    try:
        bot_info = await bot.get_me()
        username = str(getattr(bot_info, "username", "") or "").lstrip("@")
    except Exception:
        username = ""
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        return "No pude generar tu enlace ahora. Inténtalo de nuevo más tarde.", _user_keyboard()
    bonus = _referral_bonus(user_id)
    link = f"https://t.me/{username}?start=ref_{user_id}"
    share_url = "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote("¡Únete al bot de IA!", safe="")
    text = (
        "👥 <b>Programa de referidos</b>\n\n"
        f"📈 Invitaciones confirmadas: <b>{count}</b>\n"
        f"🎟️ Tokens extra: <b>{bonus}/{MAX_REFERRAL_BONUS}</b>\n\n"
        "Cada amistad que se una a los dos canales y confirme su acceso te regala "
        "<b>1 token adicional</b> en tus ventanas de 10 horas, hasta un máximo de 10.\n\n"
        f"🔗 <b>Tu enlace personal</b>\n<code>{escape(link)}</code>\n\n"
        "Usa el botón <b>Invitar</b> para compartirlo desde Telegram."
    )
    return text, _user_keyboard(share_url)


async def _quota_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context.bot):
        return
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Abre el bot en privado para consultar tu cuota.")
        return
    await update.effective_message.reply_text(
        _quota_text(update.effective_user.id),
        reply_markup=_user_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def _user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query:
        return
    if not user or not chat or chat.type != "private" or user.id in _BANNED_USERS:
        await query.answer("No disponible.", show_alert=True)
        return
    if user.id not in _JOIN_CONFIRMED and user.id not in ADMIN_USER_IDS:
        await query.answer("Primero verifica tu acceso en los dos canales con /start.", show_alert=True)
        return
    action = str(query.data or "").removeprefix("secondary:user:")
    persona_key = action.removeprefix("persona:set:") if action.startswith("persona:set:") else None
    temperature_key = action.removeprefix("temperature:set:") if action.startswith("temperature:set:") else None
    if (action not in {"quota", "referrals", "persona", "temperature"}
            and persona_key not in AI_PERSONAS and temperature_key not in AI_TEMPERATURES):
        await query.answer("Opción no válida.", show_alert=True)
        return
    await query.answer()
    if temperature_key:
        with _RATE_LOCK:
            info = _USERS.setdefault(user.id, {})
            info["temperature"] = temperature_key
            _save_state_locked()
        label, value = AI_TEMPERATURES[temperature_key]
        text = f"🌡️ Temperatura actualizada: <b>{escape(label)} · {value:.1f}</b>. Se aplicará a tus próximas preguntas."
        keyboard = _temperature_keyboard(temperature_key)
    elif action == "temperature":
        current = str(_USERS.get(user.id, {}).get("temperature", "balanced"))
        if current not in AI_TEMPERATURES:
            current = "balanced"
        label, value = AI_TEMPERATURES[current]
        text = (
            "🌡️ <b>Temperatura de respuesta</b>\n\n"
            f"Actual: <b>{escape(label)} · {value:.1f}</b>\n"
            "Más baja = respuestas más precisas; más alta = más variedad e ideas creativas."
        )
        keyboard = _temperature_keyboard(current)
    elif persona_key:
        with _RATE_LOCK:
            info = _USERS.setdefault(user.id, {})
            info["persona"] = persona_key
            _save_state_locked()
        label = AI_PERSONAS[persona_key][0]
        text = f"✅ Personalidad actualizada a <b>{escape(label)}</b>. Se aplicará a tus próximos mensajes."
        keyboard = _persona_keyboard(persona_key)
    elif action == "persona":
        current = str(_USERS.get(user.id, {}).get("persona", "normal"))
        if current not in AI_PERSONAS:
            current = "normal"
        text = (
            "🧠 <b>Personalidad de la IA</b>\n\n"
            f"Modo actual: <b>{escape(AI_PERSONAS[current][0])}</b>\n"
            "Hay seis opciones breves; la elección se guarda para próximos despliegues."
        )
        keyboard = _persona_keyboard(current)
    elif action == "quota":
        text, keyboard = _quota_text(user.id), _user_keyboard()
    else:
        text, keyboard = await _referral_view(user.id, context.bot)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def _referrals_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context.bot):
        return
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Abre el bot en privado para consultar y compartir tus referidos.")
        return
    text, keyboard = await _referral_view(update.effective_user.id, context.bot)
    await update.effective_message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


def _identity_reply(prompt: str) -> str | None:
    """Da una respuesta estable sobre el creador sin exponer detalles internos del proveedor."""
    if not re.search(
        r"\b(quien|quién)\b.*\b(cre[oó]|hizo|desarroll[oó]|program[oó]|maneja|administra|gestiona|mueve|dirige|est[aá] detr[aá]s)\b"
        r"|\b(quien|quién)\b.*\b(es|fue)\b.*\b(creador|creadora|dueñ[oa]|responsable|autor[oa])\b"
        r"|\b(modelo|proveedor|api interna|motor de ia|inteligencia artificial)\b",
        prompt,
        flags=re.IGNORECASE,
    ):
        return None
    return (
        "🤖 <b>AiTod4S3</b>\n\n"
        "Creado y administrado por <a href=\"https://t.me/Niku_Blade\">@Niku_Blade</a>.\n"
        "Novedades: <a href=\"https://t.me/Dark_Zone_666\">Dark Zone</a>.\n\n"
        "Por privacidad, no comparto detalles técnicos internos del servicio de IA."
    )


async def _chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context.bot):
        return
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not message.text:
        return
    if not _AI_ENABLED:
        await message.reply_text("⏸️ La IA está pausada temporalmente por el administrador. Vuelve a intentarlo más tarde.")
        return
    prompt = message.text.strip()
    if not prompt or len(prompt) > 4000:
        await message.reply_text("Escribe un mensaje de hasta 4000 caracteres.")
        return
    about_reply = _identity_reply(prompt)
    if about_reply:
        await message.reply_text(
            about_reply,
            reply_markup=_user_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    is_admin = user.id in ADMIN_USER_IDS
    if not is_admin and not _rate_allowed(user.id):
        _, wait_seconds = _quota_status(user.id)
        hours, minutes = divmod(int(wait_seconds // 60), 60)
        await message.reply_text(
            f"🌙 Ya usaste tus {_question_limit(user.id)} tokens de esta ventana. "
            f"Tu próximo token se libera en aproximadamente {hours} h {minutes} min."
        )
        return
    chat_id = update.effective_chat.id
    history = _HISTORY[(chat_id, user.id)]
    history.append({"role": "user", "content": prompt})
    history[:] = history[-MAX_STORED_MESSAGES:]
    typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))
    progress_message = None
    preview_queue: asyncio.Queue = asyncio.Queue()
    preview_task = None
    loop = asyncio.get_running_loop()

    def enqueue_delta(chunk: str) -> None:
        if chunk:
            loop.call_soon_threadsafe(preview_queue.put_nowait, chunk)

    try:
        progress_message = await message.reply_text("🧠 La IA está trabajando en tu respuesta…")
        preview_task = asyncio.create_task(_stream_answer_preview(progress_message, preview_queue))
        payload, status = await asyncio.to_thread(
            dispatch_telegram_ai_chat, user.id, _bounded_history(history), bypass_quota=is_admin,
            question_limit=_question_limit(user.id),
            persona=str(_USERS.get(user.id, {}).get("persona", "normal")),
            temperature_profile=str(_USERS.get(user.id, {}).get("temperature", "balanced")),
            on_delta=enqueue_delta,
        )
        if status != 200 or not payload.get("ok"):
            error_code = str(payload.get("error", "ai_unavailable"))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", error_code):
                error_code = "ai_unavailable"
            raise RuntimeError(f"{error_code}:{status}")
        answer = str(payload["reply"]).strip()
    except Exception as exc:
        if preview_task is not None:
            await asyncio.sleep(0)
            preview_queue.put_nowait(None)
            await preview_task
            preview_task = None
        log.warning("Fallo en IA del segundo bot (%s)", type(exc).__name__)
        _record_error(exc)
        if not is_admin:
            _refund_question(user.id)
        if history and history[-1].get("role") == "user" and history[-1].get("content") == prompt:
            history.pop()
        error_text = "⚠️ La IA no está disponible ahora. Inténtalo de nuevo más tarde."
        if progress_message:
            await progress_message.edit_text(error_text)
        else:
            await message.reply_text(error_text)
        return
    finally:
        if preview_task is not None:
            await asyncio.sleep(0)
            preview_queue.put_nowait(None)
            await preview_task
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
    history.append({"role": "assistant", "content": answer})
    history[:] = history[-MAX_STORED_MESSAGES:]
    with _RATE_LOCK:
        _save_state_locked()
    response_text = _format_answer_for_telegram(answer[:3900])
    if not is_admin:
        _commit_question(user.id)
        remaining, _ = _quota_status(user.id)
        response_text += (
            f"\n\n<i>🎟️ Te quedan {remaining}/{_question_limit(user.id)} tokens. "
            f"Al agotar los {QUESTION_LIMIT} tokens iniciales, tendrás {RENEWAL_QUESTION_LIMIT} tokens por cada ventana de 10 h.</i>"
        )
    await progress_message.edit_text(
        response_text,
        reply_markup=None if is_admin else _user_keyboard(),
        parse_mode=ParseMode.HTML,
    )


def _build_secondary_application() -> Application:
    """Permite que comandos como /start no esperen una consulta de IA lenta."""
    return Application.builder().token(TOKEN).concurrent_updates(16).build()


def run_secondary_ai_bot() -> None:
    """Ejecuta el polling del bot 2 en su propio proceso."""
    while True:
        try:
            application = _build_secondary_application()
            application.add_handler(CommandHandler("admin", _admin))
            application.add_handler(CommandHandler("start", _start))
            application.add_handler(CommandHandler("reset", _reset))
            application.add_handler(CommandHandler(["cuota", "preguntas"], _quota_command))
            application.add_handler(CommandHandler(["referidos", "invitar"], _referrals_command))
            application.add_handler(CallbackQueryHandler(_admin_callback, pattern=r"^secondary:admin:"))
            application.add_handler(CallbackQueryHandler(_user_callback, pattern=r"^secondary:user:"))
            application.add_handler(CallbackQueryHandler(_verify, pattern=r"^secondary:verify$"))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _admin_text))
            log.info("Segundo bot de IA iniciado; canales promocionados: %s y %s", CHANNEL_ONE, CHANNEL_TWO)
            application.run_polling(
                drop_pending_updates=False,
                bootstrap_retries=-1,
                stop_signals=None,
            )
            return
        except InvalidToken:
            log.error("Telegram rechazó el token del bot 2. Revisa TELEGRAM_AI_BOT_TOKEN en Railway.")
            return
        except Exception:
            log.exception("Fallo temporal del segundo bot; se reintentará en 10 segundos")
            time.sleep(10)


def start_secondary_ai_bot(
    state_file: str | Path | None = None,
    admin_file: str | Path | None = None,
) -> subprocess.Popen | None:
    """Arranca el polling del bot 2 en un proceso separado del administrador."""
    if not TOKEN:
        log.info("Segundo bot de IA desactivado: falta TELEGRAM_AI_BOT_TOKEN")
        return None
    primary_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if primary_token and TOKEN == primary_token:
        log.error("Segundo bot desactivado: el token del bot 2 no puede reutilizar TELEGRAM_BOT_TOKEN")
        return None

    child_env = os.environ.copy()
    if state_file is not None:
        child_env["SECONDARY_AI_BOT_STATE_FILE"] = str(state_file)
    if admin_file is not None:
        child_env["USERS_DATA_FILE"] = str(admin_file)
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--run-secondary-ai-bot"],
        env=child_env,
    )
    log.info("Proceso independiente del segundo bot de IA iniciado: pid=%s", process.pid)
    return process


if __name__ == "__main__" and "--run-secondary-ai-bot" in sys.argv:
    run_secondary_ai_bot()
