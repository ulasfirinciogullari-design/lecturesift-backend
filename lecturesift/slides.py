from collections import Counter
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


ProgressCallback = Callable[[float, str], None]
_FACE_CLASSIFIER = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def dhash(frame: np.ndarray) -> np.ndarray:
    gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (17, 16), interpolation=cv2.INTER_AREA)
    return (gray[:, 1:] > gray[:, :-1]).flatten()


def hamming(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.count_nonzero(left != right)) / len(left)


def _scaled(frame: np.ndarray, width: int) -> np.ndarray:
    height = max(1, int(frame.shape[0] * width / frame.shape[1]))
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)


def face_area_ratio(frame: np.ndarray) -> float:
    small = _scaled(frame, 360)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    faces = _FACE_CLASSIFIER.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(36, 36))
    area = max(1, small.shape[0] * small.shape[1])
    return max((fw * fh for _, _, fw, fh in faces), default=0) / area


def skin_ratio(frame: np.ndarray) -> float:
    small = _scaled(frame, 240)
    ycrcb = cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb)
    mask = cv2.inRange(
        ycrcb,
        np.array([0, 133, 77], dtype=np.uint8),
        np.array([255, 173, 127], dtype=np.uint8),
    )
    return float(np.count_nonzero(mask)) / mask.size


def _layout_metrics(frame: np.ndarray) -> dict[str, float | int]:
    small = _scaled(frame, 360)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 70, 160)
    edge_density = float(np.count_nonzero(edges)) / edges.size

    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    flat_ratio = float(np.mean(np.abs(laplacian) < 8))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    saturation = float(np.mean(hsv[:, :, 1])) / 255.0

    threshold = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        19,
        9,
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(threshold, connectivity=8)
    text_components = 0
    for index in range(1, count):
        _, _, width, height, area = stats[index]
        if 3 <= height <= 32 and 2 <= width <= 170 and 8 <= area <= 1800 and width / max(height, 1) <= 14:
            text_components += 1

    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (24, 1))
    horizontal = cv2.morphologyEx(threshold, cv2.MORPH_OPEN, horizontal_kernel)
    line_density = float(np.count_nonzero(horizontal)) / horizontal.size

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    frame_area = small.shape[0] * small.shape[1]
    rectangles = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if not frame_area * 0.012 <= area <= frame_area * 0.88:
            continue
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        if len(polygon) == 4 and cv2.isContourConvex(polygon):
            rectangles += 1

    return {
        "edge_density": edge_density,
        "flat_ratio": flat_ratio,
        "saturation": saturation,
        "text_components": text_components,
        "line_density": line_density,
        "rectangles": rectangles,
    }


def presentation_score(frame: np.ndarray) -> tuple[int, dict]:
    metrics = _layout_metrics(frame)
    face = face_area_ratio(frame)
    skin = skin_ratio(frame)
    metrics["face_ratio"] = face
    metrics["skin_ratio"] = skin

    score = 0
    if metrics["edge_density"] >= 0.035:
        score += 1
    if metrics["edge_density"] >= 0.060:
        score += 1
    if metrics["flat_ratio"] >= 0.48:
        score += 2
    if metrics["saturation"] <= 0.48:
        score += 1
    if metrics["text_components"] >= 5:
        score += 2
    if metrics["text_components"] >= 12:
        score += 2
    if metrics["line_density"] >= 0.004 and (metrics["text_components"] >= 3 or metrics["edge_density"] >= 0.025):
        score += 2
    if metrics["rectangles"] >= 2:
        score += 1

    if face >= 0.018:
        score -= 5
    if face >= 0.050:
        score -= 5
    if skin >= 0.20:
        score -= 3
    if skin >= 0.30:
        score -= 4

    has_layout = bool(
        metrics["text_components"] >= 5
        or (metrics["line_density"] >= 0.004 and (metrics["text_components"] >= 3 or metrics["edge_density"] >= 0.025))
        or metrics["rectangles"] >= 2
    )
    metrics["has_layout"] = has_layout
    rounded = {
        key: round(float(value), 4) if isinstance(value, (float, np.floating)) else value
        for key, value in metrics.items()
    }
    return score, rounded


def fullness_score(frame: np.ndarray) -> float:
    small = _scaled(frame, 320)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 150)
    edge = float(np.count_nonzero(edges)) / edges.size
    histogram = cv2.calcHist([gray], [0], None, [32], [0, 256]).ravel()
    probability = histogram / max(histogram.sum(), 1)
    probability = probability[probability > 0]
    entropy = float(-(probability * np.log2(probability)).sum()) / 5.0
    return edge * 2 + entropy


def read_frame_at(video_path: Path, second: float) -> np.ndarray | None:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError("Video could not be opened.")
    capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
    ok, frame = capture.read()
    capture.release()
    return frame if ok else None


