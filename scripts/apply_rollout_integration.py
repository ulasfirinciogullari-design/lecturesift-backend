"""Apply small idempotent integrations without rewriting large legacy files."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def write_if_changed(path: Path, value: str) -> bool:
    original = path.read_text(encoding="utf-8")
    if value == original:
        return False
    path.write_text(value, encoding="utf-8")
    print(f"patched {path.relative_to(ROOT)}")
    return True


def patch_html(path: Path, *, script: bool = False) -> bool:
    value = path.read_text(encoding="utf-8")
    value = re.sub(r"\s*<span class=\"version-pill\">[^<]*</span>", "", value)
    if "rollout.css" not in value:
        value = value.replace("</head>", '<link rel="stylesheet" href="/rollout.css?v=1"></head>', 1)
    if script and "rollout.js" not in value:
        value = value.replace("</body>", '<script src="/rollout.js?v=1"></script></body>', 1)
    return write_if_changed(path, value)


def main() -> None:
    for path in FRONTEND.glob("*.html"):
        patch_html(path, script=path.name in {"index.html", "plans.html", "account.html"})

    contact = FRONTEND / "contact.html"
    if contact.exists():
        value = contact.read_text(encoding="utf-8")
        if "support@lecturesift.com" not in value:
            marker = '<p class="lead">Teknik sorun, hesap, gizlilik veya ödeme talebini güvenli form üzerinden ilet. Parola, kart bilgisi ya da doğrulama kodu gönderme.</p>'
            replacement = marker + '<p><a href="mailto:support@lecturesift.com">support@lecturesift.com</a></p>'
            value = value.replace(marker, replacement, 1)
            write_if_changed(contact, value)

    app_js = FRONTEND / "app.js"
    if app_js.exists():
        value = app_js.read_text(encoding="utf-8").replace(
            "LectureSift_Paketi_V4.1.zip", "LectureSift_Paketi.zip"
        )
        write_if_changed(app_js, value)

    backend = ROOT / "lecturesift" / "app.py"
    if backend.exists():
        value = backend.read_text(encoding="utf-8").replace(
            'filename="LectureSift_Paketi_V4.1.zip"',
            'filename="LectureSift_Paketi.zip"',
        )
        write_if_changed(backend, value)

    rollout_service = ROOT / "lecturesift" / "rollout_service.py"
    if rollout_service.exists():
        value = rollout_service.read_text(encoding="utf-8").replace(
            '        5,\n        10,\n        ("short", "standard"),',
            '        10,\n        20,\n        ("short", "standard"),',
            1,
        )
        write_if_changed(rollout_service, value)


if __name__ == "__main__":
    main()
