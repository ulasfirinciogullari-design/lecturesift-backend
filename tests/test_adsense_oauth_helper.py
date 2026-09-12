from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
from urllib.parse import parse_qs, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "deploy" / "adsense_oauth_loopback.py"
SPEC = importlib.util.spec_from_file_location("adsense_oauth_loopback", HELPER_PATH)
assert SPEC and SPEC.loader
oauth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oauth)


def test_authorization_url_is_fixed_read_only_state_and_pkce():
    verifier, challenge = oauth.pkce_pair()
    assert 43 <= len(verifier) <= 128
    parsed = urlsplit(
        oauth.authorization_url(
            "123-example.apps.googleusercontent.com",
            "http://127.0.0.1:49152/",
            "synthetic-state",
            challenge,
        )
    )
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == oauth.AUTHORIZATION_ENDPOINT
    query = parse_qs(parsed.query)
    assert query == {
        "client_id": ["123-example.apps.googleusercontent.com"],
        "redirect_uri": ["http://127.0.0.1:49152/"],
        "response_type": ["code"],
        "scope": ["https://www.googleapis.com/auth/adsense.readonly"],
        "state": ["synthetic-state"],
        "code_challenge": [challenge],
        "code_challenge_method": ["S256"],
        "access_type": ["offline"],
        "prompt": ["consent"],
        "include_granted_scopes": ["false"],
    }
    assert "https://www.googleapis.com/auth/adsense" not in query["scope"]


def test_desktop_client_and_output_are_private_and_outside_repository(tmp_path: Path):
    tmp_path.chmod(0o700)
    client = tmp_path / "desktop-client.json"
    client.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "123-example.apps.googleusercontent.com",
                    "client_secret": "synthetic-secret",
                    "auth_uri": oauth.AUTHORIZATION_ENDPOINT,
                    "token_uri": oauth.TOKEN_ENDPOINT,
                }
            }
        ),
        encoding="utf-8",
    )
    client.chmod(0o600)
    assert oauth.load_desktop_client(client) == (
        "123-example.apps.googleusercontent.com",
        "synthetic-secret",
    )
    untrusted = json.loads(client.read_text(encoding="utf-8"))
    untrusted["installed"]["token_uri"] = "https://example.test/token"
    client.write_text(json.dumps(untrusted), encoding="utf-8")
    with pytest.raises(oauth.OAuthSetupError, match="fixed Google endpoint"):
        oauth.load_desktop_client(client)
    untrusted["installed"]["token_uri"] = oauth.TOKEN_ENDPOINT
    client.write_text(json.dumps(untrusted), encoding="utf-8")

    output = tmp_path / "adsense-api.env"
    oauth.write_private_environment(
        output,
        client_id="123-example.apps.googleusercontent.com",
        client_secret="synthetic-secret",
        refresh_token="synthetic-refresh-token",
        account_name="accounts/pub-1234567890",
        site_domain="lecturesift.com",
        replace=False,
    )
    content = output.read_text(encoding="utf-8")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "LECTURESIFT_ADSENSE_API_ENABLED=false" in content
    assert "LECTURESIFT_ADSENSE_API_REFRESH_TOKEN=synthetic-refresh-token" in content
    assert "ACCESS_TOKEN" not in content
    with pytest.raises(oauth.OAuthSetupError, match="--replace"):
        oauth.validate_private_output(output, replace=False)


