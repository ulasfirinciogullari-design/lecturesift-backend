from pathlib import Path
import json


ORIGIN = "https://lecturesift.com"
LANGUAGES = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")
PATHS = (
    "/",
    "/features.html",
    "/document-summary.html",
    "/lecture-video-summary.html",
    "/quiz-flashcards.html",
    "/plans.html",
    "/about.html",
    "/contact.html",
    "/privacy.html",
    "/terms.html",
    "/distance-sales.html",
    "/cookies.html",
    "/refund.html",
)
LAST_MODIFIED = "2026-09-13"
RESOURCES_PATH = Path(__file__).resolve().parents[1] / "frontend" / "study-resources.json"


def canonical_path(path: str) -> str:
    if path in {"/", "/index.html"}:
        return "/"
    return path.removesuffix(".html")


def localized_path(language: str, path: str) -> str:
    path = canonical_path(path)
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
            lines.append(f"    <lastmod>{'2026-09-19' if path == '/' else LAST_MODIFIED}</lastmod>")
            for alternate in LANGUAGES:
                href = f"{ORIGIN}{localized_path(alternate, path)}"
                lines.append(f'    <xhtml:link rel="alternate" hreflang="{alternate}" href="{href}"/>')
            lines.append(
                f'    <xhtml:link rel="alternate" hreflang="x-default" '
                f'href="{ORIGIN}{canonical_path(path)}"/>'
            )
            lines.append("  </url>")
    resources = json.loads(RESOURCES_PATH.read_text(encoding="utf-8"))
    for page in resources["pages"]:
        path = f'/{page["slug"]}'
        for language in resources["languages"]:
            lines.append("  <url>")
            lines.append(f"    <loc>{ORIGIN}{localized_path(language, path)}</loc>")
            lines.append(f'    <lastmod>{resources["updated"]}</lastmod>')
            for alternate in resources["languages"]:
                href = f"{ORIGIN}{localized_path(alternate, path)}"
                lines.append(f'    <xhtml:link rel="alternate" hreflang="{alternate}" href="{href}"/>')
            lines.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{ORIGIN}{path}"/>')
            lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "frontend" / "sitemap.xml"
    target.write_text(build_sitemap(), encoding="utf-8")
