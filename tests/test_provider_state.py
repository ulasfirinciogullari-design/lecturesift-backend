import json

import pytest
from fastapi import HTTPException

from lecturesift.errors import LectureSiftError
from lecturesift import app as app_module
from lecturesift.app import _job_requires_ai
from lecturesift.app import _validate_document_job_type
from lecturesift.provider_state import AIProviderCircuitBreaker


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.ttls.pop(key, None)


class UnavailableRedis(FakeRedis):
    def get(self, key: str) -> str | None:
        raise TimeoutError("redis unavailable")

    def setex(self, key: str, ttl: int, value: str) -> None:
        raise TimeoutError("redis unavailable")


def test_quota_failure_trips_shared_provider_circuit() -> None:
    redis = FakeRedis()
    breaker = AIProviderCircuitBreaker(redis_client=redis, cooldown_seconds=120)

    assert breaker.trip("LS-AI-01") is True
    state = json.loads(redis.values[breaker.REDIS_KEY])
    assert state["code"] == "LS-AI-01"
    assert redis.ttls[breaker.REDIS_KEY] == 120
    with pytest.raises(LectureSiftError) as caught:
        breaker.require_available()
    assert caught.value.code == "LS-AI-01"
    assert "plan dakikan" in caught.value.user_message


def test_provider_circuit_is_visible_to_another_process_instance() -> None:
    """The API must observe the outage first recorded by a worker process."""
    redis = FakeRedis()
    worker_breaker = AIProviderCircuitBreaker(redis_client=redis, cooldown_seconds=120)
    api_breaker = AIProviderCircuitBreaker(redis_client=redis, cooldown_seconds=120)

    assert worker_breaker.trip("LS-AI-01") is True
    state = api_breaker.status()

    assert state is not None
    assert state["provider"] == "openai"
    assert state["code"] == "LS-AI-01"
    assert state["blocked"] is True
    assert 1 <= state["retry_after_seconds"] <= 120


def test_shared_provider_marker_contains_no_customer_or_secret_data() -> None:
    redis = FakeRedis()
    breaker = AIProviderCircuitBreaker(redis_client=redis, cooldown_seconds=120)

    breaker.trip("LS-AI-03")
    stored = json.loads(redis.values[breaker.REDIS_KEY])

    assert set(stored) == {"provider", "code", "blocked_at", "retry_at"}
    serialized = json.dumps(stored).casefold()
    for forbidden in ("api_key", "authorization", "email", "user_id", "job_id", "technical"):
        assert forbidden not in serialized


def test_transient_rate_limit_does_not_block_later_uploads() -> None:
    breaker = AIProviderCircuitBreaker(redis_client=FakeRedis(), cooldown_seconds=120)
    assert breaker.trip("LS-AI-02") is False
    assert breaker.status() is None
    breaker.require_available()


def test_provider_guard_fails_open_if_shared_marker_store_is_unavailable() -> None:
    breaker = AIProviderCircuitBreaker(redis_client=UnavailableRedis(), cooldown_seconds=120)

    assert breaker.status() is None
    assert breaker.trip("LS-AI-01") is False
    # The process that observed the provider failure still protects itself even
    # when Redis could not distribute that marker to the other processes.
    assert breaker.status()["code"] == "LS-AI-01"


def test_expired_local_marker_clears_automatically() -> None:
    breaker = AIProviderCircuitBreaker(redis_url="", cooldown_seconds=60)
    breaker.trip("LS-AI-03")
    assert breaker._local is not None
    breaker._local["retry_at"] = 0
    assert breaker.status() is None


def test_clear_allows_operator_retry() -> None:
    redis = FakeRedis()
    breaker = AIProviderCircuitBreaker(redis_client=redis, cooldown_seconds=120)
    breaker.trip("LS-AI-01")
    breaker.clear()
    assert breaker.status() is None


def test_local_only_exports_and_transcript_only_ocr_remain_available() -> None:
    assert not _job_requires_ai({"job_type": "audio_export"})
    assert not _job_requires_ai({"job_type": "download_video"})
    assert not _job_requires_ai(
        {
            "job_type": "study_pack",
            "include_summary": False,
            "quiz_count": 0,
            "flashcard_count": 0,
        },
        document_mode=True,
    )
    assert _job_requires_ai(
        {
            "job_type": "study_pack",
            "include_summary": False,
            "quiz_count": 0,
            "flashcard_count": 0,
        },
        document_mode=False,
    )


def test_documents_reject_media_only_job_types_before_upload() -> None:
    for job_type in ("audio_export", "download_video"):
        with pytest.raises(LectureSiftError) as caught:
            _validate_document_job_type({"job_type": job_type}, document_mode=True)
        assert caught.value.code == "LS-OUTPUT-02"
        assert caught.value.status_code == 400
    _validate_document_job_type({"job_type": "study_pack"}, document_mode=True)


def test_provider_gate_returns_public_503_and_retry_after_without_blocking_local_work(
    monkeypatch,
) -> None:
    breaker = AIProviderCircuitBreaker(redis_client=FakeRedis(), cooldown_seconds=120)
    breaker.trip("LS-AI-01")
    monkeypatch.setattr(app_module, "AI_PROVIDER_CIRCUIT", breaker)

    with pytest.raises(HTTPException) as caught:
        app_module._require_ai_provider({"job_type": "study_pack"})

    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "LS-AI-01"
    assert "technical" not in caught.value.detail
    assert 1 <= int(caught.value.headers["Retry-After"]) <= 120

    # These paths do not spend OpenAI credits and must remain usable while the
    # provider balance or credentials are unavailable.
    app_module._require_ai_provider({"job_type": "audio_export"})
    app_module._require_ai_provider({"job_type": "download_video"})
    app_module._require_ai_provider(
        {
            "job_type": "study_pack",
            "include_summary": False,
            "quiz_count": 0,
            "flashcard_count": 0,
        },
        document_mode=True,
    )