def scan_candidate_timestamps(
    video_path: Path,
    progress: ProgressCallback,
) -> tuple[list[float], float]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError("Video could not be opened.")

    fps = capture.get(cv2.CAP_PROP_FPS) or 25
    total_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = total_frames / fps if fps else 0
    step = 1.5 if duration <= 600 else (2.5 if duration <= 3600 else 4.0)
    target_width = 144

    previous = None
    last_kept = -999.0
    timestamps: list[float] = []
    current = 0.0
    total_steps = max(1, int(duration / step) + 1)
    iteration = 0

    while current <= duration:
        capture.set(cv2.CAP_PROP_POS_MSEC, current * 1000)
        ok, frame = capture.read()
        if ok and frame is not None:
            gray = cv2.cvtColor(_scaled(frame, target_width), cv2.COLOR_BGR2GRAY)
            if previous is None:
                timestamps.append(current)
                last_kept = current
            else:
                difference = float(np.mean(cv2.absdiff(gray, previous))) / 255.0
                if difference > 0.092:
                    timestamps.append(current)
                    last_kept = current
                elif difference < 0.019 and current - last_kept >= 10.5:
                    timestamps.append(current)
                    last_kept = current
            previous = gray

        iteration += 1
        if iteration % 8 == 0:
            progress(55 * iteration / total_steps, "scene_scan")
        current += step

    capture.release()
    return timestamps, duration


def extract_slides(
    video_path: Path,
    slides_dir: Path,
    progress: ProgressCallback,
) -> tuple[list[dict], dict]:
    slides_dir.mkdir(parents=True, exist_ok=True)
    timestamps, duration = scan_candidate_timestamps(video_path, progress)
    progress(58, "slide_validation")

    accepted: list[dict] = []
    rejection_counts: Counter[str] = Counter()
    for index, second in enumerate(timestamps):
        frame = read_frame_at(video_path, second)
        if frame is None:
            rejection_counts["unreadable"] += 1
            continue
        score, metrics = presentation_score(frame)
        reasons: list[str] = []
        if not metrics["has_layout"]:
            reasons.append("no_slide_layout")
        if metrics["face_ratio"] >= 0.035:
            reasons.append("face")
        if metrics["skin_ratio"] >= 0.27:
            reasons.append("person_or_skin")
        if score < 7:
            reasons.append("low_score")

        if not reasons:
            accepted.append(
                {
                    "time": second,
                    "hash": dhash(frame),
                    "fullness": fullness_score(frame),
                    "score": score,
                    "metrics": metrics,
                }
            )
        else:
            rejection_counts.update(reasons)
        del frame
        if index % 4 == 0:
            progress(58 + 24 * (index + 1) / max(1, len(timestamps)), "slide_validation")

    groups: list[list[dict]] = []
    for item in accepted:
        if not groups:
            groups.append([item])
            continue
        previous = groups[-1][-1]
        if item["time"] - previous["time"] <= 16 and hamming(item["hash"], previous["hash"]) < 0.20:
            groups[-1].append(item)
        else:
            groups.append([item])

    # A real slide usually persists. Single-frame groups must have very strong
    # layout evidence; this removes most talking-head and room false positives.
    persistent_groups = [group for group in groups if len(group) >= 2 or max(x["score"] for x in group) >= 9]
    rejection_counts["not_persistent"] += len(groups) - len(persistent_groups)

    representatives = [max(group, key=lambda item: (item["fullness"], item["time"])) for group in persistent_groups]
    unique: list[dict] = []
    for item in representatives:
        duplicate_index = next(
            (index for index, old in enumerate(unique) if hamming(item["hash"], old["hash"]) < 0.055),
            None,
        )
        if duplicate_index is None:
            unique.append(item)
        elif item["fullness"] > unique[duplicate_index]["fullness"] * 1.03:
            unique[duplicate_index] = item

    unique.sort(key=lambda item: item["time"])
    manifest: list[dict] = []
    for index, item in enumerate(unique, 1):
        frame = read_frame_at(video_path, item["time"])
        if frame is None:
            continue
        second = item["time"]
        filename = f"slide_{index:03d}_{int(second // 60):02d}m{int(second % 60):02d}s.jpg"
        cv2.imwrite(str(slides_dir / filename), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        del frame
        manifest.append(
            {
                "file": filename,
                "second": round(second, 1),
                "timestamp": f"{int(second // 60):02d}:{int(second % 60):02d}",
                "slide_score": item["score"],
                **item["metrics"],
            }
        )
        progress(83 + 17 * index / max(1, len(unique)), "slide_export")

    diagnostics = {
        "engine": "v4-layout-persistence",
        "memory_mode": "timestamp_only",
        "duration_seconds": round(duration, 1),
        "fast_candidates": len(timestamps),
        "presentation_candidates": len(accepted),
        "persistent_groups": len(persistent_groups),
        "final_unique_slides": len(manifest),
        "rejections": dict(rejection_counts),
    }
    progress(100, "visual_done")
    return manifest, diagnostics