def test_token_exchange_uses_fixed_endpoint_and_never_prints_token(monkeypatch, capsys):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            assert size == oauth.MAX_TOKEN_RESPONSE_BYTES + 1
            return json.dumps(
                {
                    "access_token": "synthetic-access-token",
                    "refresh_token": "synthetic-refresh-token",
                    "scope": oauth.ADSENSE_READONLY_SCOPE,
                    "token_type": "Bearer",
                }
            ).encode()

    class Opener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.method
            captured["body"] = parse_qs(request.data.decode())
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(oauth, "build_opener", lambda *handlers: Opener())
    token = oauth.exchange_code(
        client_id="123-example.apps.googleusercontent.com",
        client_secret="synthetic-secret",
        code="synthetic-code",
        verifier="synthetic-verifier",
        redirect_uri="http://127.0.0.1:49152/",
        timeout_seconds=7,
    )
    assert token == "synthetic-refresh-token"
    assert captured == {
        "url": "https://oauth2.googleapis.com/token",
        "method": "POST",
        "body": {
            "client_id": ["123-example.apps.googleusercontent.com"],
            "client_secret": ["synthetic-secret"],
            "code": ["synthetic-code"],
            "code_verifier": ["synthetic-verifier"],
            "grant_type": ["authorization_code"],
            "redirect_uri": ["http://127.0.0.1:49152/"],
        },
        "timeout": 7,
    }
    assert "synthetic-refresh-token" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "returned_scope",
    [None, "https://www.googleapis.com/auth/adsense"],
)
def test_token_exchange_requires_the_exact_readonly_scope(monkeypatch, returned_scope):
    document = {
        "access_token": "synthetic-access-token",
        "refresh_token": "synthetic-refresh-token",
        "token_type": "Bearer",
    }
    if returned_scope is not None:
        document["scope"] = returned_scope

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _size):
            return json.dumps(document).encode()

    class Opener:
        def open(self, _request, timeout):
            assert timeout == 7
            return Response()

    monkeypatch.setattr(oauth, "build_opener", lambda *handlers: Opener())
    with pytest.raises(oauth.OAuthSetupError, match="outside the requested read-only scope"):
        oauth.exchange_code(
            client_id="123-example.apps.googleusercontent.com",
            client_secret="synthetic-secret",
            code="synthetic-code",
            verifier="synthetic-verifier",
            redirect_uri="http://127.0.0.1:49152/",
            timeout_seconds=7,
        )


def test_account_name_is_required_and_the_cli_default_is_the_known_account():
    assert oauth.DEFAULT_ACCOUNT_NAME == "accounts/pub-7608481350058806"
    assert oauth._validate_account_name(oauth.DEFAULT_ACCOUNT_NAME) == oauth.DEFAULT_ACCOUNT_NAME
    with pytest.raises(oauth.OAuthSetupError, match="accounts/pub-<digits>"):
        oauth._validate_account_name("")


def test_deployment_keeps_credentials_in_api_role_only():
    generator = (ROOT / "deploy" / "generate_role_envs.py").read_text(encoding="utf-8")
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    example = (ROOT / "deploy" / "env.example").read_text(encoding="utf-8")
    preflight = (ROOT / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    snapshot = (ROOT / "deploy" / "configuration_snapshot.py").read_text(encoding="utf-8")

    for key in (
        "LECTURESIFT_ADSENSE_API_CLIENT_SECRET",
        "LECTURESIFT_ADSENSE_API_REFRESH_TOKEN",
    ):
        assert f'"{key}"' in generator.split("API_SENSITIVE_KEYS", 1)[1].split(
            "SENSITIVE_NAME_MARKERS", 1
        )[0]
        assert render.count(f"- key: {key}") == 1
    assert "LECTURESIFT_ADSENSE_API_CLIENT_ID=\n" in example
    assert "LECTURESIFT_ADSENSE_API_CLIENT_SECRET=\n" in example
    assert "LECTURESIFT_ADSENSE_API_REFRESH_TOKEN=\n" in example
    assert (
        "LECTURESIFT_ADSENSE_API_ACCOUNT_NAME=accounts/pub-7608481350058806"
        in example
    )
    assert "LECTURESIFT_ADSENSE_API_ENABLED=false" in example
    assert "- key: LECTURESIFT_ADSENSE_API_ENABLED\n        sync: false" in render
    assert (
        "- key: LECTURESIFT_ADSENSE_API_ACCOUNT_NAME\n"
        "        value: accounts/pub-7608481350058806"
        in render
    )
    assert "Missing enabled AdSense read-only API values" in preflight
    assert "^accounts/pub-[0-9]+$" in preflight
    assert '"runtime.env"' in snapshot and '"api.env"' in snapshot
    assert oauth.ADSENSE_READONLY_SCOPE in HELPER_PATH.read_text(encoding="utf-8")
