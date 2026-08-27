import hashlib
import json
import secrets
import threading
import time
import urllib.request
import uuid
from typing import Any

from redis import Redis

from .config import (
    ADMIN_TOKEN,
    BANK_TRANSFER_IBAN,
    BANK_TRANSFER_RECIPIENT,
    CONTACT_EMAIL,
    EMAIL_FROM,
    GUEST_TRIAL_MAX_MINUTES,
    INSTAGRAM_ACCOUNT,
    INSTAGRAM_BONUS_MINUTES,
    REDIS_URL,
    RESEND_API_KEY,
    WORK_DIR,
)


PLANS = {
    "starter": {
        "name": "Starter",
        "minutes": 120,
        "monthly": {"TRY": 149, "USD": 5, "EUR": 5, "GBP": 4},
        "yearly": {"TRY": 1490, "USD": 50, "EUR": 50, "GBP": 40},
    },
    "pro": {
        "name": "Pro",
        "minutes": 600,
        "monthly": {"TRY": 399, "USD": 13, "EUR": 12, "GBP": 10},
        "yearly": {"TRY": 3990, "USD": 130, "EUR": 120, "GBP": 100},
    },
    "max": {
        "name": "Max",
        "minutes": 1800,
        "monthly": {"TRY": 799, "USD": 26, "EUR": 24, "GBP": 20},
        "yearly": {"TRY": 7990, "USD": 260, "EUR": 240, "GBP": 200},
    },
}


def _now() -> float:
    return time.time()


def _normalize_email(value: str) -> str:
    return value.strip().casefold()


def _safe_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("name", ""),
        "preferred_language": user.get("preferred_language", "tr"),
        "minutes_balance": round(float(user.get("minutes_balance", 0)), 2),
        "plan": user.get("plan", "free"),
        "instagram_bonus_claimed": bool(user.get("instagram_bonus_claimed")),
        "created": user.get("created"),
    }


