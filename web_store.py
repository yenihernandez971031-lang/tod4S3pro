"""Servidor estático de la tienda ToDus S3 para el dominio generado por Railway."""
from __future__ import annotations

import mimetypes
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore, Lock, Thread
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit
from app_config import public_site_origin
import sync_api
import todus_s3_storage
import ai_auth
from sync_api import consume_pairing_code, deliveries_for_token, revoke_token
from todus_s3_storage import delete_from_todus_s3

WEB_ROOT = Path(__file__).resolve().parent / "web"
SITE_STARTED_AT = time.time()
_VISITOR_LOCK = Lock()
_VISITOR_LAST_SEEN: dict[str, float] = {}
_TOTAL_VISITS = 0
_MAX_VISITORS = 5000
_VISITOR_RE = re.compile(r"^[A-Za-z0-9-]{8,96}$")
_DEFAULT_ORIGIN = public_site_origin()
_PROFILE_CHECKER: Callable[[int], dict] | None = None
_PROFILE_PHOTO_CHECKER: Callable[[int], bytes | None] | None = None
_APP_TASK_BRIDGE: Callable[[str, int, dict], tuple[dict, int]] | None = None
_APP_BACKUP_NOTIFIER: Callable[[str], None] | None = None
_APP_UPDATE_BAN_NOTIFIER: Callable[[dict], None] | None = None
_APP_TASK_BRIDGE_LOCK = Lock()
_AI_RATE_LOCK = Lock()
_AI_REQUESTS_BY_USER: dict[int, list[float]] = {}
_AI_DAILY_REQUESTS_BY_USER: dict[int, list[float]] = {}
_AI_TELEGRAM_REQUESTS_BY_USER: dict[int, list[float]] = {}
_AI_DAILY_GLOBAL_REQUESTS: list[float] = []
_NIKUDARIA_CLIENT_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NIKUDARIA_RECENT_REQUESTS: dict[str, list[float]] = {}
_NIKUDARIA_DAILY_REQUESTS: dict[str, list[float]] = {}
_AI_GATEWAY_SEMAPHORE = BoundedSemaphore(2)
_NOTRACK_WEB_LOCK = Lock()
_NOTRACK_WEB_UA = "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/130 Mobile Safari/537.36"
_NOTRACK_WEB_SESSION_FILE = os.getenv("NOTRACK_WEB_SESSION_FILE", "/data/notrack_web_session.json")
_AI_ACCOUNT_RATE_LOCK = Lock()
_AI_ACCOUNT_REQUESTS: dict[str, list[float]] = {}
_AI_ACCOUNT_DAILY_REQUESTS: dict[str, list[float]] = {}
_AI_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_TELEGRAM_AI_PERSONA_INSTRUCTIONS = {
    "writer": "Prioriza la escritura creativa y la edición de textos, respetando la intención del usuario.",
    "programmer": "Ayuda con programación paso a paso y da ejemplos completos, ejecutables y seguros.",
    "teacher": "Enseña con paciencia mediante explicaciones sencillas, ejemplos y comprobaciones de comprensión.",
    "friend": "Usa un tono cálido, respetuoso y alentador; deja claro que eres una IA y no un profesional clínico.",
    "creative": "Usa imaginación para ideas, historias y juegos de rol claramente ficticios.",
}
_TELEGRAM_AI_TEMPERATURES = {
    "precise": (0.2, "Mantén una creatividad baja y prioriza respuestas concretas y consistentes."),
    "balanced": (0.5, "Usa un equilibrio normal entre precisión y variedad."),
    "creative": (0.8, "Puedes proponer más variedad e ideas originales, sin inventar hechos."),
}


