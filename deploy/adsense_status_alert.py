#!/usr/bin/env python3
"""Email the operators when the LectureSift AdSense status changes.

lecturesift-adsense-status.timer starts lecturesift-adsense-status.service
once a day at 03:10 UTC (06:10 in Turkey). The service runs this script on
the host, which reads the status inside the running api container:

    docker compose exec -T api python -m lecturesift.adsense_status_snapshot

That module uses the read-only AdSense OAuth credentials that already live in
the api role's environment and prints a small JSON summary. The credentials
never reach the host process, and no new environment file holds them.

The summary is compared with the snapshot saved in
/var/lib/lecturesift/adsense-status/state.json: site state, account state,
alert types and their severities, the number of policy issues and auto ads.
When something changed, the operators get a short Turkish email through the
failure alert's sender (service_failure_alert.py): one message per address in
LECTURESIFT_OPS_ALERT_EMAIL and LECTURESIFT_ADMIN_EMAILS, through the Resend
or SMTP account in /etc/lecturesift/runtime.env.

The first successful check saves the snapshot and sends one baseline email.
A check that cannot read every compared field never emails a change. After
three such checks in a row, one "AdSense durumu okunamıyor" email goes out,
and it is sent again only after a successful check has reset the count.

The state file holds only the compared fields, one time and the failure
count. It never holds a credential, an id, an address or provider text, and
neither do the emails.

``--manual-test`` sends the operators one email with the current status even
when nothing changed. It leaves the saved state exactly as it was, except on
the very first run, where the snapshot it saves is the baseline the daily
check compares against.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - the production host is Linux
    fcntl = None  # type: ignore[assignment]


def _load_alert_mail() -> Any:
    # Reuse the failure alert's settings parser, recipient list and Resend or
    # SMTP sender instead of a third mail path. ``python3 -I`` leaves the
    # script directory off sys.path, so the root-owned sibling is loaded by
    # its exact path.
    path = Path(__file__).resolve().with_name("service_failure_alert.py")
    spec = importlib.util.spec_from_file_location("lecturesift_failure_alert_mail", path)
    if spec is None or spec.loader is None:
        raise ImportError("service_failure_alert.py is missing")
    module = importlib.util.module_from_spec(spec)
    # Leave no __pycache__ behind in the release tree.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


alert_mail = _load_alert_mail()

UNIT = "lecturesift-adsense-status.service"
SNAPSHOT_SCHEMA = "lecturesift-adsense-status-v1"
STATE_SCHEMA = "lecturesift-adsense-status-state-v1"
DEFAULT_STATE_DIR = Path("/var/lib/lecturesift/adsense-status")
STATE_NAME = "state.json"
LOCK_NAME = ".lock"
MAX_STATE_BYTES = 65536
SITE = "lecturesift.com"
ADMIN_URL = "https://lecturesift.com/admin.html"
ADSENSE_URL = "https://adsense.google.com/"
FAILURES_BEFORE_EMAIL = 3
# The reader inside the container stops itself after about 35 seconds. The
# unit's TimeoutStartSec=300 covers this, the 60-second delivery limit and
# saving the state.
READ_SECONDS = 90
MAX_OUTPUT_BYTES = 16384
PRODUCTION_ROOT = "/opt/lecturesift"
SNAPSHOT_COMMAND = (
    "/usr/bin/docker", "compose",
    "--project-directory", PRODUCTION_ROOT,
    "--file", f"{PRODUCTION_ROOT}/compose.yaml",
    "exec", "-T", "api",
    "python", "-m", "lecturesift.adsense_status_snapshot",
)
# Compose resolves the role files from its own defaults; nothing from the
# caller's environment is handed to Docker.
COMMAND_ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C.UTF-8"}

# The same values lecturesift.adsense_management accepts.
ACCOUNT_STATES = frozenset({"STATE_UNSPECIFIED", "READY", "NEEDS_ATTENTION", "CLOSED"})
SITE_STATES = frozenset(
    {"STATE_UNSPECIFIED", "REQUIRES_REVIEW", "GETTING_READY", "READY", "NEEDS_ATTENTION"}
)
SEVERITIES = frozenset({"SEVERITY_UNSPECIFIED", "INFO", "WARNING", "SEVERE"})
ALERT_TYPE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
MAX_ALERTS = 25
# The reader asks for at most 10000 policy issues and refuses a second page.
MAX_POLICY_ISSUES = 10000
FIELDS = ("site_state", "account_state", "alerts", "policy_issues", "auto_ads")
CHECKED_AT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

SITE_MEANINGS = {
    "READY": (
        "Hazır",
        "Google siteyi onayladı. Reklamların gerçekten gösterildiğini ve kazancı "
        "AdSense raporlarından doğrula.",
    ),
    "GETTING_READY": (
        "Hazırlanıyor",
        "Google siteyi inceliyor. Şimdilik bir şey yapman gerekmiyor; inceleme "
        "birkaç gün sürebilir.",
    ),
    "NEEDS_ATTENTION": (
        "İşlem gerekiyor",
        f"AdSense panelinde Siteler > {SITE} satırındaki sebebi kontrol et. "
        "Düzelttikten sonra aynı yerden incelemeyi yeniden iste.",
    ),
    "REQUIRES_REVIEW": (
        "İnceleme gerekiyor",
        f"AdSense panelinde Siteler > {SITE} satırından siteyi incelemeye gönder.",
    ),
    "STATE_UNSPECIFIED": (
        "Bilinmiyor",
        f"Google site durumunu bildirmedi. AdSense panelinde Siteler > {SITE} satırına bak.",
    ),
}
ACCOUNT_MEANINGS = {
    "READY": (
        "Hazır",
        "AdSense hesabı kullanıma hazır. Bu, sitenin onaylandığı anlamına gelmez; "
        "site durumu ayrıca gösterilir.",
    ),
    "NEEDS_ATTENTION": (
        "İşlem gerekiyor",
        "AdSense panelinin ana sayfasındaki uyarıları ve tamamlanmamış adımları kontrol et.",
    ),
    "CLOSED": (
        "Kapalı",
        "AdSense hesabı kapalı ve reklam gösterilmez. AdSense panelinde Hesap bölümüne bak.",
    ),
    "STATE_UNSPECIFIED": (
        "Bilinmiyor",
        "Google hesap durumunu bildirmedi. AdSense panelinin ana sayfasına bak.",
    ),
}
AUTO_ADS_MEANINGS = {
    True: (
        "Açık",
        "Otomatik reklamlar bu sitede açık. Bu, reklam gösterildiğini veya gelir "
        "oluştuğunu kanıtlamaz.",
    ),
    False: (
        "Kapalı",
        "Otomatik reklamlar bu sitede kapalı. Reklam göstermek istiyorsan AdSense "
        f"panelinde Reklamlar > Siteye göre > {SITE} satırından aç.",
    ),
    None: ("Bilinmiyor", "Google bu ayarı bildirmedi."),
}
SEVERITY_LABELS = {
    "INFO": "bilgi",
    "WARNING": "uyarı",
    "SEVERE": "ciddi",
    "SEVERITY_UNSPECIFIED": "belirsiz",
}
ALERT_MEANINGS = {
    "adsense-onboarding-incomplete": (
        "AdSense kurulumunda tamamlanmamış adımlar var. AdSense panelinin ana "
        "sayfasındaki adımları tamamla."
    ),
    "ua-conflict-policy-update": (
        "Ukrayna ile ilgili genel bir politika duyurusu. Tek başına bu siteye "
        "verilmiş bir ihlal kararı değil."
    ),
}
ALERT_DEFAULT_MEANING = (
    "Ayrıntısını AdSense panelinin ana sayfasındaki uyarılarda oku."
)
POLICY_MEANINGS = {
    True: (
        "AdSense panelinde Politika merkezini aç. Her sorunun hangi sayfayı "
        "etkilediğine ve gereken düzeltmeye bak."
    ),
    False: "Politika merkezinde açık sorun yok.",
}
READ_ERRORS = {
    "not_configured": "AdSense API bağlantısı sunucuda kapalı.",
    "configuration_invalid": "AdSense bağlantı ayarları geçersiz.",
    "authentication_failed": "Google bağlantısının yetkisi yenilenmeli.",
    "permission_denied": "Bağlı Google hesabının AdSense erişim izni yok.",
    "scope_mismatch": "Google bağlantısının yetkisi salt okunur değil; bağlantıyı yeniden kur.",
    "rate_limited": "Google istek sınırına takıldı.",
    "provider_unavailable": "Google AdSense API zamanında yanıt vermedi.",
    "invalid_response": "Google yanıtı doğrulanamadı.",
    "account_not_found": "Bağlı Google hesabında AdSense hesabı bulunamadı.",
    "site_not_found": f"AdSense hesabında {SITE} sitesi bulunamadı.",
    "api_unreachable": (
        "API konteynerine ulaşılamadı; çekirdek (lecturesift.service) çalışmıyor olabilir."
    ),
    "timeout": f"Durum okuması {READ_SECONDS} saniyede bitmedi.",
    "invalid_snapshot": "API'den gelen durum özeti doğrulanamadı.",
    "unknown": "Bilinmeyen bir hata oluştu.",
}
READ_PARTS = {
    "account": "hesap ve site durumu",
    "alerts": "uyarılar",
    "policy": "politika sorunları",
    "api": "API konteyneri",
}


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass(frozen=True)
class Reading:
    """One check: the compared fields, or which part failed and why."""

    snapshot: dict[str, Any] | None
    part: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.snapshot is not None


def _failed(part: str, error: str) -> Reading:
    return Reading(
        None,
        part if part in READ_PARTS else "account",
        error if error in READ_ERRORS else "unknown",
    )


def validate_snapshot(value: Any) -> dict[str, Any] | None:
    """The compared fields in a fixed shape, or None if anything is off."""

    if not isinstance(value, dict) or set(value) != set(FIELDS):
        return None
    account = value["account_state"]
    site = value["site_state"]
    auto_ads = value["auto_ads"]
    alerts = value["alerts"]
    policy = value["policy_issues"]
    if not isinstance(account, str) or account not in ACCOUNT_STATES:
        return None
    if not isinstance(site, str) or site not in SITE_STATES:
        return None
    if auto_ads is not None and not isinstance(auto_ads, bool):
        return None
    if not isinstance(alerts, dict) or len(alerts) > MAX_ALERTS:
        return None
    for alert_type, severity in alerts.items():
        if not isinstance(alert_type, str) or not ALERT_TYPE.fullmatch(alert_type):
            return None
        if not isinstance(severity, str) or severity not in SEVERITIES:
            return None
    if type(policy) is not int or not 0 <= policy <= MAX_POLICY_ISSUES:
        return None
    return {
        "site_state": site,
        "account_state": account,
        "alerts": dict(sorted(alerts.items())),
        "policy_issues": policy,
        "auto_ads": auto_ads,
    }


def parse_snapshot(raw: bytes) -> Reading:
    """Validate what the container printed; only known values are kept."""

    if len(raw) > MAX_OUTPUT_BYTES:
        return _failed("api", "invalid_snapshot")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return _failed("api", "invalid_snapshot")
    if not isinstance(data, dict) or data.get("schema") != SNAPSHOT_SCHEMA:
        return _failed("api", "invalid_snapshot")
    if data.get("ok") is False:
        part, error = data.get("part"), data.get("error")
        return _failed(
            part if isinstance(part, str) else "",
            error if isinstance(error, str) else "",
        )
    if data.get("ok") is not True:
        return _failed("api", "invalid_snapshot")
    snapshot = validate_snapshot({name: data.get(name) for name in FIELDS})
    if snapshot is None:
        return _failed("api", "invalid_snapshot")
    return Reading(snapshot)


def read_status() -> Reading:
    """Run the reader inside the api container, bounded by READ_SECONDS.

    Its stderr is dropped unread: the container's diagnostics stay in its own
    logs and never reach this journal or an email.
    """

    try:
        result = subprocess.run(
            SNAPSHOT_COMMAND,
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            cwd="/",
            env=COMMAND_ENVIRONMENT,
            timeout=READ_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return _failed("api", "timeout")
    except OSError:
        return _failed("api", "api_unreachable")
    if result.returncode != 0:
        log(f"The status command inside the api container exited with status {result.returncode}.")
        return _failed("api", "api_unreachable")
    return parse_snapshot(result.stdout)


@dataclass
class State:
    snapshot: dict[str, Any] | None = None
    checked_at: str | None = None
    failures: int = 0
    unreadable_sent: bool = False

    def as_json(self) -> str:
        return json.dumps(
            {
                "schema": STATE_SCHEMA,
                "snapshot": self.snapshot,
                "checked_at": self.checked_at,
                "consecutive_failures": self.failures,
                "unreadable_email_sent": self.unreadable_sent,
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ) + "\n"


def load_state(state_dir: Path) -> State:
    """The saved state; a missing or unusable file starts a new baseline."""

    path = state_dir / STATE_NAME
    try:
        details = path.lstat()
    except FileNotFoundError:
        return State()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        log("The saved AdSense state is not a regular file; a new baseline will be saved.")
        return State()
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_STATE_BYTES else None
    except (OSError, UnicodeError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
        log("The saved AdSense state could not be read; a new baseline will be saved.")
        return State()
    snapshot = validate_snapshot(data.get("snapshot")) if data.get("snapshot") is not None else None
    checked_at = data.get("checked_at")
    failures = data.get("consecutive_failures")
    return State(
        snapshot=snapshot,
        checked_at=checked_at if _parse_checked_at(checked_at) is not None else None,
        failures=failures if type(failures) is int and 0 <= failures <= 100000 else 0,
        unreadable_sent=data.get("unreadable_email_sent") is True,
    )


def save_state(state_dir: Path, state: State) -> None:
    if state_dir.is_symlink():
        raise OSError("the AdSense state directory is a symlink")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=state_dir, prefix=".state-")
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(state.as_json())
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, state_dir / STATE_NAME)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@contextmanager
def exclusive(state_dir: Path) -> Iterator[None]:
    """Hold the state lock; raises BlockingIOError if another check holds it."""

    if state_dir.is_symlink():
        raise OSError("the AdSense state directory is a symlink")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        state_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def changed_fields(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    return [name for name in FIELDS if old[name] != new[name]]


def _iso(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _moment(now: float) -> str:
    utc = datetime.fromtimestamp(now, timezone.utc)
    local = utc.astimezone(alert_mail.TURKEY_TIME)
    return f"{utc:%Y-%m-%d %H:%M:%S} UTC ({local:%Y-%m-%d %H:%M:%S} Türkiye saati)"


def _parse_checked_at(value: Any) -> float | None:
    if not isinstance(value, str) or not CHECKED_AT.fullmatch(value):
        return None
    try:
        moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc).timestamp()


def _moment_of(checked_at: str | None) -> str:
    timestamp = _parse_checked_at(checked_at)
    return "henüz yok" if timestamp is None else _moment(timestamp)


def _state_text(meanings: Mapping[str, tuple[str, str]], value: str) -> str:
    return f"{value} ({meanings[value][0]})"


def _severity_text(severity: str) -> str:
    return f"{severity} ({SEVERITY_LABELS[severity]})"


def _alert_meaning(alert_type: str) -> str:
    return ALERT_MEANINGS.get(alert_type, ALERT_DEFAULT_MEANING)


def _alerts_text(alerts: Mapping[str, str]) -> str:
    if not alerts:
        return "yok"
    return "; ".join(f"{name}, {_severity_text(severity)}" for name, severity in alerts.items())


FOOTER = (
    "",
    f"Yönetim paneli: {ADMIN_URL}",
    f"AdSense paneli: {ADSENSE_URL}",
    "",
    "Bu kontrol her gün 03:10 UTC civarında (06:10 Türkiye saati) çalışır ve "
    "yalnızca bir şey değiştiğinde e-posta gönderir.",
)


def _status_lines(snapshot: Mapping[str, Any]) -> list[str]:
    """The current status, each part with what it means and what to do."""

    lines = [
        f"Site ({SITE}): {_state_text(SITE_MEANINGS, snapshot['site_state'])}",
        f"Anlamı: {SITE_MEANINGS[snapshot['site_state']][1]}",
        "",
        f"Hesap: {_state_text(ACCOUNT_MEANINGS, snapshot['account_state'])}",
        f"Anlamı: {ACCOUNT_MEANINGS[snapshot['account_state']][1]}",
        "",
    ]
    if snapshot["alerts"]:
        lines.append("Uyarılar:")
        for name, severity in snapshot["alerts"].items():
            lines.append(f"- {name}, {_severity_text(severity)}. Anlamı: {_alert_meaning(name)}")
    else:
        lines.append("Uyarılar: yok")
    lines += [
        "",
        f"Politika sorunları: {snapshot['policy_issues']}",
        f"Anlamı: {POLICY_MEANINGS[snapshot['policy_issues'] > 0]}",
        "",
        f"Otomatik reklamlar: {AUTO_ADS_MEANINGS[snapshot['auto_ads']][0]}",
        f"Anlamı: {AUTO_ADS_MEANINGS[snapshot['auto_ads']][1]}",
    ]
    return lines


def _change_lines(name: str, old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    if name == "site_state":
        return [
            f"Site durumu: {_state_text(SITE_MEANINGS, old[name])} → "
            f"{_state_text(SITE_MEANINGS, new[name])}",
            f"Anlamı: {SITE_MEANINGS[new[name]][1]}",
        ]
    if name == "account_state":
        return [
            f"Hesap durumu: {_state_text(ACCOUNT_MEANINGS, old[name])} → "
            f"{_state_text(ACCOUNT_MEANINGS, new[name])}",
            f"Anlamı: {ACCOUNT_MEANINGS[new[name]][1]}",
        ]
    if name == "policy_issues":
        return [
            f"Politika sorunu sayısı: {old[name]} → {new[name]}",
            f"Anlamı: {POLICY_MEANINGS[new[name] > 0]}",
        ]
    if name == "auto_ads":
        return [
            f"Otomatik reklamlar: {AUTO_ADS_MEANINGS[old[name]][0]} → "
            f"{AUTO_ADS_MEANINGS[new[name]][0]}",
            f"Anlamı: {AUTO_ADS_MEANINGS[new[name]][1]}",
        ]
    before, after = old["alerts"], new["alerts"]
    lines = ["Uyarılar:"]
    for alert_type in sorted(set(after) - set(before)):
        lines.append(
            f"- Yeni: {alert_type}, {_severity_text(after[alert_type])}. "
            f"Anlamı: {_alert_meaning(alert_type)}"
        )
    for alert_type in sorted(set(before) - set(after)):
        lines.append(f"- Kalktı: {alert_type}, {_severity_text(before[alert_type])}")
    for alert_type in sorted(set(before) & set(after)):
        if before[alert_type] != after[alert_type]:
            lines.append(
                f"- Önem derecesi değişti: {alert_type}, {_severity_text(before[alert_type])} → "
                f"{_severity_text(after[alert_type])}. Anlamı: {_alert_meaning(alert_type)}"
            )
    return lines


def compose_change(
    old: Mapping[str, Any], new: Mapping[str, Any], now: float, checked_at: str | None
) -> tuple[str, str]:
    lines = [
        f"{SITE} için AdSense durumu değişti.",
        "",
        f"Zaman: {_moment(now)}",
        f"Önceki başarılı kontrol: {_moment_of(checked_at)}",
        "",
        "Değişenler:",
    ]
    for name in changed_fields(old, new):
        lines += ["", *_change_lines(name, old, new)]
    lines += [
        "",
        "Şu anki durum:",
        f"- Site: {_state_text(SITE_MEANINGS, new['site_state'])}",
        f"- Hesap: {_state_text(ACCOUNT_MEANINGS, new['account_state'])}",
        f"- Uyarılar: {_alerts_text(new['alerts'])}",
        f"- Politika sorunları: {new['policy_issues']}",
        f"- Otomatik reklamlar: {AUTO_ADS_MEANINGS[new['auto_ads']][0]}",
        *FOOTER,
    ]
    return "[LectureSift] AdSense durumu değişti", "\n".join(lines) + "\n"


def compose_baseline(snapshot: Mapping[str, Any], now: float) -> tuple[str, str]:
    lines = [
        f"{SITE} için günlük AdSense durum kontrolü çalışıyor. Bu, ilk kontrolün "
        "sonucu; bundan sonra yalnızca bir şey değiştiğinde e-posta gelir.",
        "",
        f"Zaman: {_moment(now)}",
        "",
        *_status_lines(snapshot),
        *FOOTER,
    ]
    return "[LectureSift] AdSense durum takibi başladı", "\n".join(lines) + "\n"


def compose_test(
    snapshot: Mapping[str, Any], saved: Mapping[str, Any], now: float
) -> tuple[str, str]:
    differences = changed_fields(saved, snapshot)
    names = {
        "site_state": "site durumu",
        "account_state": "hesap durumu",
        "alerts": "uyarılar",
        "policy_issues": "politika sorunu sayısı",
        "auto_ads": "otomatik reklamlar",
    }
    comparison = (
        "Kayıtlı durumdan farklı olanlar: "
        + ", ".join(names[name] for name in differences)
        + ". Günlük kontrol bunları bir sonraki çalışmasında ayrıca bildirecek."
        if differences
        else "Kayıtlı durumla aynı; günlük kontrol bu durum için e-posta göndermeyecek."
    )
    lines = [
        "Bu e-posta, sunucuda elle çalıştırılan testle gönderildi. Günlük kontrolün "
        "kayıtlı durumu değiştirilmedi.",
        comparison,
        "",
        f"Zaman: {_moment(now)}",
        "",
        *_status_lines(snapshot),
        *FOOTER,
    ]
    return "[LectureSift] AdSense durum kontrolü testi", "\n".join(lines) + "\n"


def compose_unreadable(
    reading: Reading, failures: int, now: float, checked_at: str | None
) -> tuple[str, str]:
    lines = [
        f"{SITE} için AdSense durumu art arda {failures} kontrolde okunamadı "
        "(her gün bir kontrol yapılır).",
        "",
        f"Zaman: {_moment(now)}",
        f"Okunamayan: {READ_PARTS[reading.part]}",
        f"Sebep: {READ_ERRORS[reading.error]} ({reading.error})",
        f"Son başarılı kontrol: {_moment_of(checked_at)}",
        "",
        "Ne yapmalı:",
        f"- Yönetim panelinde AdSense bölümünün bağlantı durumuna bak: {ADMIN_URL}",
        f"- Sunucuda kontrolün kaydını oku: journalctl -u {UNIT} -n 50",
        "",
        "Durum okunamadığı sürece değişiklikler bildirilemez. Bu e-posta, durum "
        "yeniden okunana kadar tekrar gönderilmez.",
    ]
    return "[LectureSift] AdSense durumu okunamıyor", "\n".join(lines) + "\n"


def _mail_settings(env_file: Path) -> tuple[Mapping[str, str], list[str]] | None:
    try:
        settings, undecoded = alert_mail.load_settings(env_file)
    except (OSError, UnicodeError):
        log("AdSense status email not sent: the runtime environment is missing or unsafe.")
        return None
    if undecoded:
        log(f"{undecoded} line(s) of the runtime environment could not be decoded and were ignored.")
    to = alert_mail.recipients(settings)
    if not to:
        log(
            "AdSense status email not sent: set LECTURESIFT_OPS_ALERT_EMAIL or "
            "LECTURESIFT_ADMIN_EMAILS to a valid address."
        )
        return None
    return settings, to


@dataclass
class Context:
    env_file: Path
    state_dir: Path
    now: float
    sender: Callable[..., None]
    clock: Callable[[], float]
    pause: Callable[[float], None]


def _send(
    context: Context,
    what: str,
    message: tuple[str, str],
    on_first_delivery: Callable[[], None] | None = None,
) -> bool:
    """Email every operator; True when at least one provider accepted it."""

    mail = _mail_settings(context.env_file)
    if mail is None:
        return False
    settings, to = mail
    subject, text = message
    reached, summary = alert_mail.deliver(
        settings, to, subject, text,
        sender=context.sender, clock=context.clock, pause=context.pause,
        on_first_delivery=on_first_delivery,
    )
    if not reached:
        log(f"AdSense {what} email not sent: {summary}.")
        return False
    if reached < len(to):
        log(f"AdSense {what} email {summary}.")
    log(f"AdSense {what} email sent to {reached} recipient(s).")
    return True


def _save(context: Context, state: State) -> bool:
    try:
        save_state(context.state_dir, state)
    except OSError:
        log("The AdSense state could not be saved.")
        return False
    return True


def _summary(snapshot: Mapping[str, Any]) -> str:
    return (
        f"site {snapshot['site_state']}, account {snapshot['account_state']}, "
        f"{len(snapshot['alerts'])} alert type(s), {snapshot['policy_issues']} policy issue(s), "
        f"auto ads {snapshot['auto_ads']}"
    )


def _after_success(context: Context, state: State, new: dict[str, Any]) -> int:
    old = state.snapshot
    state.failures = 0
    state.unreadable_sent = False
    if old is not None:
        changes = changed_fields(old, new)
        if not changes:
            state.checked_at = _iso(context.now)
            log(f"AdSense status unchanged ({_summary(new)}).")
            return 0 if _save(context, state) else 1
        log(f"AdSense status changed: {', '.join(changes)} (now {_summary(new)}).")
        what, message = "status change", compose_change(old, new, context.now, state.checked_at)
    else:
        log(f"No AdSense snapshot is saved yet; sending the baseline ({_summary(new)}).")
        what, message = "baseline", compose_baseline(new, context.now)
    # Save the reset failure count before sending. If the state cannot be
    # saved, sending would repeat the same email every day, so nothing is sent.
    if not _save(context, state):
        return 1
    saved = [True]

    def record_snapshot() -> None:
        # Saved as soon as anyone has the email, so being stopped later cannot
        # send the same change again.
        state.snapshot = new
        state.checked_at = _iso(context.now)
        saved[0] = _save(context, state)

    if not _send(context, what, message, record_snapshot):
        log("The AdSense snapshot was kept as it was, so the next check sends this again.")
        return 1
    return 0 if saved[0] else 1


def _after_failure(context: Context, state: State, reading: Reading) -> int:
    state.failures += 1
    log(
        f"AdSense status could not be read ({reading.part}: {reading.error}); "
        f"{state.failures} failed check(s) in a row. No change is reported from this check."
    )
    if not _save(context, state):
        return 1
    if state.failures < FAILURES_BEFORE_EMAIL or state.unreadable_sent:
        return 0
    saved = [True]

    def record_sent() -> None:
        state.unreadable_sent = True
        saved[0] = _save(context, state)

    message = compose_unreadable(reading, state.failures, context.now, state.checked_at)
    if not _send(context, "unreadable-status", message, record_sent):
        log("The next failed check tries this email again.")
        return 1
    return 0 if saved[0] else 1


def _manual_test(context: Context, state: State, reading: Reading) -> int:
    if reading.snapshot is None:
        log(
            f"Manual test: the AdSense status could not be read ({reading.part}: "
            f"{reading.error}). Nothing was sent and the saved state was not changed."
        )
        return 1
    if state.snapshot is None:
        # The first run is the baseline the daily check compares against.
        log("Manual test: no snapshot is saved yet, so this run saves the baseline.")
        return _after_success(context, state, reading.snapshot)
    log(f"Manual test: read {_summary(reading.snapshot)}.")
    message = compose_test(reading.snapshot, state.snapshot, context.now)
    sent = _send(context, "manual test", message)
    log("Manual test: the saved AdSense state was not changed.")
    return 0 if sent else 1


def run(
    *,
    env_file: Path,
    state_dir: Path,
    manual_test: bool = False,
    now: float | None = None,
    reader: Callable[[], Reading] | None = None,
    sender: Callable[..., None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
) -> int:
    """Check once. Returns 0 when nothing is left undone, 1 otherwise."""

    context = Context(
        env_file=env_file,
        state_dir=state_dir,
        now=time.time() if now is None else now,
        sender=sender or alert_mail.send_email,
        clock=clock,
        pause=pause,
    )
    try:
        with exclusive(state_dir):
            state = load_state(state_dir)
            reading = (reader or read_status)()
            if manual_test:
                return _manual_test(context, state, reading)
            if reading.snapshot is None:
                return _after_failure(context, state, reading)
            return _after_success(context, state, reading.snapshot)
    except BlockingIOError:
        log("Another AdSense status check is running; this one did nothing.")
        return 1 if manual_test else 0
    except OSError:
        log("The AdSense state directory is missing or unsafe.")
        return 1


def main(argv: Sequence[str]) -> int:
    if list(argv) not in ([], ["--manual-test"]):
        log("Usage: adsense_status_alert.py [--manual-test]")
        return 2
    env_file = Path(os.environ.get(alert_mail.ENV_FILE_VARIABLE) or alert_mail.DEFAULT_ENV_FILE)
    try:
        return run(
            env_file=env_file,
            state_dir=DEFAULT_STATE_DIR,
            manual_test=bool(argv),
        )
    except Exception as exc:  # noqa: BLE001 - one opaque line; never a value
        log(f"AdSense status check failed: unexpected {type(exc).__name__}.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
