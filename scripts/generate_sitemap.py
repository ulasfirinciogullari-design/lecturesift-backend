from pathlib import Path


ORIGIN = "https://lecturesift.com"
LANGUAGES = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")
PATHS = ("/", "/features.html", "/plans.html", "/about.html", "/contact.html", "/privacy.html", "/terms.html", "/distance-sales.html", "/cookies.html", "/refund.html")
LAST_MODIFIED = "2026-08-28"


def localized_path(language: str, path: str) -> str:
    if language == "tr":
        return path
    return f"/{language}/" if path == "/" else f"/{language}{path}"


def build_sitemap() -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    for language in LANGUAGES:
        for path in PATHS:
            lines.append("  <url>")
            lines.append(f"    <loc>{ORIGIN}{localized_path(language, path)}</loc>")
            lines.append(f"    <lastmod>{LAST_MODIFIED}</lastmod>")
            for alternate in LANGUAGES:
                href = f"{ORIGIN}{localized_path(alternate, path)}"
                lines.append(f'    <xhtml:link rel="alternate" hreflang="{alternate}" href="{href}"/>')
            lines.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{ORIGIN}{path}"/>')
            lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "frontend" / "sitemap.xml"
    target.write_text(build_sitemap(), encoding="utf-8")
