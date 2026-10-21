"""Processing-time model shared by user-facing ETAs and the worker's time limits."""

from __future__ import annotations

# Seconds of processing per media minute when no completed job has been
# measured yet. Learned rates are clamped to the bounds below.
BASELINE_SECONDS_PER_MEDIA_MINUTE = 30.0
_MIN_SECONDS_PER_MEDIA_MINUTE = 8.0
_MAX_SECONDS_PER_MEDIA_MINUTE = 180.0
_FIXED_OVERHEAD_SECONDS = 45.0
_TRANSFER_BYTES_PER_SECOND = 40 * 1024 * 1024
JOB_TYPE_MULTIPLIERS = {
    "audio_export": 0.22,
    "download_video": 0.12,
}
SUMMARY_STYLE_MULTIPLIERS = {
    "short": 0.9,
    "detailed": 1.15,
    "exam": 1.08,
    "five_minute": 0.92,
}
DOCUMENT_SOURCE_MULTIPLIER = 0.62


def eta_seconds(
    media_minutes: float,
    size_bytes: int,
    seconds_per_minute: float,
    *,
    job_type: str = "study_pack",
    summary_style: str = "standard",
    source_kind: str = "media",
) -> int:
    processing = _FIXED_OVERHEAD_SECONDS + media_minutes * max(
        _MIN_SECONDS_PER_MEDIA_MINUTE, min(seconds_per_minute, _MAX_SECONDS_PER_MEDIA_MINUTE)
    )
    type_multiplier = JOB_TYPE_MULTIPLIERS.get(str(job_type or "study_pack"), 1.0)
    source_multiplier = DOCUMENT_SOURCE_MULTIPLIER if source_kind == "document" else 1.0
    style_multiplier = SUMMARY_STYLE_MULTIPLIERS.get(str(summary_style or "standard"), 1.0)
    processing *= type_multiplier * source_multiplier * style_multiplier
    size_overhead = max(0, int(size_bytes)) / _TRANSFER_BYTES_PER_SECOND
    minimum = 15 if job_type == "download_video" else 20 if job_type == "audio_export" else 35
    return max(minimum, int(round(processing + size_overhead)))
