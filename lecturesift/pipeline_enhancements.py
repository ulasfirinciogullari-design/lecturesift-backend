"""Idempotent quality enhancements shared by web and Celery workers."""

from __future__ import annotations

import json
import shutil
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

    def enhanced(job_dir: Path, result: dict, slides_dir: Path):
        _normalize_flashcards(result)
        artifacts, _old_zip = original(job_dir, result, slides_dir)
        package_dir = job_dir / "package"

        for artifact in artifacts:
            filename = str(artifact.get("file", ""))
            if filename.startswith("Ders_Notlari."):
                source = package_dir / filename
                target_name = filename.replace("Ders_Notlari.", "Akilli_Notlar.", 1)
                target = package_dir / target_name
                if source.exists():
                    source.replace(target)
                artifact["file"] = target_name
                artifact["label"] = str(artifact.get("label", "")).replace("Ders Notları", "Akıllı Notlar")

        result_path = job_dir / "result.json"
        result_path.write_text(
            json.dumps({**result, "artifacts": artifacts}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        zip_base = job_dir / "LectureSift_Study_Pack"
        zip_path = zip_base.with_suffix(".zip")
        if zip_path.exists():
            zip_path.unlink()
        shutil.make_archive(str(zip_base), "zip", root_dir=package_dir)
        for old in job_dir.glob("LectureSift_Study_Pack_V*.zip"):
            old.unlink(missing_ok=True)
        return artifacts, zip_path

    pipeline.build_artifacts = enhanced
    _INSTALLED = True
