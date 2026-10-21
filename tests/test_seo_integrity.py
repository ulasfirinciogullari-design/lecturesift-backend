from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
from html import unescape
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
COURSE_PAGES = [page for page in STUDY_RESOURCES["pages"] if page["kind"] == "course"]
TOPIC_PAGES = [page for page in STUDY_RESOURCES["pages"] if page["kind"] == "topic"]


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
        self.descriptions: list[str] = []
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
        if tag.lower() == "meta" and attributes.get("name", "").lower() == "description":
            self.descriptions.append(attributes.get("content", ""))

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


def _copy_build_inputs(sandbox: Path) -> None:
    shutil.copytree(FRONTEND, sandbox / "frontend")
    (sandbox / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts" / "build_localized_site.mjs", sandbox / "scripts")


def _build_site(sandbox: Path, operator_env: dict[str, str] | None = None, *, copy_inputs: bool = True) -> Path:
    node = shutil.which("node")
    assert node, "Node.js is required to validate the production SEO build"
    if copy_inputs:
        _copy_build_inputs(sandbox)
    # Seller identity is optional build input; never let a developer's shell
    # values leak into, or change, the default build under test.
    env = {key: value for key, value in os.environ.items() if not key.startswith("LEGAL_")}
    env.update(operator_env or {})
    result = subprocess.run(
        [node, "scripts/build_localized_site.mjs"],
        cwd=sandbox,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return sandbox / "dist"


@pytest.fixture(scope="module")
def localized_output(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_site(tmp_path_factory.mktemp("localized-seo-build"))


def test_checked_in_sitemap_matches_its_generator_exactly() -> None:
    generator_path = ROOT / "scripts" / "generate_sitemap.py"
    spec = importlib.util.spec_from_file_location("lecturesift_generate_sitemap", generator_path)
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert (FRONTEND / "sitemap.xml").read_text(encoding="utf-8") == generator.build_sitemap()


def _sitemap_dates(path: Path) -> dict[str, str | None]:
    return {
        node.findtext(f"{{{SITEMAP_NAMESPACE}}}loc"): node.findtext(f"{{{SITEMAP_NAMESPACE}}}lastmod")
        for node in ElementTree.parse(path).findall(f"{{{SITEMAP_NAMESPACE}}}url")
    }


def _git(repository: Path, *args: str, date: str | None = None) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(
        [
            "git", "-c", "user.name=Sitemap Test", "-c", "user.email=sitemap@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args,
        ],
        cwd=repository, env=env, check=True, capture_output=True, timeout=60,
    )


def _core_entries(dates: dict[str, str | None]) -> dict[str, str | None]:
    landing = json.loads((FRONTEND / "landing-pages.json").read_text(encoding="utf-8"))
    entries = {}
    for language in LANGUAGES:
        prefix = "" if language == "tr" else f"/{language}"
        for path in (
            "/", "/features", "/document-summary", "/lecture-video-summary", "/quiz-flashcards", "/plans",
            "/about", "/contact", "/privacy", "/terms", "/distance-sales", "/cookies", "/refund",
        ):
            if language in landing["pages"].get(f"{path}.html", {}):
                continue
            location = f"{ORIGIN}{prefix}{path}" if path != "/" else f"{ORIGIN}{prefix}/"
            entries[location] = dates[location]
    return entries


def test_checked_in_sitemap_leaves_core_pages_for_the_build_to_date() -> None:
    dates = _sitemap_dates(FRONTEND / "sitemap.xml")
    assert set(_core_entries(dates).values()) == {None}
    # Editorial dates stay with their JSON sources.
    landing = json.loads((FRONTEND / "landing-pages.json").read_text(encoding="utf-8"))
    assert dates[f"{ORIGIN}/en/document-summary"] == landing["updated"]
    assert all(dates[f"{ORIGIN}/{page['slug']}"] for page in STUDY_RESOURCES["pages"])


@pytest.mark.skipif(not shutil.which("git"), reason="git is required to date sitemap entries")
def test_build_dates_core_sitemap_entries_from_git_history(tmp_path: Path) -> None:
    repository = tmp_path / "site"
    repository.mkdir()
    _copy_build_inputs(repository)
    _git(repository, "init", "-q")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "site", date="2026-01-02T12:00:00+00:00")
    plans = repository / "frontend" / "plans.html"
    plans.write_text(plans.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    _git(repository, "commit", "-q", "-am", "plans", date="2026-03-04T12:00:00+00:00")

    dist = _build_site(repository, copy_inputs=False)
    dates = _sitemap_dates(dist / "sitemap.xml")
    core = _core_entries(dates)
    landing = json.loads((FRONTEND / "landing-pages.json").read_text(encoding="utf-8"))
    assert len(core) == 13 * len(LANGUAGES) - sum(len(editions) for editions in landing["pages"].values())
    plan_pages = {f"{ORIGIN}/plans"} | {f"{ORIGIN}/{language}/plans" for language in LANGUAGES[1:]}
    assert {loc for loc, value in core.items() if value == "2026-03-04"} == plan_pages
    assert {value for loc, value in core.items() if loc not in plan_pages} == {"2026-01-02"}
    # Everything else in the published sitemap is the checked-in file's.
    checked_in = _sitemap_dates(FRONTEND / "sitemap.xml")
    assert dates.keys() == checked_in.keys()
    assert {loc: value for loc, value in dates.items() if loc not in core} == {
        loc: value for loc, value in checked_in.items() if loc not in core
    }

    # A shallow clone would date every page to its single commit: no dates instead.
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", repository.as_uri(), str(shallow))
    shallow_dates = _sitemap_dates(_build_site(shallow, copy_inputs=False) / "sitemap.xml")
    assert shallow_dates == checked_in


def test_build_without_git_history_publishes_the_checked_in_sitemap(localized_output: Path) -> None:
    assert (localized_output / "sitemap.xml").read_bytes() == (FRONTEND / "sitemap.xml").read_bytes()


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
        assert page.robots == ["max-image-preview:large,max-snippet:-1,max-video-preview:-1"], location
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


def test_shared_application_description_matches_the_homepage_in_every_language(localized_output: Path) -> None:
    product_descriptions = {}
    for language in LANGUAGES:
        prefix = "" if language == "tr" else f"/{language}"
        home = _parse_html(_output_path(localized_output, f"{ORIGIN}{prefix}/"))
        assert len(home.descriptions) == 1 and home.descriptions[0].strip()
        product_descriptions[language] = home.descriptions[0]
    assert len(set(product_descriptions.values())) == len(LANGUAGES)

    # Includes policy pages, product landing pages, and authored study guides.
    # Their own subjects must never replace the shared application's identity.
    for location in _sitemap_records():
        page = _parse_html(_output_path(localized_output, location))
        graph = [node for schema in page.structured_data for node in schema.get("@graph", [])]
        application = next(node for node in graph if node.get("@type") == "SoftwareApplication")
        webpage = next(node for node in graph if node.get("@type") == "WebPage")
        assert application["@id"] == f"{ORIGIN}/#application"
        assert application["description"] == product_descriptions[page.document_language], location
        assert len(page.descriptions) == 1
        assert unescape(webpage["description"]) == page.descriptions[0], location
        for article in (node for node in graph if node.get("@type") == "Article"):
            assert article["description"] == webpage["description"], location


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


def test_payment_marks_and_plan_minutes_are_translated_when_prerendered(localized_output: Path) -> None:
    for language in LANGUAGES[1:]:
        for route in ("/", "/plans"):
            html = _output_path(localized_output, f"{ORIGIN}/{language}{route}").read_text(encoding="utf-8")
            assert 'alt="iyzico ile Öde' not in html, (language, route)
            assert 'alt="Mastercard, Visa, American Express ve TROY"' not in html, (language, route)
    english_home = _output_path(localized_output, f"{ORIGIN}/en/").read_text(encoding="utf-8")
    assert 'alt="Pay with iyzico, Mastercard, Visa, American Express and TROY"' in english_home
    # "dakika" means minutes of media, never meeting minutes ("Protokoll").
    german_plans = _output_path(localized_output, f"{ORIGIN}/de/plans").read_text(encoding="utf-8")
    assert "Vergleichen Sie Minuten, Quizze, Lernkarten" in german_plans
    assert "Protokoll" not in german_plans
    german_sales = _output_path(localized_output, f"{ORIGIN}/de/distance-sales").read_text(encoding="utf-8")
    assert "Die Minuten-, Quiz-, Lernkarten-" in german_sales
    assert "Protokoll" not in german_sales


def _assert_bilingual_guide_pages(localized_output: Path, pages: list[dict], nav: str, listing: str) -> None:
    records = _sitemap_records()
    sitemap = ElementTree.parse(FRONTEND / "sitemap.xml")
    dates = {
        node.findtext(f"{{{SITEMAP_NAMESPACE}}}loc"): node.findtext(f"{{{SITEMAP_NAMESPACE}}}lastmod")
        for node in sitemap.findall(f"{{{SITEMAP_NAMESPACE}}}url")
    }
    for page in pages:
        slug = page["slug"]
        pair = {"tr": f"{ORIGIN}/{slug}", "en": f"{ORIGIN}/en/{slug}"}
        # Only the written Turkish and English editions exist; no other language is indexed.
        assert [url for url in records if urlsplit(url).path.rsplit("/", 1)[-1] == slug] == list(pair.values())
        assert not any((localized_output / language / f"{slug}.html").exists() for language in LANGUAGES[2:])
        for language, location in pair.items():
            copy = page[language]
            prefix = "" if language == "tr" else "/en"
            output = _output_path(localized_output, location)
            html = output.read_text(encoding="utf-8")
            parsed = _parse_html(output)
            assert parsed.document_language == language
            assert parsed.canonicals == [location]
            assert records[location] == {**pair, "x-default": pair["tr"]}
            assert {code: values[0] for code, values in parsed.alternates.items()} == records[location]
            title = unescape(re.search(r"<title>(.*?)</title>", html).group(1))
            assert title == f"{copy['title']} | LectureSift" and len(title) <= 72, location
            assert parsed.descriptions == [copy["description"]] and 110 <= len(copy["description"]) <= 160, location
            assert '<meta property="og:type" content="article">' in html

            graph = [node for schema in parsed.structured_data for node in schema.get("@graph", [])]
            nodes = {node["@type"]: node for node in graph}
            assert nodes["WebPage"]["name"] == title and nodes["WebPage"]["inLanguage"] == language
            assert nodes["Article"]["dateModified"] == page["updated"] == dates[location]
            assert [item["item"] for item in nodes["BreadcrumbList"]["itemListElement"]] == [
                f"{ORIGIN}{prefix}/", f"{ORIGIN}{prefix}/study-guides", location,
            ]
            faq_html = re.search(r'<div class="faq-list">(.*?)</div>', html, re.S).group(1)
            visible_questions = re.findall(r"<summary>(.*?)</summary>", faq_html)
            assert len(visible_questions) >= 4
            assert [item["name"] for item in nodes["FAQPage"]["mainEntity"]] == visible_questions
            # The worked output is explicitly an authored example, not a customer result.
            assert re.search(
                r'<section id="(?:ornek|example)"><h2>[^<]+</h2><p class="guide-note"><strong>'
                r"(?:Bu bölüm bir örnektir\.|This section is an example\.)</strong>",
                html,
            ), location

            trial = re.search(r'<div class="guide-hero-actions" data-guide-trial>(.*?)</div>', html).group(1)
            assert f'href="{prefix}/workspace.html"' in trial and f'href="{prefix}/register.html"' in trial
            related = re.search(rf"<nav[^>]*{nav}[^>]*>(.*?)</nav>", html).group(1)
            assert re.findall(r'href="([^"]+)"', related) == [
                f"{prefix}/{other['slug']}" for other in pages if other is not page
            ]
            for attributes, href in re.findall(r'<a\b([^>]*)\bhref="(/[^"]*)"', html):
                if "guide-language" in attributes:
                    assert href == (f"/en/{slug}" if language == "tr" else f"/{slug}")
                elif language == "en":
                    assert href.startswith("/en/") or href.startswith("/assets/"), (location, href)
                else:
                    assert not href.startswith("/en/"), (location, href)

    # The bilingual study library makes every page of this kind discoverable in its own language.
    for prefix in ("", "/en"):
        library = _output_path(localized_output, f"{ORIGIN}{prefix}/study-guides").read_text(encoding="utf-8")
        listed = re.search(rf'<ul class="{listing}">(.*?)</ul>', library, re.S).group(1)
        assert re.findall(r'href="([^"]+)"', listed) == [f"{prefix}/{page['slug']}" for page in pages]


def test_course_pages_are_turkish_english_pairs_with_trial_calls_and_faq(localized_output: Path) -> None:
    assert [page["slug"] for page in COURSE_PAGES] == [
        "medicine-lecture-notes", "law-lecture-notes", "engineering-lecture-notes",
        "psychology-lecture-notes", "economics-lecture-notes",
    ]
    _assert_bilingual_guide_pages(localized_output, COURSE_PAGES, "data-guide-subjects", "guide-subjects")


def test_topic_pages_are_substantial_turkish_english_pairs_with_trial_calls_and_faq(localized_output: Path) -> None:
    assert [page["slug"] for page in TOPIC_PAGES] == [
        "yks-biology-notes", "kpss-history-notes", "engineering-formula-sheet", "english-medium-lectures",
        "final-exam-study-plan", "long-lecture-recordings", "open-education-unit-summary",
    ]
    _assert_bilingual_guide_pages(localized_output, TOPIC_PAGES, "data-guide-topics", "guide-topics")
    eyebrows = {"tr": "LectureSift · Sınava ve konuya göre çalışma", "en": "LectureSift · Study by exam and topic"}
    for page in TOPIC_PAGES:
        for language, prefix in (("tr", ""), ("en", "/en")):
            # Each guide is a full worked lesson, not a thin doorway page.
            words = re.sub(r"<[^>]+>", " ", page[language]["body"]).split()
            assert len(words) >= 700, (page["slug"], language, len(words))
            html = _output_path(localized_output, f"{ORIGIN}{prefix}/{page['slug']}").read_text(encoding="utf-8")
            assert f'<p class="guide-eyebrow">{eyebrows[language]}</p>' in html
    # Search results show a distinct title and description for every study page.
    for language in ("tr", "en"):
        titles = [page[language]["title"] for page in STUDY_RESOURCES["pages"]]
        descriptions = [page[language]["description"] for page in STUDY_RESOURCES["pages"]]
        assert len(set(titles)) == len(titles), language
        assert len(set(descriptions)) == len(descriptions), language


def test_guide_measurement_has_consent_controls_without_the_large_application_scripts(localized_output: Path) -> None:
    for slug in STUDY_SLUGS:
        for prefix in ["", "en/"]:
            html = (localized_output / f"{prefix}{slug}.html").read_text(encoding="utf-8")
            sources = re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', html)
            assert sources.count("/consent.js?v=6") == 1
            assert sources.count("/analytics.js?v=11") == 1
            assert "/consent.css?v=3" in html
            assert not any(re.search(r"/(?:i18n|page-i18n|app)\.js", src) for src in sources)


def test_public_pages_use_small_language_specific_runtime(localized_output: Path) -> None:
    original = (FRONTEND / "i18n.js").read_bytes()
    for language in LANGUAGES:
        prefix = "" if language == "tr" else f"/{language}"
        html = _output_path(localized_output, f"{ORIGIN}{prefix}/document-summary").read_text()
        source, = re.findall(r'<script[^>]*src="(/assets/i18n/[^\"]+)"', html)
        assert re.fullmatch(rf"/assets/i18n/{language}-[a-f0-9]{{12}}\.js", source)
        runtime = (localized_output / source.lstrip("/")).read_bytes()
        assert len(runtime) < len(original) * 0.5, (language, len(runtime), len(original))
        for route in ["/", "/plans"]:
            url = f"{ORIGIN}{prefix}{route}"
            assert f'src="{source}"' in _output_path(localized_output, url).read_text()
    assert (localized_output / "i18n.js").read_bytes() == original


def _resolve_page_copy(rows: dict[str, list[str | None]], source: str, index: int) -> str:
    # i18n.js exact(): row[index] || row[1] || source.
    row = rows.get(source) or []
    return next((value for value in row[index:index + 1] + row[1:2] if value), source)


# Runs a generated loader as a page would and, with "execute", every catalog it writes.
LOADER_HARNESS = r"""
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const [dist, loader, scenarios] = [process.argv[1], process.argv[2], JSON.parse(process.argv[3])];
const execute = process.argv[4] === 'execute';
const results = scenarios.map(([pathname, saved, browser]) => {
  const written = [];
  const context = {
    location: {pathname}, navigator: {language: browser}, window: {},
    localStorage: {getItem: key => key === 'lecturesift-ui' ? saved : null},
    document: {write: markup => written.push(markup)},
  };
  vm.runInNewContext(fs.readFileSync(path.join(dist, loader), 'utf8'), context);
  const sources = written.map(markup => {
    const match = markup.match(/^<script src="([^"]+)"><\/script>$/);
    if (!match) throw new Error(`Unexpected loader output: ${markup}`);
    return match[1];
  });
  if (execute) for (const source of sources) vm.runInNewContext(fs.readFileSync(path.join(dist, source), 'utf8'), context);
  return {sources, rows: context.window.LECTURESIFT_PAGE_COPY || {}};
});
process.stdout.write(JSON.stringify(results));
"""

# (pathname, saved language, browser language) -> language i18n.js selects.
LOADER_SCENARIOS = [
    ("/workspace.html", None, "en-US", "en"),
    ("/workspace.html", "de", "en-US", "de"),
    ("/workspace", "xx", "fr-CA", "fr"),
    ("/account.html", None, "nl-NL", "tr"),
    ("/account.html", "tr", "en-US", "tr"),
    ("/en/workspace.html", "de", "fr-FR", "en"),
    ("/zh/login", None, "en-US", "zh"),
    ("/features", "de", "de-DE", "tr"),
    ("/index.html", "ja", "ja-JP", "tr"),
    *((f"/{language}/support.html", "tr", "tr-TR", language) for language in LANGUAGES[1:]),
]


def _run_loader(dist: Path, loader: str, *, execute: bool) -> list[dict]:
    node = shutil.which("node")
    assert node
    result = subprocess.run(
        [node, "-e", LOADER_HARNESS, str(dist), loader.lstrip("/"),
         json.dumps([scenario[:3] for scenario in LOADER_SCENARIOS]), "execute" if execute else "list"],
        text=True, encoding="utf-8", capture_output=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_app_pages_load_only_the_readers_page_copy_catalog(localized_output: Path) -> None:
    full_source = (FRONTEND / "page-i18n.js").read_text(encoding="utf-8")
    full = json.loads(full_source.split("window.LECTURESIFT_PAGE_COPY=", 1)[1].rstrip(";\n"))
    generated = localized_output / "assets" / "page-i18n"
    for asset in generated.iterdir():
        name, digest = re.fullmatch(r"([a-z]+)-([a-f0-9]{12})\.js", asset.name).groups()
        assert hashlib.sha256(asset.read_bytes()).hexdigest()[:12] == digest, asset.name
        limit = 4_000 if name == "loader" else len(full_source.encode()) // 4
        assert asset.stat().st_size < limit, (asset.name, asset.stat().st_size)

    loaders = set()
    for filename in (*NONINDEXABLE_HTML, "404.html"):
        html = (localized_output / filename).read_text(encoding="utf-8")
        assert "page-i18n.js" not in html, filename
        tag, = re.findall(r'<script\b[^>]*/assets/page-i18n/[^>]*>', html)
        # document.write only runs from a parser-blocking script, before the runtime.
        loader, = re.fullmatch(r'<script src="(/assets/page-i18n/loader-[a-f0-9]{12}\.js)">', tag).groups()
        assert html.index(tag) < html.index('<script src="/assets/i18n/loader-'), filename
        loaders.add(loader)
    loader, = loaders
    for page_path in localized_output.rglob("*.html"):
        assert not re.search(r'src="[^"]*page-i18n\.js', page_path.read_text(encoding="utf-8")), page_path

    loaded_scenarios = _run_loader(localized_output, loader, execute=True)
    for (pathname, saved, browser, language), loaded in zip(LOADER_SCENARIOS, loaded_scenarios, strict=True):
        if language == "tr" and all(row[0] == source for source, row in full.items()):
            # Turkish page copy is its own source text: Turkish readers load no catalog.
            assert loaded["sources"] == [], pathname
        else:
            source, = loaded["sources"]
            assert re.fullmatch(rf"/assets/page-i18n/{language}-[a-f0-9]{{12}}\.js", source), (pathname, source)
            if language != "tr":
                assert loaded["rows"].keys() == full.keys(), pathname
        index = LANGUAGES.index(language)
        assert all(
            _resolve_page_copy(loaded["rows"], source, index) == _resolve_page_copy(full, source, index)
            for source in full
        ), (pathname, saved, browser)


# One catalog row of i18n.js, matched as the build's publicRuntimeSource matches it.
RUNTIME_ROW = re.compile(r"""^(\s*,?["']([^"']+)["']\s*:\s*)(\[[^\r\n]+\])(\s*,?)$""", re.MULTILINE)
FULL_RUNTIME_REFERENCE = re.compile(r'\bsrc="(?:[^"]*/)?i18n\.js\b', re.IGNORECASE)


def _runtime_parts(source: str) -> tuple[str, dict[str, list[str | None]]]:
    # The runtime code with every catalog row emptied, and the rows by key.
    rows: dict[str, list[str | None]] = {}

    def empty(match: re.Match[str]) -> str:
        rows[match.group(2)] = json.loads(match.group(3))
        return f"{match.group(1)}[]{match.group(4)}"

    return RUNTIME_ROW.sub(empty, source), rows


def test_app_pages_load_only_the_readers_runtime(localized_output: Path) -> None:
    full_source = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    full_code, full_rows = _runtime_parts(full_source)
    runtimes = {}
    for asset in (localized_output / "assets" / "i18n").iterdir():
        name, digest = re.fullmatch(r"([a-z]+)-([a-f0-9]{12})\.js", asset.name).groups()
        assert hashlib.sha256(asset.read_bytes()).hexdigest()[:12] == digest, asset.name
        limit = 4_000 if name == "loader" else len(full_source.encode()) // 2
        assert asset.stat().st_size < limit, (asset.name, asset.stat().st_size)
        if name != "loader":
            runtimes[name] = f"/assets/i18n/{asset.name}"
    assert sorted(runtimes) == sorted(LANGUAGES)

    # i18n.js t()/exact(): row[index] || row[1].
    resolve = lambda row, index: next((value for value in row[index:index + 1] + row[1:2] if value), None)
    for language, source in runtimes.items():
        code, rows = _runtime_parts((localized_output / source.lstrip("/")).read_text(encoding="utf-8"))
        # Only other languages' values are dropped: the same code, keys and
        # Turkish source text, and the same value for the reader's language.
        assert code == full_code, language
        assert rows.keys() == full_rows.keys(), language
        index = LANGUAGES.index(language)
        for key, row in full_rows.items():
            assert rows[key][0] == row[0] and resolve(rows[key], index) == resolve(row, index), (language, key)

    loaders = set()
    for filename in (*NONINDEXABLE_HTML, "404.html"):
        html = (localized_output / filename).read_text(encoding="utf-8")
        tag, = re.findall(r'<script\b[^>]*/assets/i18n/[^>]*>', html)
        loader, = re.fullmatch(r'<script src="(/assets/i18n/loader-[a-f0-9]{12}\.js)">', tag).groups()
        loaders.add(loader)
    loader, = loaders
    # No page keeps the all-language runtime; it stays deployed only as the fallback.
    for page_path in localized_output.rglob("*.html"):
        assert not FULL_RUNTIME_REFERENCE.search(page_path.read_text(encoding="utf-8")), page_path
    assert (localized_output / "i18n.js").read_bytes() == full_source.encode()

    # Both app-page loaders run the same copied language choice; only their file maps differ.
    choice = lambda path: re.sub(r"const script = \{[^}]*\}\[selected\];", "", path.read_text(encoding="utf-8"))
    page_copy_loader, = (localized_output / "assets" / "page-i18n").glob("loader-*.js")
    assert choice(page_copy_loader) == choice(localized_output / loader.lstrip("/"))
    loaded_scenarios = _run_loader(localized_output, loader, execute=False)
    for (pathname, saved, browser, language), loaded in zip(LOADER_SCENARIOS, loaded_scenarios, strict=True):
        assert loaded["sources"] == [runtimes[language]], (pathname, saved, browser)


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


def test_locale_rewrites_only_serve_noindex_app_pages_and_real_assets(localized_output: Path) -> None:
    lines = (localized_output / "_redirects").read_text(encoding="utf-8").splitlines()
    rules = [line.split() for line in lines if line.strip() and not line.startswith("#")]
    assert len(rules) < 2000
    # The non-locale rewrites recover retired JS bundles. They must stay
    # unforced and single-segment; all app/asset locale routes remain exact.
    fallbacks = {"/assets/i18n/:bundle": "/i18n.js", "/assets/page-i18n/:bundle": "/page-i18n.js"}
    for fallback, target in fallbacks.items():
        assert [rule for rule in rules if rule[0] == fallback] == [[fallback, target, "200"]]
        assert (localized_output / target.lstrip("/")).read_bytes() == (FRONTEND / target.lstrip("/")).read_bytes()
    rewrites = {source: target for source, target, status in rules
                if status == "200" and source not in fallbacks}
    assert rewrites
    for source, target in rewrites.items():
        assert "*" not in source and ":" not in source, source
        assert source.split("/")[1] in LANGUAGES[1:]
        target_file = localized_output / target.lstrip("/")
        assert target_file.is_file(), (source, target)
        if target_file.suffix == ".html":
            page = _parse_html(target_file)
            assert not page.canonicals, (source, target)
            assert any("noindex" in value for value in page.robots), (source, target)
        else:
            assert target.startswith("/assets/") or target_file.suffix in {
                ".js", ".css", ".png", ".svg", ".jpg", ".webp", ".ico", ".woff2",
            }
    for language in LANGUAGES[1:]:
        for filename in NONINDEXABLE_HTML:
            assert rewrites[f"/{language}/{filename}"] == f"/{filename}"
            assert rewrites[f"/{language}/{Path(filename).stem}"] == f"/{filename}"
        assert rewrites[f"/{language}/auth.css"] == "/auth.css"
        assert rewrites[f"/{language}/assets/study/cornell-notes-en.txt"] == "/assets/study/cornell-notes-en.txt"
        for nested in ("en/features", "de/", "sitemap.xml", "features", "not-a-real-seo-page"):
            assert f"/{language}/{nested}" not in rewrites


def test_production_host_alias_redirect_precedes_path_rules(localized_output: Path) -> None:
    rules = [line.split() for line in (localized_output / "_redirects").read_text().splitlines()
             if line.strip() and not line.startswith("#")]
    for position, scheme in enumerate(("http", "https")):
        assert rules[position] == [
            f"{scheme}://clever-horse-22b1a8.netlify.app/*", f"{ORIGIN}/:splat", "301!",
        ]
    assert all("deploy-preview" not in rule[0] for rule in rules)


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


def _operator_section(html: str) -> str:
    sections = re.findall(r'<section class="legal-operator" data-legal-operator="static">.*?</section>', html, re.S)
    assert len(sections) == 1
    return sections[0]


def test_unconfigured_build_renders_no_static_seller_identity(localized_output: Path) -> None:
    for page_path in localized_output.rglob("*.html"):
        html = page_path.read_text(encoding="utf-8")
        assert 'data-legal-operator="static"' not in html, page_path
        assert "data-about-operator" not in html, page_path


def test_configured_build_prerenders_seller_identity_without_the_api(tmp_path: Path) -> None:
    dist = _build_site(tmp_path / "operator-build", {
        "LEGAL_OPERATOR_NAME": "Synthetic <Seller> & Co",
        "LEGAL_OPERATOR_ADDRESS": "Synthetic Street 1, Test City",
        "LEGAL_OPERATOR_COUNTRY": "TR",
        "LEGAL_OPERATOR_PHONE": "+90 500 000 00 00",
        "LEGAL_OPERATOR_EMAIL": "seller@example.invalid",
        "LEGAL_TAX_OFFICE": "Synthetic Office",
    })

    english = _operator_section((dist / "en" / "distance-sales.html").read_text(encoding="utf-8"))
    assert "<h2>Service provider details</h2>" in english
    assert "<dt>Legal name</dt><dd>Synthetic &lt;Seller&gt; &amp; Co</dd>" in english
    assert "<dt>Registered address</dt><dd>Synthetic Street 1, Test City</dd>" in english
    assert '<a href="tel:+905000000000">+90 500 000 00 00</a>' in english
    assert '<a href="mailto:seller@example.invalid">seller@example.invalid</a>' in english
    assert "<dt>Tax office</dt><dd>Synthetic Office</dd>" in english
    assert "Tax ID" not in english and "MERSIS" not in english
    assert "<Seller>" not in (dist / "en" / "distance-sales.html").read_text(encoding="utf-8")

    turkish_contact = (dist / "contact.html").read_text(encoding="utf-8")
    assert "<h2>Hizmet sağlayıcı bilgileri</h2>" in _operator_section(turkish_contact)
    # The block follows the page lead so the heading order stays h1 -> h2.
    assert turkish_contact.index('class="lead"') < turkish_contact.index("data-legal-operator")

    german_about = (dist / "de" / "about.html").read_text(encoding="utf-8")
    assert (
        '<p class="about-operator" data-about-operator>LectureSift wird von '
        "<strong>Synthetic &lt;Seller&gt; &amp; Co</strong> betrieben. "
        '<a href="/de/distance-sales">' in german_about
    )
    assert "Synthetic Street" not in german_about
    for unrelated in ("privacy.html", "terms.html", "index.html"):
        assert "Synthetic" not in (dist / "en" / unrelated).read_text(encoding="utf-8")
