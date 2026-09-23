from __future__ import annotations

import importlib.util
import json
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
STUDY_RESOURCES = json.loads((FRONTEND / "study-resources.json").read_text(encoding="utf-8"))
STUDY_SLUGS = {page["slug"] for page in STUDY_RESOURCES["pages"]}


def _page_languages(location: str) -> tuple[str, ...]:
    slug = urlsplit(location).path.rstrip("/").split("/")[-1]
    return tuple(STUDY_RESOURCES["languages"]) if slug in STUDY_SLUGS else LANGUAGES


class _SeoHeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.document_language = ""
        self.canonicals: list[str] = []
        self.alternates: dict[str, list[str]] = {}
        self.robots: list[str] = []
        self.structured_data: list[dict] = []
        self._json_ld: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "script" and attributes.get("type") == "application/ld+json":
            self._json_ld = []
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

    def handle_data(self, data: str) -> None:
        if self._json_ld is not None:
            self._json_ld.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._json_ld is not None:
            self.structured_data.append(json.loads("".join(self._json_ld)))
            self._json_ld = None


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
    physical_canonicals: list[str] = []

    for location, sitemap_alternates in records.items():
        expected_hreflang = set(_page_languages(location)) | {"x-default"}
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
        for language in _page_languages(source):
            target = alternates[language]
            assert target in records, f"{source} points to absent hreflang target {target}"
            assert records[target][source_language] == source, (
                f"Non-reciprocal hreflang: {source} -> {target}"
            )
        assert alternates["x-default"] in records


def test_breadcrumbs_follow_the_localized_navigation(localized_output: Path) -> None:
    records = _sitemap_records()
    for location in records:
        page = _parse_html(_output_path(localized_output, location))
        graph = [node for schema in page.structured_data for node in schema.get("@graph", [])]
        webpage = next(node for node in graph if node.get("@type") == "WebPage")
        breadcrumbs = [node for node in graph if node.get("@type") == "BreadcrumbList"]
        prefix = "" if page.document_language == "tr" else f"/{page.document_language}"
        home = f"{ORIGIN}{prefix}/"
        if location == home:
            assert not breadcrumbs and "breadcrumb" not in webpage
            continue

        assert len(breadcrumbs) == 1, location
        breadcrumb = breadcrumbs[0]
        assert webpage["breadcrumb"] == {"@id": breadcrumb["@id"]}
        expected_urls = [home]
        slug = urlsplit(location).path.rsplit("/", 1)[-1]
        if slug in STUDY_SLUGS - {"study-guides"}:
            expected_urls.append(f"{ORIGIN}{prefix}/study-guides")
        expected_urls.append(location)
        items = breadcrumb["itemListElement"]
        assert [item["item"] for item in items] == expected_urls, location
        assert [item["position"] for item in items] == list(range(1, len(items) + 1))
        assert all(item["name"].strip() and item["item"] in records for item in items)


def test_landing_examples_faqs_and_dates_survive_prerendering(localized_output: Path) -> None:
    landing = json.loads((FRONTEND / "landing-pages.json").read_text(encoding="utf-8"))
    sitemap = ElementTree.parse(FRONTEND / "sitemap.xml")
    dates = {
        node.findtext(f"{{{SITEMAP_NAMESPACE}}}loc"): node.findtext(f"{{{SITEMAP_NAMESPACE}}}lastmod")
        for node in sitemap.findall(f"{{{SITEMAP_NAMESPACE}}}url")
    }
    for route, editions in landing["pages"].items():
        for language, copy in editions.items():
            prefix = "" if language == "tr" else f"/{language}"
            location = f"{ORIGIN}{prefix}{route.removesuffix('.html')}"
            output = _output_path(localized_output, location)
            html = output.read_text(encoding="utf-8")
            parsed = _parse_html(output)
            graph = [node for schema in parsed.structured_data for node in schema.get("@graph", [])]
            webpage = next(node for node in graph if node["@type"] == "WebPage")
            article = next(node for node in graph if node["@type"] == "Article")
            faq = next(node for node in graph if node["@type"] == "FAQPage")
            assert webpage["name"] == copy["title"]
            assert webpage["description"] == copy["description"]
            assert article["dateModified"] == dates[location] == landing["updated"]
            assert 'id="example"' in html and 'class="landing-source"' in html
            assert 'class="landing-answer"' in html
            # The teaching exercise is not a customer FAQ.
            assert [question["name"] for question in faq["mainEntity"]] == [item["question"] for item in copy["faqs"]]
            for item in copy["related"]:
                assert f'href="{prefix}{item["path"]}"' in html
                assert f'{ORIGIN}{prefix}{item["path"]}' in _sitemap_records()
        # No English editorial fallback is injected into an unwritten edition.
        de_html = _output_path(localized_output, f"{ORIGIN}/de{route.removesuffix('.html')}").read_text(encoding="utf-8")
        assert "data-landing-page" not in de_html
    assert not (localized_output / "landing-pages.json").exists()


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
    # Netlify normalizes trailing slashes before rule matching. A rule that
    # differs only by that slash redirects its destination back to itself.
    for source, (destination, status) in rules.items():
        if status.rstrip("!") in {"301", "302", "303", "307", "308"}:
            assert source.rstrip("/") != destination.rstrip("/"), (source, destination)
    for language in LANGUAGES[1:]:
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
