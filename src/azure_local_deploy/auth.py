"""Authentication & user management module.

Provides JWT-based auth, API key auth, user CRUD, RBAC, and password policy.
On first start the default admin user (admin / admin123) is created.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import bcrypt
from flask import Flask, Request, current_app, g, jsonify, request

try:
    import jwt as pyjwt  # PyJWT
except ImportError:  # pragma: no cover
    pyjwt = None  # type: ignore[assignment]

from azure_local_deploy.models import APIKey, User, UserRole
from azure_local_deploy.utils import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_DIR = Path.home() / ".azure-local-deploy"
USERS_FILE = DATA_DIR / "users.json"
APIKEYS_FILE = DATA_DIR / "api_keys.json"
JWT_SECRET_FILE = DATA_DIR / "jwt_secret.key"
TOKEN_BLACKLIST_FILE = DATA_DIR / "token_blacklist.json"

DEFAULT_ACCESS_TTL = 3600       # 1 hour
DEFAULT_REFRESH_TTL = 604800    # 7 days
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = 900          # 15 min
PASSWORD_MIN_LENGTH = 12
PASSWORD_HISTORY_SIZE = 5
BCRYPT_ROUNDS = 12

# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def check_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def validate_password_strength(password: str) -> list[str]:
    """Return list of policy violations (empty = OK)."""
    errors: list[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    if not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        errors.append("Password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain at least one digit")
    if not any(c in "!@#$%^&*()-_=+[]{}|;:',.<>?/`~" for c in password):
        errors.append("Password must contain at least one special character")
    return errors


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

_file_lock = threading.Lock()


def _get_jwt_secret() -> str:
    """Load or create the JWT signing secret (with restrictive perms)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if JWT_SECRET_FILE.exists():
        return JWT_SECRET_FILE.read_text().strip()
    secret = secrets.token_hex(64)
    JWT_SECRET_FILE.write_text(secret)
    # Restrict to owner only on non-Windows
    try:
        JWT_SECRET_FILE.chmod(0o600)
    except OSError:
        pass
    return secret


def create_access_token(
    user_id: int, username: str, role: str, ttl: int = DEFAULT_ACCESS_TTL,
) -> str:
    """Create a JWT access token."""
    if pyjwt is None:
        raise RuntimeError("PyJWT not installed – pip install pyjwt")
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl,
        "jti": secrets.token_hex(16),
        "type": "access",
    }
    return pyjwt.encode(payload, _get_jwt_secret(), algorithm="HS256")


