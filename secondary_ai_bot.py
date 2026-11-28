"""Segundo bot de Telegram para usar AiTod4S3 con suscripción obligatoria.

El token se configura únicamente mediante TELEGRAM_AI_BOT_TOKEN.
Este bot es independiente del bot administrador, pero delega la IA al backend
compartido que ya utiliza la aplicación.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

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
MAX_HISTORY = 10
_RATE_LOCK = threading.RLock()
_RECENT = defaultdict(list)
_DAILY = defaultdict(list)
_HISTORY: dict[tuple[int, int], list[dict[str, str]]] = defaultdict(list)
_JOIN_CONFIRMED: set[int] = set()


def _join_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if CHANNEL_ONE_URL:
        rows.append([InlineKeyboardButton("🔵 Abrir canal oficial", url=CHANNEL_ONE_URL)])
    if CHANNEL_TWO_URL:
        rows.append([InlineKeyboardButton("🟣 Abrir canal secundario", url=CHANNEL_TWO_URL)])
    rows.append([InlineKeyboardButton("✅ Ya me uní · continuar", callback_data="secondary:verify")])
    return InlineKeyboardMarkup(rows)


def _join_text() -> str:
    return (
        "✨ <b>¡Bienvenido a la IA!</b> ✨\n\n"
        "🔹 Si quieres, visita nuestros canales con los botones de abajo.\n"
        "💬 Después pulsa <b>«Ya me uní · continuar»</b> para empezar a chatear.\n\n"
        "<i>Los canales son opcionales; el botón solo abre el acceso al bot.</i>"
    )


async def _require_access(update: Update, bot=None) -> bool:
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat
    if not user or not message or not chat or getattr(chat, "type", None) not in {"private", "group", "supergroup"}:
        return False
    if user.id not in _JOIN_CONFIRMED:
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


def _rate_allowed(user_id: int) -> bool:
    now = time.monotonic()
    try:
        daily_limit = max(1, min(1000, int(os.getenv("AI_APP_DAILY_LIMIT", "80"))))
    except ValueError:
        daily_limit = 80
    with _RATE_LOCK:
        _RECENT[user_id] = [value for value in _RECENT[user_id] if now - value < 60]
        _DAILY[user_id] = [value for value in _DAILY[user_id] if now - value < 86_400]
        if len(_RECENT[user_id]) >= 8 or len(_DAILY[user_id]) >= daily_limit:
            return False
        _RECENT[user_id].append(now)
        _DAILY[user_id].append(now)
        return True


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    if await _require_access(update, context.bot):
        await update.effective_message.reply_text(
            "✅ <b>Ya tienes acceso.</b> Envíame tu pregunta y responderé con la IA.",
            parse_mode=ParseMode.HTML,
        )


async def _verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    # Es una confirmación visual: no se consulta la membresía de Telegram.
    _JOIN_CONFIRMED.add(update.effective_user.id)
    await query.edit_message_text(
        "✅ <b>Acceso listo.</b> Ya puedes usar la IA enviando mensajes aquí.",
        parse_mode=ParseMode.HTML,
    )


async def _reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.effective_chat:
        _HISTORY.pop((update.effective_chat.id, update.effective_user.id), None)
    if update.effective_message:
        await update.effective_message.reply_text("✅ Conversación reiniciada.")


async def _chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_access(update, context.bot):
        return
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not message.text:
        return
    prompt = message.text.strip()
    if not prompt or len(prompt) > 4000:
        await message.reply_text("Escribe un mensaje de hasta 4000 caracteres.")
        return
    if not _rate_allowed(user.id):
        await message.reply_text("Has alcanzado el límite temporal de la IA. Inténtalo más tarde.")
        return
    chat_id = update.effective_chat.id
    history = _HISTORY[(chat_id, user.id)]
    history.append({"role": "user", "content": prompt})
    history[:] = history[-MAX_HISTORY:]
    typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))
    try:
        payload, status = await asyncio.to_thread(dispatch_telegram_ai_chat, user.id, list(history))
        if status != 200 or not payload.get("ok"):
            raise RuntimeError(str(payload.get("error", "ai_unavailable")))
        answer = str(payload["reply"]).strip()
    except Exception:
        log.exception("Fallo en IA del segundo bot")
        await message.reply_text("La IA no está disponible en este momento. Inténtalo de nuevo más tarde.")
        return
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
    history.append({"role": "assistant", "content": answer})
    history[:] = history[-MAX_HISTORY:]
    await message.reply_text(answer[:4096])


def run_secondary_ai_bot() -> None:
    """Ejecuta el polling del bot 2 en su propio proceso."""
    while True:
        try:
            application = Application.builder().token(TOKEN).build()
            application.add_handler(CommandHandler("start", _start))
            application.add_handler(CommandHandler("reset", _reset))
            application.add_handler(CallbackQueryHandler(_verify, pattern=r"^secondary:verify$"))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _chat))
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


def start_secondary_ai_bot() -> subprocess.Popen | None:
    """Arranca el polling del bot 2 en un proceso separado del administrador."""
    if not TOKEN:
        log.info("Segundo bot de IA desactivado: falta TELEGRAM_AI_BOT_TOKEN")
        return None
    primary_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if primary_token and TOKEN == primary_token:
        log.error("Segundo bot desactivado: el token del bot 2 no puede reutilizar TELEGRAM_BOT_TOKEN")
        return None

    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--run-secondary-ai-bot"])
    log.info("Proceso independiente del segundo bot de IA iniciado: pid=%s", process.pid)
    return process


if __name__ == "__main__" and "--run-secondary-ai-bot" in sys.argv:
    run_secondary_ai_bot()
