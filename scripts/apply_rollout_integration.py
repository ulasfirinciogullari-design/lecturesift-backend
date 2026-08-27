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

    rollout_js = FRONTEND / "rollout.js"
    if rollout_js.exists():
        value = rollout_js.read_text(encoding="utf-8")
        value = re.sub(
            r'    let currency = selectedCurrency\(\);\n    let label = \$\("rolloutCurrency"\);.*?    async function load\(\) \{\n      try \{\n',
            '''    const selector = $("billingCurrency");
    let currency = selector?.value || selectedCurrency();
    if (!document.querySelector("#plans .rollout-guest-note")) {
      const note = document.createElement("p");
      note.className = "rollout-guest-note";
      note.textContent = "Fiyatlar seçilen para biriminde gösterilir. Havale/EFT siparişi, oluşturulduğunda ekranda görünen kesin TRY tutarıyla ödenir; açıklamaya sipariş numarası yazılır.";
      grid.insertAdjacentElement("beforebegin", note);
    }
    async function load() {
      try {
        currency = selector?.value || currency;
''',
            value,
            count=1,
            flags=re.S,
        )
        write_if_changed(rollout_js, value)

    backend = ROOT / "lecturesift" / "app.py"
    if backend.exists():
        value = backend.read_text(encoding="utf-8").replace(
            'filename="LectureSift_Paketi_V4.1.zip"',
            'filename="LectureSift_Paketi.zip"',
        )
        write_if_changed(backend, value)

    rollout_service = ROOT / "lecturesift" / "rollout_service.py"
    if rollout_service.exists():
        value = rollout_service.read_text(encoding="utf-8")
        value = value.replace(
            '        5,\n        10,\n        ("short", "standard"),',
            '        10,\n        20,\n        ("short", "standard"),',
            1,
        )
        value = value.replace(
            'Column("handle", String(100), nullable=False),',
            'Column("handle", String(100), nullable=False, unique=True),',
            1,
        )
        write_if_changed(rollout_service, value)

    rollout_routes = ROOT / "lecturesift" / "rollout_routes.py"
    if rollout_routes.exists():
        value = rollout_routes.read_text(encoding="utf-8")
        value = value.replace(
            '    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()\n'
            '    remote = forwarded or (request.client.host if request.client else "unknown")\n'
            '    user_agent = request.headers.get("user-agent", "unknown")[:300]\n'
            '    fingerprint = hashlib.sha256(f"{device_id}|{remote}|{user_agent}".encode("utf-8")).hexdigest()\n',
            '    user_agent = request.headers.get("user-agent", "unknown")[:300]\n'
            '    # Keep the trial tied to this browser/device even when the user changes Wi-Fi or mobile network.\n'
            '    fingerprint = hashlib.sha256(f"{device_id}|{user_agent}".encode("utf-8")).hexdigest()\n',
            1,
        )
        write_if_changed(rollout_routes, value)


if __name__ == "__main__":
    main()
