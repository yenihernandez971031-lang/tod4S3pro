"""Independent AiTod4S3 app accounts and bearer sessions.

Passwords are stored only as PBKDF2-HMAC-SHA256 verifiers. Session tokens are
random bearer secrets; only their SHA-256 digests are persisted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

_ITERATIONS = 600_000
_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
_LOCK = threading.RLock()
_STORAGE_PATH = Path(os.getenv("AI_ACCOUNTS_FILE", "/data/ai_app_accounts.json"))
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_DEVICE_ID_RE = re.compile(r"^[a-fA-F0-9]{16}$")


def configure_storage(path: str | Path) -> None:
    global _STORAGE_PATH
    with _LOCK:
        _STORAGE_PATH = Path(path)


def validate_credentials(username: object, password: object) -> tuple[str, str]:
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError("invalid_credentials")
    username = username.strip()
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError("invalid_username")
    if len(password) < 10 or len(password) > 256 or any(ord(c) < 32 for c in password):
        raise ValueError("invalid_password")
    return username, password


def _load() -> dict:
    try:
        data = json.loads(_STORAGE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"users": {}, "sessions": {}}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("ai_account_storage_unavailable") from exc
    if not isinstance(data, dict) or not isinstance(data.get("users", {}), dict) or not isinstance(data.get("sessions", {}), dict):
        raise RuntimeError("ai_account_storage_unavailable")
    data.setdefault("users", {})
    data.setdefault("sessions", {})
    return data


def _save(data: dict) -> None:
    parent = _STORAGE_PATH.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".ai-accounts-", dir=str(parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, _STORAGE_PATH)
        try:
            os.chmod(_STORAGE_PATH, 0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS, dklen=32)


def create_or_update_account(username: object, password: object) -> None:
    username, password = validate_credentials(username, password)
    salt = secrets.token_bytes(16)
    verifier = _password_hash(password, salt)
    with _LOCK:
        data = _load()
        previous = data["users"].get(username, {})
        row = {
            "salt": salt.hex(), "password_hash": verifier.hex(),
            "iterations": _ITERATIONS, "active": True,
            "updated_at": int(time.time()),
        }
        if isinstance(previous, dict) and previous.get("device_hash"):
            row["device_hash"] = previous["device_hash"]
        data["users"][username] = row
        pending = data.setdefault("pending", {})
        if isinstance(pending, dict):
            pending.pop(username, None)
        # Replacing credentials invalidates all existing sessions for that user.
        data["sessions"] = {key: row for key, row in data["sessions"].items() if row.get("username") != username}
        _save(data)


def request_account(username: object, password: object, device_id: object) -> None:
    """Store a password verifier pending administrator approval."""
    username, password = validate_credentials(username, password)
    device_hash = _device_digest(device_id)
    if device_hash is None:
        raise ValueError("invalid_device")
    salt = secrets.token_bytes(16)
    verifier = _password_hash(password, salt)
    with _LOCK:
        data = _load()
        existing = data["users"].get(username)
        if isinstance(existing, dict) and existing.get("active"):
            raise ValueError("account_exists")
        pending = data.setdefault("pending", {})
        if not isinstance(pending, dict):
            raise RuntimeError("ai_account_storage_unavailable")
        if username in pending:
            raise ValueError("request_exists")
        pending[username] = {
            "salt": salt.hex(), "password_hash": verifier.hex(),
            "iterations": _ITERATIONS, "device_hash": device_hash,
            "requested_at": int(time.time()),
        }
        _save(data)


def pending_accounts() -> list[dict[str, object]]:
    """Return safe request metadata, never password material."""
    with _LOCK:
        data = _load()
        pending = data.setdefault("pending", {})
        if not isinstance(pending, dict):
            raise RuntimeError("ai_account_storage_unavailable")
        return [
            {"username": str(username), "requested_at": int(row.get("requested_at", 0))}
            for username, row in sorted(pending.items())
            if isinstance(row, dict)
        ]


def approve_account_request(username: object) -> bool:
    """Promote one pending request to an active account."""
    if not isinstance(username, str) or not _USERNAME_RE.fullmatch(username.strip()):
        return False
    username = username.strip()
    with _LOCK:
        data = _load()
        pending = data.setdefault("pending", {})
        row = pending.get(username) if isinstance(pending, dict) else None
        if not isinstance(row, dict):
            return False
        data["users"][username] = {
            "salt": row["salt"], "password_hash": row["password_hash"],
            "iterations": row["iterations"], "active": True,
            "device_hash": row["device_hash"], "updated_at": int(time.time()),
        }
        pending.pop(username, None)
        data["sessions"] = {key: item for key, item in data["sessions"].items() if item.get("username") != username}
        _save(data)
        return True


def reject_account_request(username: object) -> bool:
    """Delete one pending request without retaining its password verifier."""
    if not isinstance(username, str):
        return False
    with _LOCK:
        data = _load()
        pending = data.setdefault("pending", {})
        if not isinstance(pending, dict) or username not in pending:
            return False
        pending.pop(username, None)
        _save(data)
        return True


def deactivate_account(username: object) -> bool:
    if not isinstance(username, str):
        return False
    username = username.strip()
    with _LOCK:
        data = _load()
        row = data["users"].get(username)
        if not isinstance(row, dict) or not row.get("active"):
            return False
        row["active"] = False
        row.pop("device_hash", None)
        row["updated_at"] = int(time.time())
        pending = data.setdefault("pending", {})
        if isinstance(pending, dict):
            pending.pop(username, None)
        data["sessions"] = {key: item for key, item in data["sessions"].items() if item.get("username") != username}
        _save(data)
        return True


def authenticate(username: object, password: object) -> bool:
    try:
        username, password = validate_credentials(username, password)
    except ValueError:
        # Consume comparable hash work for malformed/unknown identifiers.
        _password_hash("invalid", b"\x00" * 16)
        return False
    with _LOCK:
        data = _load()
        row = data["users"].get(username)
        if not isinstance(row, dict) or not row.get("active"):
            salt = b"\x00" * 16
            expected = _password_hash("invalid", salt)
            candidate = _password_hash(password, salt)
            hmac.compare_digest(candidate, expected)
            return False
        try:
            salt = bytes.fromhex(row["salt"])
            expected = bytes.fromhex(row["password_hash"])
            iterations = int(row["iterations"])
            if len(salt) != 16 or len(expected) != 32 or not 100_000 <= iterations <= 2_000_000:
                raise ValueError("invalid_verifier")
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("ai_account_storage_unavailable") from exc
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
        return hmac.compare_digest(candidate, expected)


def _device_digest(device_id: object) -> str | None:
    if not isinstance(device_id, str) or not _DEVICE_ID_RE.fullmatch(device_id):
        return None
    return hashlib.sha256(device_id.lower().encode("ascii")).hexdigest()


def bind_or_check_device(username: str, device_id: object) -> bool:
    """Bind the account on first login, then permit only that Android ID."""
    digest = _device_digest(device_id)
    if digest is None:
        return False
    with _LOCK:
        data = _load()
        account = data["users"].get(username)
        if not isinstance(account, dict) or not account.get("active"):
            return False
        existing = account.get("device_hash")
        if existing:
            return hmac.compare_digest(str(existing), digest)
        account["device_hash"] = digest
        account["updated_at"] = int(time.time())
        _save(data)
        return True


def release_account_device(username: object) -> bool:
    """Owner/admin recovery: unbind the Android and revoke every session."""
    if not isinstance(username, str):
        return False
    username = username.strip()
    with _LOCK:
        data = _load()
        account = data["users"].get(username)
        if not isinstance(account, dict) or not account.get("active"):
            return False
        was_bound = bool(account.pop("device_hash", None))
        data["sessions"] = {key: row for key, row in data["sessions"].items() if row.get("username") != username}
        if was_bound:
            account["updated_at"] = int(time.time())
            _save(data)
        return was_bound


def issue_session(username: str, device_id: object) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = int(time.time())
    expires = now + _TOKEN_TTL_SECONDS
    with _LOCK:
        data = _load()
        account = data["users"].get(username)
        digest_device = _device_digest(device_id)
        if not isinstance(account, dict) or not account.get("active"):
            raise ValueError("account_disabled")
        if digest_device is None or not account.get("device_hash") or not hmac.compare_digest(str(account["device_hash"]), digest_device):
            raise ValueError("device_mismatch")
        data["sessions"] = {
            key: row for key, row in data["sessions"].items()
            if isinstance(row, dict) and int(row.get("expires_at", 0)) > now
        }
        data["sessions"][digest] = {"username": username, "device_hash": digest_device, "expires_at": expires}
        _save(data)
    return token, expires


def resolve_session(token: object, device_id: object) -> str | None:
    if not isinstance(token, str) or not 20 <= len(token) <= 256:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    device_digest = _device_digest(device_id)
    if device_digest is None:
        return None
    now = int(time.time())
    with _LOCK:
        data = _load()
        session = data["sessions"].get(digest)
        if not isinstance(session, dict) or int(session.get("expires_at", 0)) <= now:
            return None
        username = session.get("username")
        account = data["users"].get(username)
        if not isinstance(account, dict) or not account.get("active"):
            return None
        if not hmac.compare_digest(str(session.get("device_hash", "")), device_digest):
            return None
        if not hmac.compare_digest(str(account.get("device_hash", "")), device_digest):
            return None
        return str(username)


def revoke_session(token: object) -> bool:
    if not isinstance(token, str) or not 20 <= len(token) <= 256:
        return False
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _LOCK:
        data = _load()
        if digest not in data["sessions"]:
            return False
        data["sessions"].pop(digest, None)
        _save(data)
        return True
