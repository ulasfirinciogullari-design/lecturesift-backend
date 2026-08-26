"""Low-memory slide engine for LectureSift V3.2.

This module monkey-patches the running app.main module so the stable V3.1
application stays intact while slide scanning uses much less RAM.
"""
from pathlib import Path
import cv2
import numpy as np


def apply(main):
    def read_frame_at(video_path: Path, second: float):
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError("Video could not be opened.")
        cap.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
        ok, frame = cap.read()
        cap.release()
        return frame if ok else None

    def fast_scene_candidates(video_path: Path, job_id: str):
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError("Video could not be opened.")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = total / fps if fps else 0
        step = 2.0 if duration <= 600 else (3.0 if duration <= 3600 else 5.0)
        target_w = 128
        prev = None
        last_kept_t = -999
        timestamps = []
        t = 0.0
        total_steps = max(1, int(duration / step) + 1)
        i = 0
        while t <= duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if ok and frame is not None:
                hh = max(1, int(frame.shape[0] * target_w / frame.shape[1]))
                gray = cv2.cvtColor(
                    cv2.resize(frame, (target_w, hh), interpolation=cv2.INTER_AREA),
                    cv2.COLOR_BGR2GRAY,
                )
                if prev is None:
                    timestamps.append(t)
                    last_kept_t = t
                else:
                    diff = float(np.mean(cv2.absdiff(gray, prev))) / 255.0
                    if diff > 0.09:
                        timestamps.append(t)
                        last_kept_t = t
                    elif diff < 0.020 and t - last_kept_t >= 12:
                        timestamps.append(t)
                        last_kept_t = t
                prev = gray
            i += 1
            if i % 10 == 0:
                main.jobset(job_id, percent=18 + int(17 * i / total_steps), stage="scene_scan")
            t += step
        cap.release()
        return timestamps, duration

    def extract_slides(video_path: Path, slides_dir: Path, job_id: str):
        slides_dir.mkdir(parents=True, exist_ok=True)
        timestamps, duration = fast_scene_candidates(video_path, job_id)
        main.jobset(job_id, percent=36, stage="slide_detection")
        filtered = []
        for idx, t in enumerate(timestamps):
            frame = read_frame_at(video_path, t)
            if frame is None:
                continue
            score, metrics = main.presentation_score(frame)
            if score >= 5 and metrics["face_ratio"] < 0.035 and metrics["skin_ratio"] < 0.30:
                filtered.append({
                    "time": t,
                    "hash": main.dhash(frame),
                    "fullness": main.fullness_score(frame),
                    "score": score,
                    "metrics": metrics,
                })
            del frame
            if idx % 4 == 0:
                main.jobset(job_id, percent=36 + int(11 * (idx + 1) / max(1, len(timestamps))), stage="slide_detection")

        groups = []
        for item in filtered:
            if not groups:
                groups.append([item])
                continue
            prev = groups[-1][-1]
            if item["time"] - prev["time"] <= 16 and main.hamming(item["hash"], prev["hash"]) < 0.20:
                groups[-1].append(item)
            else:
                groups.append([item])

        reps = [max(g, key=lambda x: (x["fullness"], x["time"])) for g in groups]
        unique = []
        for item in reps:
            dup = None
            for i, old in enumerate(unique):
                if main.hamming(item["hash"], old["hash"]) < 0.055:
                    dup = i
                    break
            if dup is None:
                unique.append(item)
            elif item["fullness"] > unique[dup]["fullness"] * 1.03:
                unique[dup] = item

        unique.sort(key=lambda x: x["time"])
        manifest = []
        for i, item in enumerate(unique, 1):
            frame = read_frame_at(video_path, item["time"])
            if frame is None:
                continue
            sec = item["time"]
            fn = f"slide_{i:03d}_{int(sec//60):02d}m{int(sec%60):02d}s.jpg"
            cv2.imwrite(str(slides_dir / fn), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
            del frame
            manifest.append({
                "file": fn,
                "second": round(sec, 1),
                "slide_score": item["score"],
                **item["metrics"],
            })

        return manifest, {
            "duration_seconds": round(duration, 1),
            "fast_candidates": len(timestamps),
            "presentation_candidates": len(filtered),
            "final_unique_slides": len(manifest),
            "memory_mode": "low",
        }

    main.read_frame_at = read_frame_at
    main.fast_scene_candidates = fast_scene_candidates
    main.extract_slides = extract_slides
    main.app.title = "LectureSift Backend V3.2"
    return main.app
