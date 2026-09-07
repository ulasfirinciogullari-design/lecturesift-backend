from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

import pytest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
ORIGIN = "https://lecturesift.com"
LANGUAGES = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")
SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"


class _SeoHeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.document_language = ""
        self.canonicals: list[str] = []
        self.alternates: dict[str, list[str]] = {}
        self.robots: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "html":
            self.document_language = attributes.get("lang", "")
            return
        if tag.lower() == "link":
            relationships = set(attributes.get("rel", "").lower().split())
            if "canonical" in relationships:
                self.canonicals.append(attributes.get("href", ""))
            if "alternate" in relationships and attributes.get("hreflang"):
                language = attributes["hreflang"].lower()
                self.alternates.setdefault(language, []).append(attributes.get("href", ""))
            return
        if tag.lower() == "meta" and attributes.get("name", "").lower() == "robots":
            self.robots.append(attributes.get("content", "").lower())


def _sitemap_records() -> dict[str, dict[str, str]]:
    tree = ElementTree.parse(FRONTEND / "sitemap.xml")
    records: dict[str, dict[str, str]] = {}
    for node in tree.findall(f"{{{SITEMAP_NAMESPACE}}}url"):
        location_node = node.find(f"{{{SITEMAP_NAMESPACE}}}loc")
        assert location_node is not None and location_node.text
        location = location_node.text.strip()
        assert location not in records, f"Duplicate sitemap location: {location}"
        alternates: dict[str, str] = {}
        for link in node.findall(f"{{{XHTML_NAMESPACE}}}link"):
            language = (link.get("hreflang") or "").lower()
            assert language and language not in alternates, (
                f"Duplicate/empty hreflang {language!r} on {location}"
            )
            assert link.get("rel") == "alternate"
            alternates[language] = link.get("href") or ""
        records[location] = alternates
    return records


def _output_path(output: Path, url: str) -> Path:
    parsed = urlsplit(url)
    assert f"{parsed.scheme}://{parsed.netloc}" == ORIGIN
    assert not parsed.query and not parsed.fragment
    relative = parsed.path.lstrip("/")
    if not relative or parsed.path.endswith("/"):
        return output / relative / "index.html"
    candidate = output / relative
    return candidate if candidate.suffix else candidate.with_suffix(".html")


def _parse_html(path: Path) -> _SeoHeadParser:
    parser = _SeoHeadParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


