import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")
PUBLIC_ROUTES = ("/", "/features", "/plans", "/about")


def expected_preview_paths() -> set[str]:
    paths = set(PUBLIC_ROUTES)
    for language in LANGUAGES:
        paths.add(f"/{language}/")
        paths.update(f"/{language}{route}" for route in PUBLIC_ROUTES[1:])
    return paths


def test_nonce_plugin_is_pinned_and_limited_to_deploy_previews() -> None:
    source = (ROOT / "netlify.toml").read_text(encoding="utf-8")
    config = tomllib.loads(source)

    assert source.count('package = "@netlify/plugin-csp-nonce"') == 1
    assert "plugins" not in config
    contexts = config["context"]
    plugins = contexts["deploy-preview"]["plugins"]
    assert len(plugins) == 1
    assert plugins[0]["package"] == "@netlify/plugin-csp-nonce"
    assert set(plugins[0]["inputs"]) == {"reportOnly", "path"}
    assert plugins[0]["inputs"]["reportOnly"] is True
    for name, context in contexts.items():
        if name != "deploy-preview":
            assert all(
                plugin.get("package") != "@netlify/plugin-csp-nonce"
                for plugin in context.get("plugins", [])
            )


def test_preview_nonce_paths_are_an_exact_public_page_allowlist() -> None:
    config = tomllib.loads((ROOT / "netlify.toml").read_text(encoding="utf-8"))
    paths = config["context"]["deploy-preview"]["plugins"][0]["inputs"]["path"]

    assert len(paths) == 52
    assert set(paths) == expected_preview_paths()
    assert all("*" not in path and not path.endswith(".html") for path in paths)
    private_roots = ("account", "admin", "assistant", "login", "register", "support", "workspace")
    assert all(not any(segment in path.split("/") for segment in private_roots) for path in paths)


def test_nonce_plugin_dependency_and_lock_are_exact() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))

    assert package["private"] is True
    assert package["devDependencies"] == {"@netlify/plugin-csp-nonce": "1.6.2"}
    assert "dependencies" not in package
    assert lock["lockfileVersion"] == 3
    assert lock["packages"][""]["devDependencies"] == package["devDependencies"]
    plugin = lock["packages"]["node_modules/@netlify/plugin-csp-nonce"]
    assert plugin["version"] == "1.6.2"
    assert plugin["integrity"] == (
        "sha512-d4DCoJmqXqtxIjTeJ7Hyo0d9EvF+9rEhSuiLYnUGa2pdkNI65PzO23TAgIdV"
        "pnqdpSiS8/PMfvvr5xoE1F418w=="
    )