class PlatformStore:
    REDIS_KEY = "lecturesift:platform:v1"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state_path = WORK_DIR / "platform-state.json"
        self._redis = Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
        self._state = self._empty()
        self._load()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"users": {}, "sessions": {}, "codes": {}, "orders": {}, "guests": {}, "rewards": {}, "stats": []}

    def _load(self) -> None:
        text = ""
        if self._redis is not None:
            try:
                text = self._redis.get(self.REDIS_KEY) or ""
            except Exception:
                text = ""
        if not text and self._state_path.exists():
            try:
                text = self._state_path.read_text(encoding="utf-8")
            except OSError:
                text = ""
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    self._state.update(data)
            except Exception:
                pass

    def _refresh(self) -> None:
        if self._redis is None:
            return
        try:
            text = self._redis.get(self.REDIS_KEY) or ""
            if text:
                data = json.loads(text)
                if isinstance(data, dict):
                    self._state = self._empty()
                    self._state.update(data)
        except Exception:
            pass

    def _flush(self) -> None:
        text = json.dumps(self._state, ensure_ascii=False, separators=(",", ":"))
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._state_path)
        if self._redis is not None:
            self._redis.set(self.REDIS_KEY, text)

    def _send_code(self, email: str, code: str, purpose: str) -> None:
        if not RESEND_API_KEY:
            return
        subject = "LectureSift doğrulama kodun"
        body = f"LectureSift {purpose} doğrulama kodun: {code}. Kod 10 dakika geçerlidir."
        payload = json.dumps({"from": EMAIL_FROM, "to": [email], "subject": subject, "text": body}).encode()
        request = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10).read()
        except Exception:
            pass

    def request_code(self, email: str, purpose: str = "login", user_token: str = "") -> dict:
        email = _normalize_email(email)
        if "@" not in email:
            raise ValueError("Geçerli bir e-posta adresi gir.")
        with self._lock:
            self._refresh()
            if purpose == "email_change":
                current = self.user_from_token(user_token)
                existing = self._state["users"].get(email)
                if existing and existing.get("id") != current.get("id"):
                    raise ValueError("Bu e-posta adresi başka bir LectureSift hesabında kullanılıyor.")
            code = f"{secrets.randbelow(1000000):06d}"
            self._state["codes"][email] = {"code": code, "purpose": purpose, "expires": _now() + 600, "user_token": user_token}
            self._flush()
        self._send_code(email, code, purpose)
        return {"ok": True, "delivery": "email" if RESEND_API_KEY else "development", "development_code": code if not RESEND_API_KEY else None}

    def verify_code(self, email: str, code: str, name: str = "") -> dict:
        email = _normalize_email(email)
        with self._lock:
            self._refresh()
            record = self._state["codes"].get(email)
            if not record or record.get("code") != code or float(record.get("expires", 0)) < _now():
                raise ValueError("Doğrulama kodu geçersiz veya süresi dolmuş.")
            purpose = record.get("purpose", "login")
            if purpose == "email_change":
                token = str(record.get("user_token", ""))
                user = self.user_from_token(token)
                old = user["email"]
                existing = self._state["users"].get(email)
                if existing and existing.get("id") != user.get("id"):
                    raise ValueError("Bu e-posta adresi başka bir LectureSift hesabında kullanılıyor.")
                user["email"] = email
                self._state["users"].pop(old, None)
                self._state["users"][email] = user
                if token in self._state["sessions"]:
                    self._state["sessions"][token]["email"] = email
                self._state["codes"].pop(email, None)
                self._flush()
                return {"token": token, "user": _safe_user(user)}
            user = self._state["users"].get(email)
            if not user:
                user = {"id": str(uuid.uuid4()), "email": email, "name": name.strip(), "preferred_language": "tr", "minutes_balance": 0.0, "plan": "free", "created": _now()}
                self._state["users"][email] = user
            elif name.strip() and not user.get("name"):
                user["name"] = name.strip()
            token = secrets.token_urlsafe(32)
            self._state["sessions"][token] = {"email": email, "created": _now()}
            self._state["codes"].pop(email, None)
            self._flush()
            return {"token": token, "user": _safe_user(user)}

    def user_from_token(self, token: str) -> dict[str, Any]:
        if not token:
            raise ValueError("Oturum gerekli.")
        self._refresh()
        session = self._state["sessions"].get(token)
        if not session:
            raise ValueError("Oturum geçersiz.")
        user = self._state["users"].get(session.get("email", ""))
        if not user:
            raise ValueError("Kullanıcı bulunamadı.")
        return user

    def me(self, token: str) -> dict:
        with self._lock:
            return _safe_user(self.user_from_token(token))

    def update_profile(self, token: str, name: str, preferred_language: str) -> dict:
        with self._lock:
            self._refresh()
            user = self.user_from_token(token)
            user["name"] = name.strip()[:100]
            if preferred_language:
                user["preferred_language"] = preferred_language[:10]
            self._flush()
            return _safe_user(user)

    def authorize_minutes(self, token: str, guest_key: str, minutes: float) -> dict:
        minutes = max(0.1, float(minutes))
        with self._lock:
            self._refresh()
            if token:
                user = self.user_from_token(token)
                balance = float(user.get("minutes_balance", 0))
                if balance < minutes:
                    raise ValueError(f"Yetersiz dakika. Gerekli: {minutes:.1f}, mevcut: {balance:.1f}.")
                user["minutes_balance"] = round(balance - minutes, 2)
                self._flush()
                return {"mode": "account", "remaining": user["minutes_balance"]}
            if minutes > GUEST_TRIAL_MAX_MINUTES + 0.05:
                raise ValueError(f"Hesapsız deneme en fazla {GUEST_TRIAL_MAX_MINUTES:g} dakikadır.")
            key = hashlib.sha256(guest_key.encode()).hexdigest() if guest_key else ""
            if not key:
                raise ValueError("Misafir oturumu oluşturulamadı.")
            if self._state["guests"].get(key, {}).get("used"):
                raise ValueError("Hesapsız deneme hakkı bu cihaz/ağ için daha önce kullanılmış.")
            self._state["guests"][key] = {"used": True, "minutes": minutes, "created": _now()}
            self._flush()
            return {"mode": "guest", "remaining": 0}

    def prices(self, currency: str) -> dict:
        currency = currency.upper() if currency.upper() in {"TRY", "USD", "EUR", "GBP"} else "TRY"
        return {"currency": currency, "plans": [{"id": key, "name": value["name"], "minutes": value["minutes"], "monthly": value["monthly"][currency], "yearly": value["yearly"][currency]} for key, value in PLANS.items()]}

    def create_order(self, token: str, plan_id: str, cycle: str, currency: str) -> dict:
        with self._lock:
            self._refresh()
            user = self.user_from_token(token)
            if plan_id not in PLANS:
                raise ValueError("Plan bulunamadı.")
            cycle = "yearly" if cycle == "yearly" else "monthly"
            currency = currency.upper() if currency.upper() in {"TRY", "USD", "EUR", "GBP"} else "TRY"
            plan = PLANS[plan_id]
            order_no = f"LS-{time.strftime('%Y%m%d')}-{secrets.randbelow(900000)+100000}"
            order = {"order_no": order_no, "user_id": user["id"], "email": user["email"], "plan_id": plan_id, "plan_name": plan["name"], "cycle": cycle, "currency": currency, "amount": plan[cycle][currency], "minutes": plan["minutes"] * (12 if cycle == "yearly" else 1), "status": "pending_transfer", "created": _now()}
            self._state["orders"][order_no] = order
            self._flush()
            return {**order, "bank": {"iban": BANK_TRANSFER_IBAN, "recipient": BANK_TRANSFER_RECIPIENT}, "transfer_note": order_no}

    def list_orders(self, token: str) -> list[dict]:
        user = self.user_from_token(token)
        return sorted([dict(item) for item in self._state["orders"].values() if item.get("user_id") == user["id"]], key=lambda x: x.get("created", 0), reverse=True)

    def require_admin(self, admin_token: str) -> None:
        if not ADMIN_TOKEN or not secrets.compare_digest(admin_token or "", ADMIN_TOKEN):
            raise PermissionError("Admin yetkisi gerekli.")

    def admin_orders(self, admin_token: str, status: str = "pending_transfer") -> list[dict]:
        self.require_admin(admin_token)
        self._refresh()
        values = [dict(item) for item in self._state["orders"].values() if not status or item.get("status") == status]
        return sorted(values, key=lambda x: x.get("created", 0), reverse=True)

    def decide_order(self, admin_token: str, order_no: str, approve: bool) -> dict:
        self.require_admin(admin_token)
        with self._lock:
            self._refresh()
            order = self._state["orders"].get(order_no)
            if not order:
                raise ValueError("Sipariş bulunamadı.")
            if order.get("status") == "approved":
                return dict(order)
            order["status"] = "approved" if approve else "rejected"
            order["decided"] = _now()
            if approve:
                user = next((u for u in self._state["users"].values() if u.get("id") == order.get("user_id")), None)
                if user:
                    user["minutes_balance"] = round(float(user.get("minutes_balance", 0)) + float(order.get("minutes", 0)), 2)
                    user["plan"] = order.get("plan_id", "free")
            self._flush()
            return dict(order)

    def claim_instagram(self, token: str, handle: str) -> dict:
        handle = handle.strip().lstrip("@")[:100]
        if not handle:
            raise ValueError("Instagram kullanıcı adını gir.")
        with self._lock:
            self._refresh()
            user = self.user_from_token(token)
            if user.get("instagram_bonus_claimed"):
                raise ValueError("Instagram bonusu daha önce kullanılmış.")
            if any(item.get("user_id") == user["id"] and item.get("status") == "pending_verification" for item in self._state["rewards"].values()):
                raise ValueError("Instagram bonus talebin zaten doğrulama bekliyor.")
            reward_id = str(uuid.uuid4())
            reward = {"id": reward_id, "user_id": user["id"], "email": user["email"], "handle": handle, "account": INSTAGRAM_ACCOUNT, "minutes": INSTAGRAM_BONUS_MINUTES, "status": "pending_verification", "created": _now()}
            self._state["rewards"][reward_id] = reward
            self._flush()
            return reward

    def admin_rewards(self, admin_token: str) -> list[dict]:
        self.require_admin(admin_token)
        self._refresh()
        return sorted([dict(x) for x in self._state["rewards"].values()], key=lambda x: x.get("created", 0), reverse=True)

    def decide_reward(self, admin_token: str, reward_id: str, approve: bool) -> dict:
        self.require_admin(admin_token)
        with self._lock:
            self._refresh()
            reward = self._state["rewards"].get(reward_id)
            if not reward:
                raise ValueError("Bonus talebi bulunamadı.")
            if reward.get("status") == "approved":
                return dict(reward)
            reward["status"] = "approved" if approve else "rejected"
            reward["decided"] = _now()
            if approve:
                user = next((u for u in self._state["users"].values() if u.get("id") == reward.get("user_id")), None)
                if user and not user.get("instagram_bonus_claimed"):
                    user["minutes_balance"] = round(float(user.get("minutes_balance", 0)) + INSTAGRAM_BONUS_MINUTES, 2)
                    user["instagram_bonus_claimed"] = True
            self._flush()
            return dict(reward)

    def record_job_speed(self, media_minutes: float, elapsed_seconds: float, bytes_size: int) -> None:
        with self._lock:
            self._refresh()
            self._state["stats"].append({"media_minutes": media_minutes, "elapsed_seconds": elapsed_seconds, "bytes": bytes_size, "created": _now()})
            self._state["stats"] = self._state["stats"][-100:]
            self._flush()

    def eta_seconds(self, media_minutes: float, bytes_size: int, upload_bps: float = 0) -> int:
        self._refresh()
        processing_ratio = 45.0
        ratios = [float(x.get("elapsed_seconds", 0)) / max(float(x.get("media_minutes", 0)), 0.1) for x in self._state.get("stats", []) if x.get("elapsed_seconds") and x.get("media_minutes")]
        if ratios:
            ratios.sort()
            processing_ratio = ratios[len(ratios) // 2]
        processing = media_minutes * processing_ratio
        upload = bytes_size / upload_bps if upload_bps and upload_bps > 0 else 0
        return max(30, int(processing + upload))


PLATFORM = PlatformStore()

PUBLIC_PLATFORM_CONFIG = {
    "contact_email": CONTACT_EMAIL,
    "guest_trial_minutes": GUEST_TRIAL_MAX_MINUTES,
    "instagram_bonus_minutes": INSTAGRAM_BONUS_MINUTES,
    "instagram_account": INSTAGRAM_ACCOUNT,
    "bank_transfer_available": bool(BANK_TRANSFER_IBAN and BANK_TRANSFER_RECIPIENT),
}