def create_refresh_token(
    user_id: int, username: str, role: str, ttl: int = DEFAULT_REFRESH_TTL,
) -> str:
    """Create a JWT refresh token."""
    if pyjwt is None:
        raise RuntimeError("PyJWT not installed – pip install pyjwt")
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl,
        "jti": secrets.token_hex(16),
        "type": "refresh",
    }
    return pyjwt.encode(payload, _get_jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token.  Raises on expiry or bad signature."""
    if pyjwt is None:
        raise RuntimeError("PyJWT not installed")
    return pyjwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])


# ---------------------------------------------------------------------------
# Token blacklist (simple file-based)
# ---------------------------------------------------------------------------

def _load_blacklist() -> set[str]:
    if TOKEN_BLACKLIST_FILE.exists():
        data = json.loads(TOKEN_BLACKLIST_FILE.read_text())
        # Prune expired entries
        now = time.time()
        return {jti for jti, exp in data.items() if exp > now}
    return set()


def _save_blacklist(bl: dict[str, float]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_BLACKLIST_FILE.write_text(json.dumps(bl))


def blacklist_token(jti: str, exp: float) -> None:
    """Add a token's JTI to the blacklist."""
    with _file_lock:
        if TOKEN_BLACKLIST_FILE.exists():
            data = json.loads(TOKEN_BLACKLIST_FILE.read_text())
        else:
            data = {}
        data[jti] = exp
        _save_blacklist(data)


def is_token_blacklisted(jti: str) -> bool:
    bl = _load_blacklist()
    return jti in bl


# ---------------------------------------------------------------------------
# User store (file-based JSON)
# ---------------------------------------------------------------------------

class UserStore:
    """Simple file-based user store.  Thread-safe via re-reading on each op."""

    def __init__(self, path: Path = USERS_FILE):
        self._path = path
        self._ensure_default_admin()

    def _ensure_default_admin(self) -> None:
        """Create the default admin user if the store is empty."""
        users = self._load()
        if not users:
            admin = User(
                id=1,
                username="admin",
                password_hash=hash_password("admin123"),
                role=UserRole.ADMIN,
                must_change_password=True,
                created_at=datetime.utcnow(),
            )
            self._save([_user_to_dict(admin)])
            log.info("Default admin user created (admin / admin123)")

    # -- persistence -------------------------------------------------------

    def _load(self) -> list[dict]:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            return json.loads(self._path.read_text())
        return []

    def _save(self, data: list[dict]) -> None:
        with _file_lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, indent=2, default=str))

    # -- CRUD --------------------------------------------------------------

    def get_all(self) -> list[User]:
        return [_dict_to_user(d) for d in self._load()]

    def get_by_id(self, user_id: int) -> User | None:
        for d in self._load():
            if d["id"] == user_id:
                return _dict_to_user(d)
        return None

    def get_by_username(self, username: str) -> User | None:
        for d in self._load():
            if d["username"] == username:
                return _dict_to_user(d)
        return None

    def create(self, username: str, password: str, role: UserRole = UserRole.OPERATOR) -> User:
        users = self._load()
        # Prevent duplicate usernames
        if any(u["username"] == username for u in users):
            raise ValueError(f"Username '{username}' already exists")
        max_id = max((u["id"] for u in users), default=0)
        user = User(
            id=max_id + 1,
            username=username,
            password_hash=hash_password(password),
            role=role,
            must_change_password=True,
            created_at=datetime.utcnow(),
        )
        users.append(_user_to_dict(user))
        self._save(users)
        return user

    def update(self, user: User) -> None:
        users = self._load()
        for i, d in enumerate(users):
            if d["id"] == user.id:
                users[i] = _user_to_dict(user)
                break
        self._save(users)

    def delete(self, user_id: int) -> bool:
        users = self._load()
        new_users = [u for u in users if u["id"] != user_id]
        if len(new_users) == len(users):
            return False
        self._save(new_users)
        return True

    def authenticate(self, username: str, password: str) -> User | None:
        """Verify credentials and return user, or None.  Handles lockout."""
        user = self.get_by_username(username)
        if user is None:
            return None

        # Check lockout
        if user.locked_until and datetime.utcnow() < user.locked_until:
            return None

        if not check_password(password, user.password_hash):
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = datetime.utcnow() + timedelta(seconds=LOCKOUT_DURATION)
            self.update(user)
            return None

        # Successful login – reset lockout
        user.failed_attempts = 0
        user.locked_until = None
        user.last_login = datetime.utcnow()
        self.update(user)
        return user


# ---------------------------------------------------------------------------
# API Key store
# ---------------------------------------------------------------------------

class APIKeyStore:
    """File-based API key store."""

    def __init__(self, path: Path = APIKEYS_FILE):
        self._path = path

    def _load(self) -> list[dict]:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            return json.loads(self._path.read_text())
        return []

    def _save(self, data: list[dict]) -> None:
        with _file_lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, indent=2, default=str))

    def get_all(self) -> list[APIKey]:
        """Return all API keys (without exposing raw keys)."""
        return [_dict_to_apikey(d) for d in self._load()]

    def create(self, user_id: int, name: str,
               permissions: list[str] | None = None,
               expires_days: int | None = 90) -> tuple[str, APIKey]:
        """Create a new API key. Returns (full_key, api_key_obj)."""
        full_key, key_hash = APIKey.generate_key()
        key = APIKey(
            id=f"ak-{secrets.token_hex(8)}",
            user_id=user_id,
            name=name,
            key_hash=key_hash,
            permissions=permissions or ["rebuild:read", "rebuild:execute"],
            expires_at=datetime.utcnow() + timedelta(days=expires_days) if expires_days else None,
            created_at=datetime.utcnow(),
        )
        keys = self._load()
        keys.append(_apikey_to_dict(key))
        self._save(keys)
        return full_key, key

    def validate(self, full_key: str) -> APIKey | None:
        """Look up an API key by its full value.  Returns None if invalid/expired."""
        key_hash = hashlib.sha256(full_key.encode()).hexdigest()
        for d in self._load():
            if d["key_hash"] == key_hash and d.get("is_active", True):
                # Check expiry
                if d.get("expires_at"):
                    try:
                        exp = datetime.fromisoformat(d["expires_at"])
                        if datetime.utcnow() > exp:
                            return None
                    except (ValueError, TypeError):
                        pass
                return _dict_to_apikey(d)
        return None

    def get_by_user(self, user_id: int) -> list[APIKey]:
        return [_dict_to_apikey(d) for d in self._load() if d["user_id"] == user_id]

    def revoke(self, key_id: str) -> bool:
        keys = self._load()
        for d in keys:
            if d["id"] == key_id:
                d["is_active"] = False
                self._save(keys)
                return True
        return False