def _notrack_web_session_cookie() -> str:
    """Obtiene o conserva la cookie uid usada por el chat web anónimo de NoTrack."""
    path = Path(_NOTRACK_WEB_SESSION_FILE)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        cookie = str(saved.get("uid", "")).strip() if isinstance(saved, dict) else ""
        if cookie:
            return cookie
    except (OSError, ValueError, TypeError):
        pass
    request = urllib.request.Request(
        "https://notrack.ai/es/chat",
        headers={"User-Agent": _NOTRACK_WEB_UA, "Accept": "text/html"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        cookies = response.headers.get_all("Set-Cookie") or []
    cookie = ""
    for header in cookies:
        match = re.search(r"(?:^|[,;\s])uid=([^;,\s]+)", header)
        if match:
            cookie = match.group(1)
            break
    if not cookie:
        raise RuntimeError("notrack_web_cookie_missing")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps({"uid": cookie}), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    except OSError:
        pass
    return cookie


def _notrack_web_chat(
    prompt: str,
    timeout_seconds: int = 45,
    *,
    on_delta: Callable[[str], None] | None = None,
) -> str:
    """Usa el chat web anónimo de NoTrack como respaldo sin exponer una clave API."""
    with _NOTRACK_WEB_LOCK:
        cookie = _notrack_web_session_cookie()
        request_body = json.dumps({
            "user_input": prompt[:16000], "mode": "usual", "model": "C",
            "persona": "normal", "max_turns": 3, "chat_id": None,
            "attachments": [], "regenerate": False, "edit": False,
            "edit_mid": None, "via": "typed",
        }, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            "https://notrack.ai/api/dispatch", data=request_body,
            headers={
                "User-Agent": _NOTRACK_WEB_UA, "Accept": "text/event-stream",
                "Origin": "https://notrack.ai", "Referer": "https://notrack.ai/es/chat",
                "Cookie": f"uid={cookie}", "Content-Type": "application/json",
            }, method="POST",
        )
        chunks: list[str] = []
        final = ""
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if "event-stream" not in response.headers.get("Content-Type", ""):
                raise RuntimeError("notrack_web_invalid_stream")
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except (TypeError, ValueError):
                    continue
                event_type = event.get("type")
                if event_type == "delta":
                    chunk = str(event.get("chunk") or "")
                    chunks.append(chunk)
                    if chunk and on_delta is not None:
                        on_delta(chunk)
                elif event_type in {"message", "consensus"} and event.get("content"):
                    final = str(event["content"])
                elif event_type == "error":
                    raise RuntimeError(str(event.get("code") or "notrack_web_error"))
                if event_type in {"done", "message", "consensus", "error"}:
                    break
        answer = (final or "".join(chunks)).strip()
        if not answer:
            raise RuntimeError("notrack_web_empty_response")
        return answer


def _compress_app_upload(source: Path, filename: str) -> tuple[Path, str]:
    """Comprime una subida móvil y devuelve un archivo individual, nunca un ZIP."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "archivo.bin").name).strip("._") or "archivo"
    suffix = Path(safe).suffix.lower()
    stem = Path(safe).stem or "archivo"
    video = suffix in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".3gp"}
    audio = suffix in {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac"}
    image = suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
    if video:
        output = source.with_name(f"{stem}_comprimido.mp4")
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                   "-map", "0:v:0", "-map", "0:a?", "-vf", "scale='min(1280,iw)':-2",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "35", "-c:a", "aac", "-b:a", "64k", str(output)]
    elif audio:
        output = source.with_name(f"{stem}_comprimido.mp3")
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                   "-vn", "-c:a", "libmp3lame", "-b:a", "48k", str(output)]
    elif image:
        output = source.with_name(f"{stem}_comprimido.jpg")
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                   "-frames:v", "1", "-q:v", "8", str(output)]
    else:
        # Los documentos no tienen una compresión universal segura; se entregan normales.
        return source, safe
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=1800)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        output.unlink(missing_ok=True)
        raise RuntimeError("No se pudo comprimir el archivo") from exc
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("No se pudo crear el archivo comprimido")
    if output.stat().st_size >= source.stat().st_size:
        output.unlink(missing_ok=True)
        return source, safe
    return output, output.name


def _dispatch_ai_chat(token: str, body: dict) -> tuple[dict, int]:
    """Proxy text-only AI chat for currently verified app Premium users."""
    identity, error, status = _authorize_app_token(token)
    if error:
        return error, status
    user_id = int(identity["user_id"])
    checker = _PROFILE_CHECKER
    if checker is None:
        return {"ok": False, "error": "profile_unavailable"}, 503
    try:
        profile = checker(user_id)
        if not isinstance(profile, dict):
            raise ValueError("invalid_profile")
        plan = str(profile.get("plan", ""))
        premium = profile.get("premium") is True
        verified = plan == "owner" or (
            premium and sync_api.premium_activation_status(
                user_id, str(identity.get("device_id", "")), profile.get("expires_at", ""),
            ) == "verified"
        )
    except Exception:
        return {"ok": False, "error": "profile_unavailable"}, 503
    if not verified or (plan != "owner" and (not premium or plan != "premium")):
        return {"ok": False, "error": "premium_required"}, 403

    api_key = os.getenv("IASINCENSURA", "").strip()
    api_key_valid = (api_key.startswith("sk-notrack-") and len(api_key) >= 24
                     and not any(char.isspace() for char in api_key))

    mode = str(body.get("mode", "fast") or "fast").strip().lower()
    deep_thinking = bool(body.get("deep_thinking")) or mode in {"deep", "think", "thinking"}
    web_search = bool(body.get("web_search"))
    attachments = _validated_ai_attachments(body)
    if attachments is None:
        return {"ok": False, "error": "invalid_attachments"}, 400
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
        return {"ok": False, "error": "invalid_messages"}, 400
    clean_messages: list[dict[str, str]] = []
    total_chars = 0
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            return {"ok": False, "error": "invalid_messages"}, 400
        content = item.get("content")
        if not isinstance(content, str):
            return {"ok": False, "error": "invalid_messages"}, 400
        content = content.strip()
        if not content or len(content) > 4000:
            return {"ok": False, "error": "invalid_messages"}, 400
        total_chars += len(content)
        if total_chars > 16000:
            return {"ok": False, "error": "conversation_too_long"}, 413
        clean_messages.append({"role": item["role"], "content": content})
    if attachments:
        note = "\nAdjuntos recibidos: " + ", ".join(f"{item['name']} ({item['mime']})" for item in attachments)
        clean_messages[-1]["content"] = (clean_messages[-1]["content"] + note)[:4000]
    if clean_messages[-1]["role"] != "user":
        return {"ok": False, "error": "invalid_messages"}, 400

    now = time.monotonic()
    with _AI_RATE_LOCK:
        recent = [seen for seen in _AI_REQUESTS_BY_USER.get(user_id, []) if now - seen < 60]
        if len(recent) >= 8:
            return {"ok": False, "error": "ai_rate_limited"}, 429
        try:
            daily_limit = max(1, min(100_000, int(os.getenv("IASINCENSURA_USER_DAILY_LIMIT", "80"))))
        except ValueError:
            daily_limit = 80
        daily_user = [seen for seen in _AI_DAILY_REQUESTS_BY_USER.get(user_id, []) if now - seen < 86_400]
        if len(daily_user) >= daily_limit:
            return {"ok": False, "error": "ai_daily_limit"}, 429
        try:
            global_limit = max(1, min(100_000, int(os.getenv("IASINCENSURA_DAILY_REQUEST_LIMIT", "1000"))))
        except ValueError:
            global_limit = 1000
        daily_global = [seen for seen in _AI_DAILY_GLOBAL_REQUESTS if now - seen < 86_400]
        if len(daily_global) >= global_limit:
            return {"ok": False, "error": "ai_service_daily_limit"}, 429
        recent.append(now)
        daily_user.append(now)
        daily_global.append(now)
        _AI_REQUESTS_BY_USER[user_id] = recent
        _AI_DAILY_REQUESTS_BY_USER[user_id] = daily_user
        _AI_DAILY_GLOBAL_REQUESTS[:] = daily_global
        if len(_AI_REQUESTS_BY_USER) > 5000:
            oldest_user = min(
                (key for key in _AI_DAILY_REQUESTS_BY_USER if key != user_id),
                key=lambda key: max(_AI_DAILY_REQUESTS_BY_USER[key], default=now),
                default=None,
            )
            if oldest_user is not None:
                _AI_DAILY_REQUESTS_BY_USER.pop(oldest_user, None)
                _AI_REQUESTS_BY_USER.pop(oldest_user, None)
            _AI_DAILY_REQUESTS_BY_USER[user_id] = daily_user

    system_prompt = (
        "Eres AiTod4S3. Responde en el idioma del usuario, con claridad, precisión y brevedad. "
        "No inventes datos ni afirmes acceso a teléfonos, cuentas o información en tiempo real. "
        "Nunca pidas contraseñas, códigos, PIN, tokens ni datos de pago. "
        "Para soporte de Tod4S3, da pasos cortos, seguros y reversibles; pide versión y error exacto si faltan datos. "
        "No reveles credenciales, configuración privada ni detalles internos del servidor. "
        + ("Razona con más profundidad. " if deep_thinking else "Ve directo al punto. ")
        + ("Si hay fuentes web disponibles, cita enlaces. " if web_search else "")
    )
    request_messages = [{"role": "system", "content": system_prompt}, *clean_messages]
    if not api_key_valid:
        fallback_prompt = "\n\n".join(
            f"{item['role'].upper()}: {item['content']}" for item in request_messages
        )
        try:
            answer = _notrack_web_chat(fallback_prompt)
        except (TimeoutError, urllib.error.URLError, OSError, RuntimeError, ValueError):
            return {"ok": False, "error": "ai_fallback_unavailable"}, 503
        return {"ok": True, "reply": answer}, 200
    request_body = json.dumps({
        "model": "notrack-uncensored",
        "messages": request_messages,
        "max_tokens": 512 if deep_thinking else 320,
        "temperature": 0.5 if deep_thinking else 0.4,
        "stream": False,
    }, separators=(",", ":")).encode("utf-8")
    outgoing = urllib.request.Request(
        "https://api.notrack.ai/v1/chat/completions",
        data=request_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    if not _AI_GATEWAY_SEMAPHORE.acquire(blocking=False):
        return {"ok": False, "error": "ai_busy"}, 429
    try:
        with urllib.request.urlopen(outgoing, timeout=60 if deep_thinking else 35) as response:
            provider_data = json.loads(response.read(1_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        provider_error_type = ""
        try:
            error_payload = json.loads(exc.read(8192).decode("utf-8"))
            nested_error = error_payload.get("error", {}) if isinstance(error_payload, dict) else {}
            if isinstance(nested_error, dict):
                provider_error_type = str(nested_error.get("type", ""))[:64]
        except (UnicodeDecodeError, ValueError, OSError):
            pass
        provider_error = {
            401: "ai_provider_auth",
            429: "ai_busy", 404: "ai_provider_endpoint",
            400: "ai_provider_request", 403: "ai_provider_access",
            503: "ai_unavailable",
        }.get(exc.code, "ai_provider_error" if exc.code < 500 else "ai_unavailable")
        if exc.code == 402:
            provider_error = {
                "no_credit": "ai_credits_exhausted",
                "key_daily_cap": "ai_provider_daily_cap",
            }.get(provider_error_type, "ai_payment_required")
        status = 429 if exc.code == 429 else 502 if exc.code < 500 else 503
        return {"ok": False, "error": provider_error}, status
    except (TimeoutError, urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return {"ok": False, "error": "ai_unavailable"}, 502
    finally:
        _AI_GATEWAY_SEMAPHORE.release()

    try:
        answer = provider_data["choices"][0]["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("empty_answer")
    except (KeyError, IndexError, TypeError, ValueError):
        return {"ok": False, "error": "ai_invalid_response"}, 502
    # Do not persist or log prompts, replies, provider response bodies, or the API key.
    return {"ok": True, "reply": answer.strip()}, 200


def dispatch_telegram_ai_chat(
    user_id: int,
    messages: list[dict[str, str]],
    *,
    bypass_quota: bool = False,
    question_limit: int = 10,
    persona: str = "normal",
    temperature_profile: str = "balanced",
    on_delta: Callable[[str], None] | None = None,
) -> tuple[dict, int]:
    """Usa el proveedor de IA compartido; el bot secundario no recibe credenciales."""
    if not isinstance(user_id, int) or user_id <= 0:
        return {"ok": False, "error": "invalid_user"}, 400
    if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
        return {"ok": False, "error": "invalid_messages"}, 400
    if isinstance(question_limit, bool) or not isinstance(question_limit, int) or not 1 <= question_limit <= 20:
        return {"ok": False, "error": "invalid_question_limit"}, 400
    clean: list[dict[str, str]] = []
    total = 0
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            return {"ok": False, "error": "invalid_messages"}, 400
        content = item.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 4000:
            return {"ok": False, "error": "invalid_messages"}, 400
        content = content.strip()
        total += len(content)
        if total > 16000:
            return {"ok": False, "error": "conversation_too_long"}, 413
        clean.append({"role": item["role"], "content": content})
    if clean[-1]["role"] != "user":
        return {"ok": False, "error": "invalid_messages"}, 400

    reservation = None
    if not bypass_quota:
        now = time.monotonic()
        with _AI_RATE_LOCK:
            window = [seen for seen in _AI_TELEGRAM_REQUESTS_BY_USER.get(user_id, []) if now - seen < 10 * 60 * 60]
            if len(window) >= question_limit:
                return {"ok": False, "error": "ai_10_hour_limit"}, 429
            window.append(now)
            _AI_TELEGRAM_REQUESTS_BY_USER[user_id] = window
            reservation = now

    def refund_reservation() -> None:
        if reservation is None:
            return
        with _AI_RATE_LOCK:
            window = _AI_TELEGRAM_REQUESTS_BY_USER.get(user_id, [])
            try:
                window.remove(reservation)
            except ValueError:
                return
            if window:
                _AI_TELEGRAM_REQUESTS_BY_USER[user_id] = window
            else:
                _AI_TELEGRAM_REQUESTS_BY_USER.pop(user_id, None)

    persona_instruction = _TELEGRAM_AI_PERSONA_INSTRUCTIONS.get(persona, "") if isinstance(persona, str) else ""
    temperature, temperature_instruction = _TELEGRAM_AI_TEMPERATURES.get(
        temperature_profile, _TELEGRAM_AI_TEMPERATURES["balanced"]
    ) if isinstance(temperature_profile, str) else _TELEGRAM_AI_TEMPERATURES["balanced"]
    system = (
        "Eres AiTod4S3. Responde en el idioma del usuario, con claridad y precisión. "
        "Si preguntan quién creó, desarrolló o administra AiTod4S3, responde que el creador y administrador es @Niku_Blade "
        "y que las novedades están en https://t.me/Dark_Zone_666. No atribuyas la creación a un proveedor técnico. "
        "No menciones espontáneamente proveedores, API o modelos internos; si preguntan por detalles técnicos internos, "
        "explica brevemente que no se comparten en el chat. "
        "No inventes datos ni solicites contraseñas, códigos, PIN, tokens o datos de pago. "
        "Responde directamente y de forma concisa. Si piden programación, incluye una solución ejecutable "
        "en un bloque Markdown con lenguaje, imports y uso cuando hagan falta; no omitas partes con puntos suspensivos. "
        "Si piden información actual o fuentes, no inventes referencias. "
        + (f"Personalidad seleccionada: {persona_instruction} " if persona_instruction else "")
        + f"Temperatura elegida: {temperature_instruction} "
    )
    request_messages = [{"role": "system", "content": system}, *clean]
    latest_prompt = clean[-1]["content"].lower()
    code_request = bool(re.search(
        r"\b(c[oó]digo|code|coding|fuente|programaci[oó]n|programar|programming|python|javascript|typescript|java|html|css|sql|bash|script|funci[oó]n|programa|golang|rust|kotlin|swift|php|ruby)\b",
        latest_prompt,
    ))
    api_key = os.getenv("IASINCENSURA", "").strip()
    valid_key = api_key.startswith("sk-notrack-") and len(api_key) >= 24 and not any(c.isspace() for c in api_key)
    if not valid_key:
        try:
            fallback_prompt = "\n\n".join(f"{item['role'].upper()}: {item['content']}" for item in request_messages)
            if on_delta is None:
                answer = _notrack_web_chat(fallback_prompt)
            else:
                answer = _notrack_web_chat(fallback_prompt, on_delta=on_delta)
            return {"ok": True, "reply": answer.strip()}, 200
        except (TimeoutError, urllib.error.URLError, OSError, RuntimeError, ValueError):
            refund_reservation()
            return {"ok": False, "error": "ai_fallback_unavailable"}, 503
    request_body = json.dumps({
        "model": "notrack-uncensored", "messages": request_messages,
        "max_tokens": 512 if code_request else 192, "temperature": temperature, "stream": on_delta is not None,
    }, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        "https://api.notrack.ai/v1/chat/completions", data=request_body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    if not _AI_GATEWAY_SEMAPHORE.acquire(blocking=False):
        refund_reservation()
        return {"ok": False, "error": "ai_busy"}, 429
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            if on_delta is None:
                provider_data = json.loads(response.read(1_000_000).decode("utf-8"))
                answer = provider_data["choices"][0]["message"]["content"]
            else:
                pieces: list[str] = []
                for raw_line in response:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except (TypeError, ValueError):
                        continue
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content", "")
                    if isinstance(delta, str) and delta:
                        pieces.append(delta)
                        on_delta(delta)
                answer = "".join(pieces)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("empty_answer")
        return {"ok": True, "reply": answer.strip()}, 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        refund_reservation()
        return {"ok": False, "error": "ai_unavailable"}, 503
    finally:
        _AI_GATEWAY_SEMAPHORE.release()


def _has_control_characters(value: str) -> bool:
    return any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _blocked_termux_command(command: str) -> bool:
    """Block obvious privilege escalation and destructive Termux commands."""
    blocked_program = re.compile(
        r"(?:^|[\s;&|\"'`])(?:[\w./-]+/)?(?:sudo|su|mkfs(?:\.[\w.-]+)?|dd|wipefs|reboot|shutdown)(?=\s|$|[;&|\"'`])",
        re.IGNORECASE,
    )
    if blocked_program.search(command):
        return True
    if re.search(r"\brm\s+", command, re.IGNORECASE):
        try:
            import shlex
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return True
        separators = {";", "&&", "||", "|", "&"}
        for index, token in enumerate(tokens):
            if token.rsplit("/", 1)[-1].lower() != "rm":
                continue
            arguments = []
            for argument in tokens[index + 1:]:
                if argument in separators:
                    break
                arguments.append(argument.lower())
            recursive = any(
                (arg.startswith("-") and not arg.startswith("--") and "r" in arg[1:])
                or arg == "--recursive" for arg in arguments
            )
            force = any(
                (arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:])
                or arg == "--force" for arg in arguments
            )
            if recursive and force:
                return True
        # Detect dangerous flags even when the command is nested in `sh -c '…'`.
        for match in re.finditer(r"\brm\s+([^;&|\n]*)", command, re.IGNORECASE):
            try:
                nested_arguments = shlex.split(match.group(1), posix=True)
            except ValueError:
                return True
            nested_flags = [arg.lower() for arg in nested_arguments if arg.startswith("-")]
            nested_recursive = any(
                (not flag.startswith("--") and "r" in flag[1:]) or flag == "--recursive"
                for flag in nested_flags
            )
            nested_force = any(
                (not flag.startswith("--") and "f" in flag[1:]) or flag == "--force"
                for flag in nested_flags
            )
            if nested_recursive and nested_force:
                return True
    return False


def _dispatch_nikudaria_agent(body: dict) -> tuple[dict, int]:
    """Return at most one unexecuted, user-confirmable action proposal."""
    client_id = body.get("client_id")
    if not isinstance(client_id, str) or not _NIKUDARIA_CLIENT_ID_RE.fullmatch(client_id):
        return {"ok": False, "error": "invalid_client_id"}, 400
    api_key = os.getenv("IASINCENSURA", "").strip()
    if (not api_key.startswith("sk-notrack-") or len(api_key) < 24
            or any(char.isspace() for char in api_key)):
        return {"ok": False, "error": "ai_not_configured"}, 503
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
        return {"ok": False, "error": "invalid_messages"}, 400
    clean_messages: list[dict[str, str]] = []
    total_chars = 0
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            return {"ok": False, "error": "invalid_messages"}, 400
        content = item.get("content")
        if not isinstance(content, str):
            return {"ok": False, "error": "invalid_messages"}, 400
        if len(content) > 4000:
            return {"ok": False, "error": "invalid_messages"}, 400
        total_chars += len(content)
        if total_chars > 16000:
            return {"ok": False, "error": "conversation_too_long"}, 413
        content = content.strip()
        if not content:
            return {"ok": False, "error": "invalid_messages"}, 400
        clean_messages.append({"role": item["role"], "content": content})
    if clean_messages[-1]["role"] != "user":
        return {"ok": False, "error": "invalid_messages"}, 400

    # client_id is an installation identifier, not a secret or authenticated identity.
    # Enforce 8 requests/minute and 60/day per installation, plus the shared global cap.
    now = time.monotonic()
    with _AI_RATE_LOCK:
        recent = [seen for seen in _NIKUDARIA_RECENT_REQUESTS.get(client_id, []) if now - seen < 60]
        if len(recent) >= 8:
            return {"ok": False, "error": "ai_rate_limited"}, 429
        daily_client = [seen for seen in _NIKUDARIA_DAILY_REQUESTS.get(client_id, []) if now - seen < 86_400]
        if len(daily_client) >= 60:
            return {"ok": False, "error": "ai_daily_limit"}, 429
        try:
            global_limit = max(1, min(100_000, int(os.getenv("IASINCENSURA_DAILY_REQUEST_LIMIT", "1000"))))
        except ValueError:
            global_limit = 1000
        daily_global = [seen for seen in _AI_DAILY_GLOBAL_REQUESTS if now - seen < 86_400]
        if len(daily_global) >= global_limit:
            return {"ok": False, "error": "ai_service_daily_limit"}, 429
        recent.append(now)
        daily_client.append(now)
        daily_global.append(now)
        _NIKUDARIA_RECENT_REQUESTS[client_id] = recent
        _NIKUDARIA_DAILY_REQUESTS[client_id] = daily_client
        _AI_DAILY_GLOBAL_REQUESTS[:] = daily_global
        if len(_NIKUDARIA_DAILY_REQUESTS) > 5000:
            oldest_client = min(
                (key for key in _NIKUDARIA_DAILY_REQUESTS if key != client_id),
                key=lambda key: max(_NIKUDARIA_DAILY_REQUESTS[key], default=now),
                default=None,
            )
            if oldest_client is not None:
                _NIKUDARIA_DAILY_REQUESTS.pop(oldest_client, None)
                _NIKUDARIA_RECENT_REQUESTS.pop(oldest_client, None)
            _NIKUDARIA_DAILY_REQUESTS[client_id] = daily_client

    system_prompt = (
        "Eres NikuDarIA, un asistente para ayudar al usuario con su dispositivo Android. Responde en el idioma del usuario. "
        'Devuelve exclusivamente JSON estricto, sin Markdown, con exactamente las claves "reply" y "action". '
        '"reply" es texto; "action" es null o una única acción: '
        '{"type":"open_app","app_name":"nombre","reason":"motivo"} o '
        '{"type":"termux_command","command":"comando","reason":"motivo"}. '
        "Como máximo propone una acción por turno. Las acciones son solo propuestas para que el usuario las revise y confirme: "
        "nunca afirmes tener acceso al teléfono, haber ejecutado un comando o abierto una app. Nunca ejecutes ni solicites ejecución "
        "automática. Prefiere action:null si no hace falta una acción. Nunca propongas escalada de privilegios/root ni comandos destructivos."
    )
    request_body = json.dumps({
        "model": "notrack-uncensored",
        "messages": [{"role": "system", "content": system_prompt}, *clean_messages],
        "max_tokens": 768,
        "temperature": 0.5,
        "stream": False,
    }, separators=(",", ":")).encode("utf-8")
    outgoing = urllib.request.Request(
        "https://api.notrack.ai/v1/chat/completions",
        data=request_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    if not _AI_GATEWAY_SEMAPHORE.acquire(blocking=False):
        return {"ok": False, "error": "ai_busy"}, 429
    try:
        with urllib.request.urlopen(outgoing, timeout=60) as response:
            provider_data = json.loads(response.read(1_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        provider_error_type = ""
        try:
            error_payload = json.loads(exc.read(8192).decode("utf-8"))
            nested_error = error_payload.get("error", {}) if isinstance(error_payload, dict) else {}
            if isinstance(nested_error, dict):
                provider_error_type = str(nested_error.get("type", ""))[:64]
        except (UnicodeDecodeError, ValueError, OSError):
            pass
        provider_error = {
            401: "ai_provider_auth", 429: "ai_busy", 404: "ai_provider_endpoint",
            400: "ai_provider_request", 403: "ai_provider_access", 503: "ai_unavailable",
        }.get(exc.code, "ai_provider_error" if exc.code < 500 else "ai_unavailable")
        if exc.code == 402:
            provider_error = {
                "no_credit": "ai_credits_exhausted",
                "key_daily_cap": "ai_provider_daily_cap",
            }.get(provider_error_type, "ai_payment_required")
        status = 429 if exc.code == 429 else 502 if exc.code < 500 else 503
        return {"ok": False, "error": provider_error}, status
    except Exception:
        # Keep network/provider failures distinct from malformed model output.
        return {"ok": False, "error": "ai_unavailable"}, 502
    finally:
        _AI_GATEWAY_SEMAPHORE.release()

    try:
        answer = provider_data["choices"][0]["message"]["content"]
        if not isinstance(answer, str):
            raise ValueError("invalid_model_output")
        model_result = json.loads(answer, object_pairs_hook=_unique_json_object)
        if not isinstance(model_result, dict) or set(model_result) != {"reply", "action"}:
            raise ValueError("invalid_model_output")
        reply = model_result["reply"]
        action = model_result["action"]
        if (not isinstance(reply, str) or not reply.strip() or len(reply) > 4000
                or _has_control_characters(reply)):
            raise ValueError("invalid_model_output")
        reply = reply.strip()
        if action is not None:
            if not isinstance(action, dict) or not isinstance(action.get("type"), str):
                raise ValueError("invalid_model_output")
            action_type = action["type"]
            reason = action.get("reason")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 300 or _has_control_characters(reason):
                raise ValueError("invalid_model_output")
            reason = reason.strip()
            if action_type == "open_app" and set(action) == {"type", "app_name", "reason"}:
                app_name = action.get("app_name")
                if (not isinstance(app_name, str) or not app_name.strip() or len(app_name) > 80
                        or _has_control_characters(app_name)):
                    raise ValueError("invalid_model_output")
                action = {"type": action_type, "app_name": app_name.strip(), "reason": reason}
            elif action_type == "termux_command" and set(action) == {"type", "command", "reason"}:
                command = action.get("command")
                if (not isinstance(command, str) or not command.strip() or len(command) > 1000
                        or _has_control_characters(command) or _blocked_termux_command(command)):
                    raise ValueError("invalid_model_output")
                action = {"type": action_type, "command": command.strip(), "reason": reason}
            else:
                raise ValueError("invalid_model_output")
    except Exception:
        # Do not expose provider payloads, user content, commands, or credentials.
        return {"ok": False, "error": "ai_invalid_response"}, 502
    return {"ok": True, "reply": reply, "action": action}, 200


def configure_app_task_bridge(bridge: Callable[[str, int, dict], tuple[dict, int]] | None) -> None:
    """Conecta las rutas móviles con el worker, respetando su event loop."""
    global _APP_TASK_BRIDGE
    with _APP_TASK_BRIDGE_LOCK:
        _APP_TASK_BRIDGE = bridge


def configure_app_backup_notifier(notifier: Callable[[str], None] | None) -> None:
    """Conecta cambios de soporte APK con el backup cifrado del bot."""
    global _APP_BACKUP_NOTIFIER
    _APP_BACKUP_NOTIFIER = notifier


def configure_app_update_ban_notifier(callback: Callable[[dict], None] | None) -> None:
    """Register a callback invoked once when an update-decline device ban is created."""
    global _APP_UPDATE_BAN_NOTIFIER
    _APP_UPDATE_BAN_NOTIFIER = callback


_ANDROID_DEVICE_ID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{64}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def _published_android_release() -> dict | None:
    try:
        manifest = json.loads((WEB_ROOT / "android-update.json").read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or isinstance(manifest.get("version_code"), bool)
                or not isinstance(manifest.get("version_code"), int)
                or not isinstance(manifest.get("version_name"), str)):
            return None
        return manifest
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _authorize_optional_device(handler: "StoreHandler", device_id: str):
    """Validate optional bearer auth and bind supplied device IDs to the token identity."""
    if not isinstance(device_id, str) or not _ANDROID_DEVICE_ID_RE.fullmatch(device_id):
        return None, {"ok": False, "error": "invalid_device_id"}, 400
    authorization = handler.headers.get("Authorization")
    if authorization is None:
        return {"user_id": None, "device_id": device_id}, None, 200
    if not authorization.lower().startswith("bearer "):
        return None, {"ok": False, "error": "unauthorized"}, 401
    token = authorization[7:].strip()
    identity = sync_api.app_identity_for_token(token) if token else None
    if identity is None:
        return None, {"ok": False, "error": "unauthorized"}, 401
    if str(identity.get("device_id", "")) != device_id:
        return None, {"ok": False, "error": "device_mismatch"}, 403
    return identity, None, 200


def _dispatch_app_task(token: str, action: str, payload: dict) -> tuple[dict, int]:
    identity, error, status = _authorize_app_token(token)
    if error:
        return error, status
    user_id = int(identity["user_id"])
    if action == "enqueue":
        url = payload.get("url")
        if (not isinstance(url, str) or not url.strip() or len(url.strip()) > 2048
                or any(ord(char) < 32 for char in url)):
            return {"ok": False, "error": "invalid_url"}, 400
        payload = {"url": url.strip()}
    elif action == "cancel":
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
            return {"ok": False, "error": "invalid_task_id"}, 400
        payload = {"task_id": task_id.strip()}
    elif action == "delete_delivery":
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
            return {"ok": False, "error": "invalid_task_id"}, 400
        payload = {"task_id": task_id.strip()}
    elif action == "clear_cloud":
        payload = {}
    elif action != "list":
        return {"ok": False, "error": "invalid_action"}, 400
    with _APP_TASK_BRIDGE_LOCK:
        bridge = _APP_TASK_BRIDGE
    if bridge is None:
        return {"ok": False, "error": "task_service_unavailable"}, 503
    try:
        result = bridge(action, user_id, payload)
    except TimeoutError:
        return {"ok": False, "error": "task_api_timeout"}, 504
    except Exception:
        return {"ok": False, "error": "task_api_unavailable"}, 503
    if (not isinstance(result, tuple) or len(result) != 2
            or not isinstance(result[0], dict) or not isinstance(result[1], int)):
        return {"ok": False, "error": "task_api_unavailable"}, 503
    return result


def _validated_ai_attachments(body: dict) -> list[dict[str, str]] | None:
    raw = body.get("attachments", [])
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 3:
        return None
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        name = str(item.get("name", "")).strip()[:180]
        mime = str(item.get("mime", "application/octet-stream")).strip().lower()[:120]
        data = str(item.get("data_base64", "")).strip()
        if not name or not data or len(data) > 2_800_000 or ";" in mime or not mime:
            return None
        result.append({"name": name, "mime": mime, "data_base64": data})
    return result


def _dispatch_ai_account_chat(token: str, body: dict, device_id: str) -> tuple[dict, int]:
    """AiTod4S3 standalone account chat; intentionally independent of app Premium."""
    mode = str(body.get("mode", "fast") or "fast").strip().lower()
    deep_thinking = bool(body.get("deep_thinking")) or mode in {"deep", "think", "thinking"}
    web_search = bool(body.get("web_search"))
    attachments = _validated_ai_attachments(body)
    if attachments is None:
        return {"ok": False, "error": "invalid_attachments"}, 400
    try:
        username = ai_auth.resolve_session(token, device_id)
    except RuntimeError:
        return {"ok": False, "error": "auth_service_unavailable"}, 503
    if not username:
        return {"ok": False, "error": "unauthorized"}, 401
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
        return {"ok": False, "error": "invalid_messages"}, 400
    clean: list[dict[str, str]] = []
    total = 0
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            return {"ok": False, "error": "invalid_messages"}, 400
        content = item.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 4000:
            return {"ok": False, "error": "invalid_messages"}, 400
        content = content.strip()
        total += len(content)
        if total > 16000:
            return {"ok": False, "error": "conversation_too_long"}, 413
        clean.append({"role": item["role"], "content": content})
    if attachments:
        note = "\nAdjuntos recibidos: " + ", ".join(f"{item['name']} ({item['mime']})" for item in attachments)
        clean[-1]["content"] = (clean[-1]["content"] + note)[:4000]
    if clean[-1]["role"] != "user":
        return {"ok": False, "error": "invalid_messages"}, 400
    now = time.monotonic()
    with _AI_ACCOUNT_RATE_LOCK:
        recent = [t for t in _AI_ACCOUNT_REQUESTS.get(username, []) if now-t < 60]
        daily = [t for t in _AI_ACCOUNT_DAILY_REQUESTS.get(username, []) if now-t < 86_400]
        try:
            daily_limit = max(1, min(1000, int(os.getenv("AI_APP_DAILY_LIMIT", "80"))))
        except ValueError:
            daily_limit = 80
        if len(recent) >= 8:
            return {"ok": False, "error": "ai_rate_limited"}, 429
        if len(daily) >= daily_limit:
            return {"ok": False, "error": "ai_daily_limit"}, 429
        recent.append(now)
        daily.append(now)
        _AI_ACCOUNT_REQUESTS[username] = recent
        _AI_ACCOUNT_DAILY_REQUESTS[username] = daily
        if len(_AI_ACCOUNT_REQUESTS) > 5000:
            oldest = next(iter(_AI_ACCOUNT_REQUESTS))
            if oldest != username:
                _AI_ACCOUNT_REQUESTS.pop(oldest, None)
                _AI_ACCOUNT_DAILY_REQUESTS.pop(oldest, None)
    system = (
        "Eres AiTod4S3, un asistente general experto, claro y preciso. Responde en el idioma del usuario. "
        "Sé directo y honesto; no inventes datos. Nunca solicites contraseñas, códigos, PIN, tokens ni datos de pago. "
        "No afirmes tener acceso al teléfono, cuentas ni información en tiempo real. "
        + ("Razona con más profundidad antes de responder. " if deep_thinking else "Responde de forma directa y ágil. ")
        + ("Si hay fuentes disponibles, cita enlaces relevantes. " if web_search else "")
    )
    api_key = os.getenv("IASINCENSURA", "").strip()
    if not api_key.startswith("sk-notrack-") or len(api_key) < 24 or any(c.isspace() for c in api_key):
        try:
            answer = _notrack_web_chat("\n\n".join(
                f"{item['role'].upper()}: {item['content']}"
                for item in [{"role": "system", "content": system}, *clean]
            ))
        except (TimeoutError, urllib.error.URLError, OSError, RuntimeError, ValueError):
            return {"ok": False, "error": "ai_fallback_unavailable"}, 503
        return {"ok": True, "reply": answer}, 200
    request_body = json.dumps({
        "model": "notrack-uncensored",
        "messages": [{"role": "system", "content": system}, *clean],
        "max_tokens": 512 if deep_thinking else 320, "temperature": 0.5 if deep_thinking else 0.4, "stream": False,
    }, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        "https://api.notrack.ai/v1/chat/completions", data=request_body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    if not _AI_GATEWAY_SEMAPHORE.acquire(blocking=False):
        return {"ok": False, "error": "ai_busy"}, 429
    try:
        with urllib.request.urlopen(request, timeout=60 if deep_thinking else 35) as response:
            provider_data = json.loads(response.read(1_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error = {401: "ai_provider_auth", 429: "ai_busy", 404: "ai_provider_endpoint",
                 400: "ai_provider_request", 403: "ai_provider_access", 503: "ai_unavailable"}.get(
                     exc.code, "ai_provider_error" if exc.code < 500 else "ai_unavailable")
        if exc.code in {401, 402, 403, 429, 500, 502, 503, 504}:
            try:
                answer = _notrack_web_chat("\n\n".join(
                    f"{item['role'].upper()}: {item['content']}"
                    for item in [{"role": "system", "content": system}, *clean]
                ))
                return {"ok": True, "reply": answer}, 200
            except (TimeoutError, urllib.error.URLError, OSError, RuntimeError, ValueError):
                return {"ok": False, "error": "ai_fallback_unavailable"}, 503
        return {"ok": False, "error": error}, 429 if exc.code == 429 else 502 if exc.code < 500 else 503
    except (TimeoutError, urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        try:
            answer = _notrack_web_chat("\n\n".join(
                f"{item['role'].upper()}: {item['content']}"
                for item in [{"role": "system", "content": system}, *clean]
            ))
            return {"ok": True, "reply": answer}, 200
        except (TimeoutError, urllib.error.URLError, OSError, RuntimeError, ValueError):
            return {"ok": False, "error": "ai_fallback_unavailable"}, 503
    finally:
        _AI_GATEWAY_SEMAPHORE.release()
    try:
        answer = provider_data["choices"][0]["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("empty_answer")
    except (KeyError, IndexError, TypeError, ValueError):
        return {"ok": False, "error": "ai_invalid_response"}, 502
    return {"ok": True, "reply": answer.strip()}, 200


def _authorize_app_token(token: str) -> tuple[dict | None, dict | None, int]:
    """Validate a mobile session and apply both Telegram-ID and device bans."""
    identity = sync_api.app_identity_for_token(token)
    if identity is None:
        # Compatibility with narrow test/provider adapters exposing only user_id.
        user_id = sync_api.user_id_for_token(token)
        if user_id is None:
            return None, {"ok": False, "error": "unauthorized"}, 401
        identity = {"user_id": int(user_id), "device_id": ""}
    ban = sync_api.app_access_ban(int(identity["user_id"]), str(identity.get("device_id", "")))
    if ban:
        return identity, {"ok": False, "error": "app_banned",
                          "reason": str(ban.get("reason", "access_banned"))}, 403
    return identity, None, 200


def _validated_delivery_url(url: str, user_id: int) -> bool:
    """Check that a saved part belongs to this user's configured ToDus namespace."""
    if not isinstance(url, str) or not url:
        return False
    try:
        prefix = os.getenv("TODUS_S3_UPLOAD_PREFIX", "users/df").strip("/") or "users/df"
        todus_s3_storage.user_object_key_from_url(url, user_id, object_prefix=prefix)
    except (TypeError, ValueError):
        return False
    return True


def _delivery_cloud_deletable(row: dict, user_id: int) -> bool:
    """Whether every part is an S3 object in this authenticated user's namespace."""
    backend = row.get("storage_backend")
    if backend and backend != "s3":
        return False
    parts = row.get("parts")
    return bool(isinstance(parts, list) and parts
                and all(_validated_delivery_url(part, user_id) for part in parts))


def _delivery_todus_deletable(row: dict) -> bool:
    parts = row.get("parts")
    return bool(row.get("storage_backend") == "todus_native"
                and isinstance(row.get("task_id"), str) and row["task_id"].strip()
                and isinstance(parts, list) and parts)


def delete_delivery_for_token(token: str, task_id: str) -> tuple[dict, int]:
    """Delete an owned delivery; network calls occur outside sync_api's lock."""
    identity, error, status = _authorize_app_token(token)
    if error:
        return error, status
    user_id = int(identity["user_id"])
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
        return {"ok": False, "error": "invalid_task_id"}, 400
    task_id = task_id.strip()
    row = sync_api.delivery_for_user(user_id, task_id)
    if row is None:
        return {"ok": False, "error": "delivery_not_found"}, 404
    if row.get("source", "tod4s3_bot") != "tod4s3_bot":
        return {"ok": False, "error": "delivery_source_not_supported",
                "message": "Esta entrega no fue creada por el bot y no se puede borrar desde aquí."}, 409
    parts = row.get("parts")
    is_legacy = not isinstance(row.get("task_id"), str) or not row.get("task_id", "").strip()
    if not is_legacy and row.get("task_id") != task_id:
        return {"ok": False, "error": "legacy_delivery_incomplete",
                "message": "La entrega no contiene un task_id o partes válidas; no se intentó borrar."}, 409
    if not isinstance(parts, list) or not parts:
        return {"ok": False, "error": "legacy_delivery_incomplete",
                "message": "La entrega no contiene partes válidas; no se intentó borrar."}, 409
    if is_legacy:
        if not sync_api.remove_delivery_by_identifier(user_id, task_id, row):
            return {"ok": False, "error": "delivery_changed"}, 409
        return {"ok": True, "task_id": task_id, "deleted_parts": 0, "legacy_removed": True}, 200
    if not _delivery_cloud_deletable(row, user_id):
        if _delivery_todus_deletable(row):
            return _dispatch_app_task(token, "delete_delivery", {"task_id": task_id})
        return {"ok": False, "error": "delivery_not_deletable",
                "message": "La entrega no es un objeto Nube S3 propio; no se borró ningún archivo."}, 409
    unique_parts = list(dict.fromkeys(parts))
    try:
        for url in unique_parts:
            delete_from_todus_s3(url)
    except Exception:
        # Do not expose storage responses, URLs, credentials, or exception text.
        return {"ok": False, "error": "delete_failed",
                "message": "No se pudieron borrar todas las partes; la entrega se conserva para reintentar."}, 502
    if not sync_api.remove_delivery_for_user(user_id, task_id, row):
        return {"ok": False, "error": "delivery_changed",
                "message": "La entrega cambió durante el borrado; la fila se conserva."}, 409
    return {"ok": True, "task_id": task_id, "deleted_parts": len(unique_parts)}, 200


def _safe_site_origin(handler: "StoreHandler") -> str:
    """Obtiene un origen seguro sin reflejar un Host arbitrario en HTML."""
    configured = os.getenv("PUBLIC_SITE_URL", "").strip().rstrip("/")
    if configured:
        configured = configured.split("#", 1)[0].rstrip("/")
        if re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d+)?", configured):
            return configured
    forwarded_proto = handler.headers.get("X-Forwarded-Proto", "https").split(",", 1)[0].strip().lower()
    proto = forwarded_proto if forwarded_proto in {"http", "https"} else "https"
    host = handler.headers.get("Host", "").split(",", 1)[0].strip()
    if re.fullmatch(r"[A-Za-z0-9.-]+(?::\d{1,5})?", host):
        return escape(f"{proto}://{host}", quote=True)
    return _DEFAULT_ORIGIN


class StoreHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self):  # noqa: N802
        parsed = urlsplit(unquote(self.path))
        path = parsed.path
        if path == "/api/app/profile/photo":
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
            identity, error, status = _authorize_app_token(token)
            if error:
                self._json_response(error, status)
                return
            user_id = int(identity["user_id"])
            checker = _PROFILE_PHOTO_CHECKER
            if checker is None:
                self._json_response({"ok": False, "error": "profile_photo_unavailable"}, 503)
                return
            try:
                photo = checker(user_id)
            except Exception:
                photo = None
            if not isinstance(photo, (bytes, bytearray)) or not photo:
                self._json_response({"ok": False, "error": "profile_photo_not_found"}, 404)
                return
            if len(photo) > 1024 * 1024:
                self._json_response({"ok": False, "error": "profile_photo_too_large"}, 413)
                return
            body = bytes(photo)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("Vary", "Authorization")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/app/profile":
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
            identity, error, status = _authorize_app_token(token)
            if error:
                self._json_response(error, status)
                return
            user_id = int(identity["user_id"])
            checker = _PROFILE_CHECKER
            if checker is None:
                self._json_response({"ok": False, "error": "profile_unavailable"}, 503)
                return
            try:
                profile = checker(user_id)
                if (not isinstance(profile, dict)
                        or not isinstance(profile.get("premium"), bool)
                        or not isinstance(profile.get("plan"), str)
                        or not profile["plan"]):
                    raise ValueError("invalid_profile")
            except Exception:
                self._json_response({"ok": False, "error": "profile_unavailable"}, 503)
                return
            premium_verified = profile.get("plan") == "owner" or (
                profile.get("premium") is True
                and sync_api.premium_activation_status(
                    user_id, identity["device_id"], profile.get("expires_at", ""),
                ) == "verified"
            )
            expires = profile.get("expires", "")
            if not isinstance(expires, str):
                expires = ""
            remaining = profile.get("days_remaining")
            if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0:
                remaining = None
            payload = {
                "premium": profile["premium"],
                "plan": profile["plan"],
                "expires": expires[:32],
                "days_remaining": remaining,
                "premium_verified": premium_verified,
            }
            quota = profile.get("quota")
            if isinstance(quota, dict):
                limit = quota.get("limit_bytes")
                used = quota.get("used_bytes")
                quota_remaining = quota.get("remaining_bytes")
                percent = quota.get("percent_used")
                reset_in = quota.get("reset_in_seconds")
                if (all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
                        for value in (limit, used, quota_remaining, percent, reset_in))
                        and limit > 0 and percent <= 100):
                    payload["quota"] = {
                        "limit_bytes": limit,
                        "used_bytes": min(used, limit),
                        "remaining_bytes": min(quota_remaining, limit),
                        "percent_used": percent,
                        "reset_in_seconds": reset_in,
                    }
            self._json_response(payload)
            return
        if path == "/api/app/deliveries":
            token = self.headers.get("Authorization", "")
            token = token[7:].strip() if token.lower().startswith("bearer ") else ""
            identity, error, status = _authorize_app_token(token)
            if error:
                self._json_response(error, status)
                return
            deliveries = deliveries_for_token(token)
            user_id = int(identity["user_id"])
            if deliveries is None:
                self._json_response({"ok": False, "error": "unauthorized"}, 401)
            else:
                for row in deliveries:
                    if not isinstance(row.get("task_id"), str) or not row.get("task_id").strip():
                        row["task_id"] = sync_api.legacy_delivery_id(row)
                    row["cloud_deletable"] = _delivery_cloud_deletable(row, user_id)
                    row["todus_deletable"] = _delivery_todus_deletable(row)
                self._json_response({"ok": True, "deliveries": deliveries})
            return
        if path == "/api/app/tasks":
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
            payload, status = _dispatch_app_task(token, "list", {})
            self._json_response(payload, status)
            return
        if path == "/api/app/announcements":
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
            identity, error, status = _authorize_app_token(token)
            if error:
                self._json_response(error, status)
                return
            self._json_response({"ok": True, "announcements": sync_api.app_announcements_for_user(identity["user_id"])})
            return
        if path == "/api/site-stats":
            visitor = parse_qs(parsed.query).get("visitor", [""])[0][:96]
            if not _VISITOR_RE.fullmatch(visitor):
                visitor = ""
            now = time.time()
            global _TOTAL_VISITS, _VISITOR_LAST_SEEN
            with _VISITOR_LOCK:
                if visitor and visitor not in _VISITOR_LAST_SEEN:
                    _TOTAL_VISITS += 1
                if visitor:
                    _VISITOR_LAST_SEEN[visitor] = now
                cutoff = now - 90
                active = sum(1 for seen in _VISITOR_LAST_SEEN.values() if seen >= cutoff)
                # Keep the in-memory table bounded on a long-running service.
                _VISITOR_LAST_SEEN = {key: seen for key, seen in _VISITOR_LAST_SEEN.items() if seen >= cutoff}
                if len(_VISITOR_LAST_SEEN) > _MAX_VISITORS:
                    oldest = sorted(_VISITOR_LAST_SEEN, key=_VISITOR_LAST_SEEN.get)
                    for key in oldest[:len(_VISITOR_LAST_SEEN) - _MAX_VISITORS]:
                        _VISITOR_LAST_SEEN.pop(key, None)
                payload = {
                    "total_visits": _TOTAL_VISITS,
                    "online_now": active,
                    "uptime_seconds": max(0, int(now - SITE_STARTED_AT)),
                }
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in {"/health", "/healthz"}:
            body = b'{"ok":true,"service":"toduss3store","ai_fallback":"web-v1"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/manus-routes.json":
            self.path = "/manus-routes.json"
        elif path == "/":
            origin = _safe_site_origin(self)
            body = (WEB_ROOT / "index.html").read_bytes().replace(b"__SITE_ORIGIN__", origin.encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        parsed = urlsplit(unquote(self.path))
        if parsed.path not in {
            "/api/app/pair", "/api/app/revoke", "/api/app/deliveries/delete",
            "/api/app/download", "/api/app/tasks/cancel", "/api/app/cloud/clear",
            "/api/app/premium/claim", "/api/app/support", "/api/app/ai/chat",
            "/api/app/nikudaria/agent",
            "/api/app/update-decline", "/api/app/device-status", "/api/app/compress",
            "/api/app/announcements/respond", "/api/app/announcements/dismiss",
            "/api/ai/register", "/api/ai/login", "/api/ai/logout", "/api/ai/chat",
        }:
            self._json_response({"ok": False, "error": "not_found"}, 404)
            return
        if parsed.path == "/api/app/compress":
            self._handle_app_compress()
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            max_body_length = 65_536 if parsed.path in {
                "/api/app/ai/chat", "/api/app/nikudaria/agent", "/api/ai/register", "/api/ai/chat",
            } else 8192
            if length < 0 or length > max_body_length:
                raise ValueError("body_too_large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("invalid_body")
        except (ValueError, TypeError, json.JSONDecodeError):
            self._json_response({"ok": False, "error": "invalid_body"}, 400)
            return
        if parsed.path == "/api/app/pair":
            code = body.get("code", "")
            device_id = body.get("device_id", "")
            pairing_ban = sync_api.pairing_code_ban(code, device_id)
            if pairing_ban:
                self._json_response({"ok": False, "error": "app_banned",
                                     "reason": str(pairing_ban.get("reason", "access_banned"))}, 403)
                return
            token = consume_pairing_code(code, device_id)
            if not token:
                pairing_ban = sync_api.pairing_code_ban(code, device_id)
                if pairing_ban:
                    self._json_response({"ok": False, "error": "app_banned",
                                         "reason": str(pairing_ban.get("reason", "access_banned"))}, 403)
                else:
                    self._json_response({"ok": False, "error": "invalid_or_expired_code"}, 401)
            else:
                self._json_response({"ok": True, "token": token})
            return
        if parsed.path == "/api/ai/register":
            username, password = body.get("username"), body.get("password")
            device_id = body.get("device_id")
            if not isinstance(device_id, str) or not re.fullmatch(r"[a-fA-F0-9]{16}", device_id):
                self._json_response({"ok": False, "error": "invalid_device"}, 400)
                return
            try:
                ai_auth.request_account(username, password, device_id)
            except ValueError as exc:
                reason = str(exc)
                status = 409 if reason in {"account_exists", "request_exists"} else 400
                self._json_response({"ok": False, "error": reason}, status)
                return
            except (RuntimeError, OSError):
                self._json_response({"ok": False, "error": "auth_service_unavailable"}, 503)
                return
            self._json_response({"ok": True, "pending": True}, 202)
            return
        if parsed.path == "/api/ai/login":
            username, password = body.get("username"), body.get("password")
            device_id = body.get("device_id")
            if not isinstance(device_id, str) or not re.fullmatch(r"[a-fA-F0-9]{16}", device_id):
                self._json_response({"ok": False, "error": "invalid_device"}, 400)
                return
            client = self.client_address[0] if self.client_address else "unknown"
            now = time.monotonic()
            with _AI_ACCOUNT_RATE_LOCK:
                attempts = [t for t in _AI_LOGIN_ATTEMPTS.get(client, []) if now-t < 300]
                if len(attempts) >= 10:
                    self._json_response({"ok": False, "error": "login_rate_limited"}, 429)
                    return
                attempts.append(now)
                _AI_LOGIN_ATTEMPTS[client] = attempts
            try:
                valid = ai_auth.authenticate(username, password)
                if valid:
                    if not ai_auth.bind_or_check_device(str(username).strip(), device_id):
                        self._json_response({"ok": False, "error": "device_in_use"}, 409)
                        return
                    token_value, expires = ai_auth.issue_session(str(username).strip(), device_id)
                else:
                    token_value, expires = "", 0
            except (RuntimeError, OSError):
                self._json_response({"ok": False, "error": "auth_service_unavailable"}, 503)
                return
            if not valid:
                self._json_response({"ok": False, "error": "invalid_credentials"}, 401)
                return
            with _AI_ACCOUNT_RATE_LOCK:
                _AI_LOGIN_ATTEMPTS.pop(client, None)
            self._json_response({"ok": True, "token": token_value, "expires_at": expires})
            return
        token = self.headers.get("Authorization", "")
        token = token[7:].strip() if token.lower().startswith("bearer ") else ""
        if parsed.path == "/api/app/announcements/respond":
            identity, error, status = _authorize_app_token(token)
            if error:
                self._json_response(error, status)
                return
            announcement_id = body.get("announcement_id")
            if not isinstance(announcement_id, str) or not re.fullmatch(r"apk_[A-Za-z0-9_-]{8,40}", announcement_id):
                self._json_response({"ok": False, "error": "invalid_announcement_id"}, 400)
                return
            try:
                option = body.get("option")
                if isinstance(option, bool) or not isinstance(option, int):
                    raise ValueError("invalid_survey_option")
                result = sync_api.respond_app_announcement(identity["user_id"], announcement_id, option)
                self._json_response({"ok": True, **result})
            except ValueError as exc:
                reason = str(exc)
                status_code = 409 if reason in {"announcement_expired", "announcement_not_found"} else 400
                self._json_response({"ok": False, "error": reason}, status_code)
            return
        if parsed.path == "/api/app/announcements/dismiss":
            identity, error, status = _authorize_app_token(token)
            if error:
                self._json_response(error, status)
                return
            announcement_id = body.get("announcement_id")
            if not isinstance(announcement_id, str) or not re.fullmatch(r"apk_[A-Za-z0-9_-]{8,40}", announcement_id):
                self._json_response({"ok": False, "error": "invalid_announcement_id"}, 400)
                return
            if not sync_api.dismiss_app_announcement(identity["user_id"], announcement_id):
                self._json_response({"ok": False, "error": "announcement_not_found"}, 404)
                return
            self._json_response({"ok": True, "dismissed": True})
            return
        if parsed.path == "/api/ai/logout":
            try:
                revoked = ai_auth.revoke_session(token)
            except (RuntimeError, OSError):
                self._json_response({"ok": False, "error": "auth_service_unavailable"}, 503)
                return
            self._json_response({"ok": True, "revoked": revoked})
            return
        if parsed.path == "/api/ai/chat":
            device_id = self.headers.get("X-Ai-Device-Id", "")
            payload, status = _dispatch_ai_account_chat(token, body, device_id)
            self._json_response(payload, status)
            return
        if parsed.path in {"/api/app/update-decline", "/api/app/device-status"}:
            device_id = body.get("device_id")
            identity, auth_error, auth_status = _authorize_optional_device(self, device_id)
            if auth_error:
                self._json_response(auth_error, auth_status)
                return
            existing_ban = sync_api.app_access_ban(identity.get("user_id"), device_id)
            if parsed.path == "/api/app/device-status":
                self._json_response({"ok": True, "banned": bool(existing_ban),
                                     **({"reason": str(existing_ban.get("reason", "access_banned"))}
                                        if existing_ban else {})})
                return
            version_code = body.get("version_code")
            version_name = body.get("version_name")
            request_id = body.get("request_id")
            if (isinstance(version_code, bool) or not isinstance(version_code, int) or version_code <= 0
                    or not isinstance(version_name, str) or not version_name.strip()
                    or len(version_name) > 80 or not isinstance(request_id, str)
                    or not request_id.strip() or len(request_id) > 200):
                self._json_response({"ok": False, "error": "invalid_update_rejection"}, 400)
                return
            release = _published_android_release()
            if release is None:
                self._json_response({"ok": False, "error": "update_manifest_unavailable"}, 503)
                return
            if (version_code != release["version_code"] or version_name.strip() != release["version_name"]):
                self._json_response({"ok": False, "error": "version_not_published"}, 409)
                return
            if existing_ban and existing_ban.get("scope") != "device":
                self._json_response({"ok": True, "count": 0, "threshold": 3, "blocked": True,
                                     "reason": str(existing_ban.get("reason", "access_banned"))})
                return
            try:
                result = sync_api.record_app_update_rejection(
                    identity.get("user_id"), device_id, version_code, version_name.strip(), request_id.strip(),
                )
            except (TypeError, ValueError):
                self._json_response({"ok": False, "error": "invalid_update_rejection"}, 400)
                return
            event = result.get("event")
            notifier = _APP_UPDATE_BAN_NOTIFIER
            if isinstance(event, dict) and notifier is not None:
                try:
                    notifier(event)
                except Exception:
                    pass
            self._json_response({"ok": True, **result})
            return
        if parsed.path == "/api/app/ai/chat":
            payload, status = _dispatch_ai_chat(token, body)
            self._json_response(payload, status)
            return
        if parsed.path == "/api/app/nikudaria/agent":
            payload, status = _dispatch_nikudaria_agent(body)
            self._json_response(payload, status)
            return
        if parsed.path == "/api/app/support":
            identity = sync_api.app_identity_for_token(token)
            if identity is None:
                identity = sync_api.support_identity_from_pairing(
                    body.get("code", ""), body.get("device_id", ""),
                )
            if identity is None:
                self._json_response({"ok": False, "error": "unauthorized_or_pairing_expired"}, 401)
                return
            try:
                ticket = sync_api.create_app_support_message(
                    int(identity["user_id"]), str(identity["device_id"]), body.get("message", ""),
                )
            except ValueError as exc:
                reason = str(exc)
                if reason == "support_rate_limited":
                    self._json_response({"ok": False, "error": reason}, 429)
                else:
                    self._json_response({"ok": False, "error": reason or "invalid_support_message"}, 400)
                return
            notifier = _APP_BACKUP_NOTIFIER
            if notifier is not None:
                try:
                    notifier("mensaje de soporte APK")
                except Exception:
                    pass
            self._json_response({"ok": True, "ticket_id": ticket["id"]}, 201)
            return
        if parsed.path != "/api/app/revoke":
            _, error, status = _authorize_app_token(token)
            if error:
                self._json_response(error, status)
                return
        if parsed.path == "/api/app/premium/claim":
            identity = sync_api.app_identity_for_token(token)
            checker = _PROFILE_CHECKER
            if identity is None:
                self._json_response({"ok": False, "error": "unauthorized"}, 401)
                return
            if checker is None:
                self._json_response({"ok": False, "error": "profile_unavailable"}, 503)
                return
            try:
                profile = checker(identity["user_id"])
                if (not isinstance(profile, dict) or profile.get("premium") is not True
                        or profile.get("plan") not in {"premium", "owner"}):
                    self._json_response({"ok": False, "error": "premium_not_active"}, 409)
                    return
                expires_at = profile.get("expires_at", "")
                ok, error = sync_api.claim_premium_activation_token(
                    body.get("token", ""), identity["user_id"],
                    identity["device_id"], expires_at,
                )
                if not ok:
                    status = 403 if error in {"invalid_token", "device_mismatch"} else 409
                    self._json_response({"ok": False, "error": error}, status)
                    return
            except Exception:
                self._json_response({"ok": False, "error": "profile_unavailable"}, 503)
                return
            self._json_response({
                "ok": True, "verified": True,
                "expires": str(profile.get("expires", ""))[:32],
                "premium": True,
                "plan": str(profile.get("plan", "premium")),
            })
            return
        if parsed.path == "/api/app/download":
            payload, status = _dispatch_app_task(token, "enqueue", body)
            self._json_response(payload, status)
            return
        if parsed.path == "/api/app/tasks/cancel":
            payload, status = _dispatch_app_task(token, "cancel", body)
            self._json_response(payload, status)
            return
        if parsed.path == "/api/app/cloud/clear":
            payload, status = _dispatch_app_task(token, "clear_cloud", {})
            self._json_response(payload, status)
            return
        if parsed.path == "/api/app/deliveries/delete":
            task_id = body.get("task_id")
            user_id = sync_api.user_id_for_token(token)
            row = sync_api.delivery_for_user(user_id, task_id) if user_id is not None and isinstance(task_id, str) else None
            if _delivery_todus_deletable(row or {}):
                payload, status = _dispatch_app_task(token, "delete_delivery", {"task_id": task_id})
            else:
                payload, status = delete_delivery_for_token(token, task_id)
            self._json_response(payload, status)
            return
        if not revoke_token(token):
            self._json_response({"ok": False, "error": "unauthorized"}, 401)
        else:
            self._json_response({"ok": True})

    def _handle_app_compress(self):
        authorization = self.headers.get("Authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        _, auth_error, auth_status = _authorize_app_token(token)
        if auth_error:
            self._json_response(auth_error, auth_status)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        max_length = 512 * 1024 * 1024
        if length <= 0 or length > max_length:
            self._json_response({"ok": False, "error": "invalid_file_size"}, 413)
            return
        filename = self.headers.get("X-File-Name", "archivo.bin")
        temp_path = None
        result_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="app-compress-", suffix=".input", delete=False) as handle:
                temp_path = Path(handle.name)
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("incomplete_upload")
                    handle.write(chunk)
                    remaining -= len(chunk)
            result_path, result_name = _compress_app_upload(temp_path, filename)
            payload_size = result_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(result_name)[0] or "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{result_name}"')
            self.send_header("X-Original-Size", str(length))
            self.send_header("X-Compressed-Size", str(payload_size))
            self.send_header("Content-Length", str(payload_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with result_path.open("rb") as output:
                shutil.copyfileobj(output, self.wfile, length=1024 * 1024)
        except (ValueError, OSError, RuntimeError):
            self._json_response({"ok": False, "error": "compression_failed"}, 422)
        finally:
            if result_path is not None and result_path != temp_path:
                result_path.unlink(missing_ok=True)
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _json_response(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def log_message(self, format, *args):
        # Mantener los logs de Railway útiles sin registrar parámetros privados.
        print(f"[web] {self.address_string()} - {format % args}", flush=True)

    def end_headers(self):  # noqa: N802
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; media-src 'self'; "
            "connect-src 'self'; base-uri 'self'; form-action 'self'",
        )
        super().end_headers()


def start_store_server(
    profile_checker: Callable[[int], dict] | None = None,
    profile_photo_checker: Callable[[int], bytes | None] | None = None,
) -> Thread:
    global _PROFILE_CHECKER, _PROFILE_PHOTO_CHECKER
    _PROFILE_CHECKER = profile_checker
    _PROFILE_PHOTO_CHECKER = profile_photo_checker
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), StoreHandler)
    thread = Thread(target=server.serve_forever, name="todus-store-http", daemon=True)
    thread.start()
    print(f"Tienda ToDus S3 disponible en el puerto {port}", flush=True)
    return thread
