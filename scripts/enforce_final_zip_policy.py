from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    found = text.count(old)
    if found != count:
        raise RuntimeError(f"{path}: expected {count}, found {found}: {old[:120]!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


# The persistent object is the final user-facing ZIP itself. Do not add hidden
# JSON/image payloads to PDF-only or user-selected output packages.
replace(
    "lecturesift/pipeline_enhancements.py",
    "        _embed_recovery_payload(package_dir, result, artifacts, slides_dir)\n",
    "",
)
replace(
    "lecturesift/exports.py",
    "    internal = package_dir / \"_lecturesift\"\n    internal.mkdir(parents=True, exist_ok=True)\n    (internal / \"result.json\").write_text(json.dumps({**result, \"artifacts\": artifacts}, ensure_ascii=False, indent=2), encoding=\"utf-8\")\n",
    "",
)
print("Final ZIP remains user-facing and contains only selected outputs.")
