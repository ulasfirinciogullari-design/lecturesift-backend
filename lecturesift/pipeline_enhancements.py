"""Idempotent quality enhancements shared by web and Celery workers."""

from __future__ import annotations

from pathlib import Path


_INSTALLED = False


def _question_front(front: str, language: str) -> str:
    value = " ".join(str(front or "").split())
    if not value:
        return "Bu kavram nedir?" if language == "tr" else "What is this concept?"
    if value.rstrip().endswith(("?", "？", "؟")):
        return value
    if language == "tr":
        return f"{value} nedir?"
    if language == "de":
        return f"Was ist {value}?"
    if language == "fr":
        return f"Qu’est-ce que {value} ?"
    if language == "es":
        return f"¿Qué es {value}?"
    return f"What is {value}?"


def _normalize_flashcards(result: dict) -> None:
    language = str(result.get("options", {}).get("output_language") or "tr")
    normalized: list[dict] = []
    for item in result.get("flashcards") or []:
        if not isinstance(item, dict):
            continue
        back = " ".join(str(item.get("back") or item.get("answer") or "").split())
        front = item.get("front") or item.get("question") or ""
        if not back:
            continue
        normalized.append({"front": _question_front(str(front), language), "back": back})
    result["flashcards"] = normalized


def install_pipeline_enhancements() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import pipeline

    original = pipeline.build_artifacts

    def enhanced(
        job_dir: Path,
        result: dict,
        slides_dir: Path,
        *,
        audio_source: Path | None = None,
    ):
        _normalize_flashcards(result)
        # Ask the exporter for the rollout names up front.  The previous
        # wrapper renamed the notes only after export, rebuilt the complete ZIP,
        # rewrote result.json, and then deleted the first archive.
        return original(
            job_dir,
            result,
            slides_dir,
            audio_source=audio_source,
            notes_stem="Akilli_Notlar",
            notes_label="Akıllı Notlar",
            archive_stem="LectureSift_Study_Pack",
        )

    pipeline.build_artifacts = enhanced
    _INSTALLED = True