@pytest.fixture(scope="module")
def localized_output(tmp_path_factory: pytest.TempPathFactory) -> Path:
    node = shutil.which("node")
    assert node, "Node.js is required to validate the production SEO build"
    sandbox = tmp_path_factory.mktemp("localized-seo-build")
    shutil.copytree(FRONTEND, sandbox / "frontend")
    (sandbox / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts" / "build_localized_site.mjs", sandbox / "scripts")
    result = subprocess.run(
        [node, "scripts/build_localized_site.mjs"],
        cwd=sandbox,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return sandbox / "dist"


def test_checked_in_sitemap_matches_its_generator_exactly() -> None:
    generator_path = ROOT / "scripts" / "generate_sitemap.py"
    spec = importlib.util.spec_from_file_location("lecturesift_generate_sitemap", generator_path)
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert (FRONTEND / "sitemap.xml").read_text(encoding="utf-8") == generator.build_sitemap()


def test_sitemap_and_prerendered_pages_are_a_closed_canonical_set(localized_output: Path) -> None:
    records = _sitemap_records()
    assert records
    expected_hreflang = set(LANGUAGES) | {"x-default"}
    physical_canonicals: list[str] = []

    for location, sitemap_alternates in records.items():
        parsed_location = urlsplit(location)
        assert parsed_location.scheme == "https"
        assert parsed_location.netloc == "lecturesift.com"
        assert "/index.html" not in parsed_location.path
        assert set(sitemap_alternates) == expected_hreflang

        output_path = _output_path(localized_output, location)
        assert output_path.is_file(), f"Sitemap URL has no generated page: {location}"
        page = _parse_html(output_path)
        assert page.canonicals == [location], f"Non-self canonical on {location}"
        assert not any("noindex" in value for value in page.robots), location
        assert set(page.alternates) == expected_hreflang
        assert all(len(values) == 1 for values in page.alternates.values())
        assert {language: values[0] for language, values in page.alternates.items()} == sitemap_alternates

        first_segment = parsed_location.path.strip("/").split("/", 1)[0]
        expected_language = first_segment if first_segment in LANGUAGES[1:] else "tr"
        assert page.document_language == expected_language
        physical_canonicals.extend(page.canonicals)

    assert len(physical_canonicals) == len(set(physical_canonicals))

    generated_canonicals: list[str] = []
    for page_path in localized_output.rglob("*.html"):
        generated_canonicals.extend(_parse_html(page_path).canonicals)
    assert sorted(generated_canonicals) == sorted(records), (
        "Generated indexable pages and sitemap URLs have drifted apart"
    )


def test_every_hreflang_cluster_is_complete_and_reciprocal() -> None:
    records = _sitemap_records()
    for source, alternates in records.items():
        source_path = urlsplit(source).path
        first_segment = source_path.strip("/").split("/", 1)[0]
        source_language = first_segment if first_segment in LANGUAGES[1:] else "tr"
        for language in LANGUAGES:
            target = alternates[language]
            assert target in records, f"{source} points to absent hreflang target {target}"
            assert records[target][source_language] == source, (
                f"Non-reciprocal hreflang: {source} -> {target}"
            )
        assert alternates["x-default"] in records


NONINDEXABLE_HTML = tuple(
    page_path.name
    for page_path in sorted(FRONTEND.glob("*.html"))
    if page_path.name not in {"index.html", "404.html"}
    and f"{ORIGIN}/{page_path.stem}" not in _sitemap_records()
)


@pytest.mark.parametrize("filename", NONINDEXABLE_HTML)
def test_nonindexable_html_pages_are_explicitly_noindex_and_crawlable(filename: str) -> None:
    robots = (FRONTEND / "robots.txt").read_text(encoding="utf-8")
    disallowed_paths = {
        match.group(1).strip()
        for match in re.finditer(r"^\s*Disallow:\s*(\S+)\s*$", robots, re.MULTILINE | re.IGNORECASE)
    }

    page = _parse_html(FRONTEND / filename)
    assert any("noindex" in value for value in page.robots), (
        f"{filename} is absent from the sitemap but has no explicit noindex"
    )
    assert f"/{filename}" not in disallowed_paths, (
        f"robots.txt blocks {filename}, preventing crawlers from seeing its noindex"
    )


def test_index_aliases_are_permanent_redirects_not_duplicate_200_pages() -> None:
    redirects = (FRONTEND / "_redirects").read_text(encoding="utf-8")
    rules: dict[str, tuple[str, str]] = {}
    for raw_line in redirects.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        assert len(fields) >= 3, f"Malformed redirect rule: {raw_line}"
        rules[fields[0]] = (fields[1], fields[2])

    permanent_root = {("/", "301"), ("/", "301!")}
    permanent_language = {("/:lang/", "301"), ("/:lang/", "301!")}
    assert rules.get("/index.html") in permanent_root
    for language in LANGUAGES[1:]:
        language_rule = rules.get(f"/{language}")
        generic_language_rule = rules.get("/:lang")
        assert language_rule in {
            (f"/{language}/", "301"),
            (f"/{language}/", "301!"),
        } or generic_language_rule in permanent_language
        index_rule = rules.get(f"/{language}/index.html")
        generic_index_rule = rules.get("/:lang/index.html")
        assert index_rule in {
            (f"/{language}/", "301"),
            (f"/{language}/", "301!"),
        } or generic_index_rule in permanent_language


def test_html_aliases_permanently_redirect_to_sitemap_clean_urls() -> None:
    redirects = (FRONTEND / "_redirects").read_text(encoding="utf-8")
    rules: dict[str, tuple[str, str]] = {}
    for raw_line in redirects.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        source, destination, status, *_ = line.split()
        rules[source] = (destination, status)

    root_routes = {
        urlsplit(location).path
        for location in _sitemap_records()
        if urlsplit(location).path != "/"
        and urlsplit(location).path.strip("/").split("/", 1)[0] not in LANGUAGES[1:]
    }
    for route in root_routes:
        assert rules.get(f"{route}.html") in {(route, "301"), (route, "301!")}
        assert rules.get(f"/:lang{route}.html") in {
            (f"/:lang{route}", "301"),
            (f"/:lang{route}", "301!"),
        }


def test_runtime_seo_language_is_derived_from_the_indexable_url() -> None:
    seo = (FRONTEND / "seo.js").read_text(encoding="utf-8")
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")

    assert 'const language = LANGUAGES.includes(parts[0]) ? parts.shift() : "tr";' in seo
    assert (
        'const language = LANGUAGES.includes(parts[0]) ? parts.shift() : '
        '(document.documentElement.lang || "tr");'
    ) not in seo
    assert 'if (publicRoutes.has(basePath)) return "tr";' in i18n
