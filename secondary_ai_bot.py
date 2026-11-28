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
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from web_store import dispatch_telegram_ai_chat

log = logging.getLogger("secondary-ai-bot")

TOKEN = (
    os.getenv("TELEGRAM_AI_BOT_TOKEN", "").strip()
    or os.getenv("SECONDARY_TELEGRAM_BOT_TOKEN", "").strip()
)
# Canales obligatorios del bot 2; no requieren variables de entorno adicionales.
CHANNEL_ONE = "@Dark_Zone_666"
CHANNEL_TWO = "@darzPrbo"
CHANNEL_ONE_URL = "https://t.me/Dark_Zone_666"
CHANNEL_TWO_URL = "https://t.me/darzPrbo"
MAX_HISTORY = 10
_RATE_LOCK = threading.RLock()
_RECENT = defaultdict(list)
_DAILY = defaultdict(list)
_HISTORY: dict[int, list[dict[str, str]]] = defaultdict(list)


def _join_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if CHANNEL_ONE_URL:
        rows.append([InlineKeyboardButton("📢 Canal oficial", url=CHANNEL_ONE_URL)])
    if CHANNEL_TWO_URL:
        rows.append([InlineKeyboardButton("📢 Canal secundario", url=CHANNEL_TWO_URL)])
    rows.append([InlineKeyboardButton("✅ Ya me uní — verificar", callback_data="secondary:verify")])
    return InlineKeyboardMarkup(rows)


def _join_text(missing: list[str] | None = None) -> str:
    if missing:
        detail = "\n".join(f"• {item}" for item in missing)
        return "Para usar la IA todavía debes unirte a estos canales:\n" + detail + "\n\nDespués pulsa «Ya me uní — verificar»."
    return "Únete a los dos canales y luego pulsa «Ya me uní — verificar» para usar la IA."


async def _membership_status(bot, user_id: int) -> tuple[bool, bool, list[str]]:
    missing: list[str] = []
    failed = False
    for chat_id, label in ((CHANNEL_ONE, "Canal oficial"), (CHANNEL_TWO, "Canal secundario")):
        try:
            member = await asyncio.wait_for(bot.get_chat_member(chat_id=chat_id, user_id=user_id), timeout=8)
            status = getattr(member, "status", "")
            allowed = status in {"creator", "administrator", "member"}
            if status == "restricted":
                allowed = bool(getattr(member, "is_member", False))
            if not allowed:
                missing.append(label)
        except Exception as exc:
            failed = True
            log.warning("No se pudo verificar %s (%s)", chat_id, type(exc).__name__)
    return not missing and not failed, failed, missing


async def _require_access(update: Update, bot=None) -> bool:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or getattr(update.effective_chat, "type", None) != "private":
        return False
    allowed, failed, missing = await _membership_status(bot or update.get_bot(), user.id)
    if failed:
        await message.reply_text("No pude verificar las suscripciones ahora. Asegúrate de que este bot sea administrador en ambos canales e inténtalo de nuevo.", reply_markup=_join_keyboard())
        return False
    if not allowed:
        await message.reply_text(_join_text(missing), reply_markup=_join_keyboard())
        return False
    return True


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
    if not update.effective_message or getattr(update.effective_chat, "type", None) != "private":
        return
    if await _require_access(update, context.bot):
        await update.effective_message.reply_text("✅ Ya tienes acceso. Envíame tu pregunta y responderé con la IA.")


async def _verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    allowed, failed, missing = await _membership_status(context.bot, update.effective_user.id)
    if failed:
        await query.edit_message_text("No pude verificar ahora. Comprueba que el segundo bot sea administrador en ambos canales.", reply_markup=_join_keyboard())
    elif allowed:
        _HISTORY.pop(update.effective_user.id, None)
        await query.edit_message_text("✅ Suscripción confirmada. Ya puedes usar la IA enviando mensajes aquí.")
    else:
        await query.edit_message_text(_join_text(missing), reply_markup=_join_keyboard())


async def _reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        _HISTORY.pop(update.effective_user.id, None)
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
    history = _HISTORY[user.id]
    history.append({"role": "user", "content": prompt})
    history[:] = history[-MAX_HISTORY:]
    try:
        payload, status = await asyncio.to_thread(dispatch_telegram_ai_chat, user.id, list(history))
        if status != 200 or not payload.get("ok"):
            raise RuntimeError(str(payload.get("error", "ai_unavailable")))
        answer = str(payload["reply"]).strip()
    except Exception:
        log.exception("Fallo en IA del segundo bot")
        await message.reply_text("La IA no está disponible en este momento. Inténtalo de nuevo más tarde.")
        return
    history.append({"role": "assistant", "content": answer})
    history[:] = history[-MAX_HISTORY:]
    await message.reply_text(answer[:4096])


def run_secondary_ai_bot() -> None:
    """Ejecuta el polling del bot 2 en su propio proceso."""
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", _start))
    application.add_handler(CommandHandler("reset", _reset))
    application.add_handler(CallbackQueryHandler(_verify, pattern=r"^secondary:verify$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _chat))
    log.info("Segundo bot de IA iniciado; canales requeridos: %s y %s", CHANNEL_ONE, CHANNEL_TWO)
    application.run_polling(
        drop_pending_updates=False,
        bootstrap_retries=-1,
        stop_signals=None,
    )


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
