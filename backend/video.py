"""FFmpeg-backed, evidence-constrained video diagnostics.

No timestamp/visual heuristic is labelled proof of capture-side frame loss.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import subprocess
from pathlib import Path
from typing import Any

TARGET_FPS = 30
MAX_SOURCE_FRAMES = 30_000
FFMPEG_TIMEOUT = 110


class VideoValidationError(Exception):
    pass


class VideoProcessingError(Exception):
    """Retryable environment failure; says nothing about video validity."""


def _run(args: list[str], timeout: int = FFMPEG_TIMEOUT) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode < 0:
            raise VideoProcessingError(f"{args[0]} terminated by signal {-result.returncode}; processing may be retried")
        return result
    except subprocess.TimeoutExpired as exc:
        raise VideoProcessingError(f"{args[0]} processing timed out after {timeout} seconds; video validity is unknown") from exc
    except OSError as exc:
        raise VideoProcessingError(f"Cannot run {args[0]}: {exc.strerror or type(exc).__name__}") from exc


def _rational(rate: str | None) -> float | None:
    try:
        num, den = (rate or "0/0").split("/")
        return int(num) / int(den) if int(den) else None
    except (ValueError, ZeroDivisionError):
        return None


def analyse_video(file_path: Path) -> dict[str, Any]:
    """Inspect source frames *before* independent target-30-FPS resampling.

    Raises VideoValidationError on invalid or undecodable video. Generated frame
    observations are warnings unless the metadata/decoder proves the issue.
    """
    metadata = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,format_name:stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,nb_frames,duration",
        "-show_format", "-show_streams", "-of", "json", str(file_path),
    ], timeout=20)
    if metadata.returncode != 0:
        raise VideoValidationError("Container parsing failed: " + metadata.stderr[-500:])
    try:
        details = json.loads(metadata.stdout)
    except json.JSONDecodeError as exc:
        raise VideoValidationError("Invalid ffprobe JSON metadata") from exc
    streams = [s for s in details.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise VideoValidationError("No video stream found")
    stream = streams[0]
    try:
        duration = float(stream.get("duration") or details.get("format", {}).get("duration") or 0)
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VideoValidationError("Invalid video duration/dimensions metadata") from exc
    if not math.isfinite(duration):
        raise VideoValidationError("Invalid nonfinite video duration")
    if not (29.5 <= duration <= 30.5):
        raise VideoValidationError(f"Expected a 30-second video (29.5–30.5s tolerance); received {duration:.3f}s")
    if width <= 0 or height <= 0 or width * height > 3840 * 2160:
        raise VideoValidationError("Video dimensions exceed 3840x2160 processing limit")

    decode = _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-err_detect", "explode",
        "-i", str(file_path), "-map", "0:v:0", "-an", "-f", "null", "-",
    ])
    if decode.returncode != 0:
        raise VideoValidationError("Decoder reported damaged/invalid frames: " + decode.stderr[-700:])

    frames_result = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,pict_type,key_frame",
        "-of", "json", str(file_path),
    ])
    if frames_result.returncode != 0:
        raise VideoValidationError("Source frame inspection failed: " + frames_result.stderr[-500:])
    try:
        raw_frames = json.loads(frames_result.stdout).get("frames", [])
    except json.JSONDecodeError as exc:
        raise VideoValidationError("Cannot parse source-frame metadata") from exc
    if not raw_frames:
        raise VideoValidationError("Video contains no decodable source frames")
    if len(raw_frames) > MAX_SOURCE_FRAMES:
        raise VideoValidationError("Too many decoded frames for bounded inspection")

    source: list[dict[str, Any]] = []
    for i, fr in enumerate(raw_frames):
        try:
            ts = float(fr.get("best_effort_timestamp_time"))
            if not math.isfinite(ts):
                ts = None
        except (TypeError, ValueError):
            ts = None
        source.append({"index": i, "timestamp_s": ts, "picture_type": fr.get("pict_type")})

    intervals = [round(source[i]["timestamp_s"] - source[i-1]["timestamp_s"], 6)
                 for i in range(1, len(source))
                 if source[i]["timestamp_s"] is not None and source[i-1]["timestamp_s"] is not None]
    positive = [dt for dt in intervals if dt > 0]
    nominal_step = statistics.median(positive) if positive else (1 / TARGET_FPS)
    warnings: list[dict[str, Any]] = []
    for i in range(1, len(source)):
        a, b = source[i - 1]["timestamp_s"], source[i]["timestamp_s"]
        if a is None or b is None:
            continue
        delta = b - a
        if delta <= 0:
            warnings.append({"type": "NON_INCREASING_TIMESTAMP", "source_frame": i,
                             "timestamp_s": b, "delta_s": round(delta, 6),
                             "confidence": "heuristic", "interpretation": "May be intentional or reflect timestamp ordering"})
        elif delta > (1.8 * nominal_step + 0.004):
            warnings.append({"type": "SOURCE_TIMESTAMP_GAP", "source_frame": i,
                             "timestamp_s": b, "delta_s": round(delta, 6),
                             "nominal_step_s": round(nominal_step, 6),
                             "estimated_missing_intervals": max(0, round(delta / nominal_step) - 1),
                             "confidence": "heuristic", "interpretation": "A VFR gap or capture/encoding discontinuity; not proof of dropped source frames"})

    # A low-resolution luma content fingerprint flags visually repeated runs. Its
    # lossy downsampling intentionally prevents calling the result a proven duplicate.
    fp = _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-i", str(file_path),
        "-map", "0:v:0", "-vf", "scale=32:32,format=gray", "-fps_mode", "passthrough", "-an", "-f", "framemd5", "-",
    ])
    if fp.returncode != 0:
        raise VideoValidationError("Frame fingerprint generation failed: " + fp.stderr[-500:])
    hashes: list[str] = []
    for line in fp.stdout.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        columns = [part.strip() for part in line.split(",")]
        if len(columns) >= 6:
            hashes.append(columns[-1])
    if hashes:
        streak_start = 0
        for i in range(1, len(hashes) + 1):
            if i < len(hashes) and hashes[i] == hashes[streak_start]:
                continue
            count = i - streak_start
            if count >= 8:
                t = source[streak_start]["timestamp_s"] if streak_start < len(source) else None
                warnings.append({"type": "VISUALLY_REPEATED_RUN", "source_frame": streak_start,
                                 "timestamp_s": t, "run_length": count,
                                 "confidence": "heuristic", "interpretation": "Could be a legitimate static scene or an inserted freeze"})
            streak_start = i

    # Independently materialize the 30fps sampling timeline. This says nothing
    # by itself about whether source frames were dropped at capture time.
    resampled = _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info", "-i", str(file_path),
        "-map", "0:v:0", "-vf", "fps=30,showinfo", "-an", "-f", "null", "-",
    ])
    if resampled.returncode != 0:
        raise VideoValidationError("30 FPS analysis failed: " + resampled.stderr[-500:])
    pattern = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*([-\d.]+)")
    target_frames: list[dict[str, Any]] = []
    for line in resampled.stderr.splitlines():
        m = pattern.search(line)
        if m:
            target_frames.append({"index": int(m.group(1)), "timestamp_s": float(m.group(3))})
    if not target_frames:
        raise VideoValidationError("Cannot extract 30 FPS target frame timeline")

    return {
        "video_valid": True,
        "source": {
            "container": details.get("format", {}).get("format_name"),
            "codec": stream.get("codec_name"),
            "duration_s": round(duration, 4), "width": stream.get("width"), "height": stream.get("height"),
            "avg_frame_rate": stream.get("avg_frame_rate"), "nominal_rate": stream.get("r_frame_rate"),
            "source_frame_count": len(source), "median_source_interval_s": round(nominal_step, 6),
            "frames": source,
        },
        "target_30fps": {"frame_rate": TARGET_FPS, "frame_count": len(target_frames), "frames": target_frames,
                         "warning": "Target resampling may duplicate or omit source frames; target count is not evidence of original frame loss"},
        "anomalies": warnings,
        "anomaly_count": len(warnings),
        "decoder_errors": [],
        "limitations": [
            "An intact SHA-256 proves equality to the client's declared byte sequence, not correct camera capture.",
            "Timestamp gaps are heuristic warnings; variable-frame-rate or encoding decisions may explain them.",
            "Visually repeated frames can be legitimate static scenes; 32x32 luma hashes are not source-reference proofs.",
            "True capture-time loss may require trustworthy capture-side timestamps, sensor logs or a reference manifest.",
        ],
    }