# ---------------------------------------------------------------------------
# Flask auth middleware
# ---------------------------------------------------------------------------

def init_auth(app: Flask) -> None:
    """Attach the auth system to a Flask app.  Stores UserStore + APIKeyStore on app."""
    app.config["USER_STORE"] = UserStore()
    app.config["APIKEY_STORE"] = APIKeyStore()

    @app.before_request
    def _auth_middleware():
        # Skip auth for health check and login
        if request.path in ("/api/v1/health",):
            return
        if request.path == "/api/v1/auth/login" and request.method == "POST":
            return
        # Skip auth for non-API routes (web wizard uses session)
        if not request.path.startswith("/api/"):
            return

        user = _extract_user_from_request(app)
        if user is None:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        g.current_user = user


def _extract_user_from_request(app: Flask) -> User | None:
    """Try JWT, then API key, return User or None."""
    auth_header = request.headers.get("Authorization", "")
    api_key_header = request.headers.get("X-API-Key", "")

    # JWT Bearer token
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            payload = decode_token(token)
            if is_token_blacklisted(payload.get("jti", "")):
                return None
            user_store: UserStore = app.config["USER_STORE"]
            return user_store.get_by_id(int(payload["sub"]))
        except Exception:
            return None

    # API Key
    if api_key_header:
        key_store: APIKeyStore = app.config["APIKEY_STORE"]
        api_key = key_store.validate(api_key_header)
        if api_key is None:
            return None
        user_store = app.config["USER_STORE"]
        return user_store.get_by_id(api_key.user_id)

    return None


def require_role(*roles: UserRole) -> Callable:
    """Decorator to enforce RBAC on API endpoints."""
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            user: User | None = getattr(g, "current_user", None)
            if user is None:
                return jsonify({"status": "error", "message": "Unauthorized"}), 401
            user_role = user.role if isinstance(user.role, UserRole) else UserRole(user.role)
            if user_role not in roles:
                return jsonify({"status": "error", "message": "Forbidden"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# HMAC webhook signing
# ---------------------------------------------------------------------------

def sign_webhook_payload(payload: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 of a webhook payload."""
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "password_hash": u.password_hash,
        "role": u.role.value if isinstance(u.role, UserRole) else u.role,
        "must_change_password": u.must_change_password,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login": u.last_login.isoformat() if u.last_login else None,
        "is_active": u.is_active,
        "failed_attempts": u.failed_attempts,
        "locked_until": u.locked_until.isoformat() if u.locked_until else None,
        "password_history": u.password_history,
    }


def _dict_to_user(d: dict) -> User:
    role = d.get("role", "operator")
    try:
        role = UserRole(role)
    except ValueError:
        role = UserRole.OPERATOR
    return User(
        id=d["id"],
        username=d["username"],
        password_hash=d["password_hash"],
        role=role,
        must_change_password=d.get("must_change_password", False),
        created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.utcnow(),
        last_login=datetime.fromisoformat(d["last_login"]) if d.get("last_login") else None,
        is_active=d.get("is_active", True),
        failed_attempts=d.get("failed_attempts", 0),
        locked_until=datetime.fromisoformat(d["locked_until"]) if d.get("locked_until") else None,
        password_history=d.get("password_history", []),
    )


def _apikey_to_dict(k: APIKey) -> dict:
    return {
        "id": k.id,
        "user_id": k.user_id,
        "name": k.name,
        "key_hash": k.key_hash,
        "permissions": k.permissions,
        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "last_used": k.last_used.isoformat() if k.last_used else None,
        "is_active": k.is_active,
    }


def _dict_to_apikey(d: dict) -> APIKey:
    return APIKey(
        id=d["id"],
        user_id=d["user_id"],
        name=d["name"],
        key_hash=d["key_hash"],
        permissions=d.get("permissions", []),
        expires_at=datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None,
        created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.utcnow(),
        last_used=datetime.fromisoformat(d["last_used"]) if d.get("last_used") else None,
        is_active=d.get("is_active", True),
    )
