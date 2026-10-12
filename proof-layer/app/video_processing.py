"""Video processing: decode real frames and measure only what pixels can show.

Pure with respect to the database. `inspect` reads a file's header and
streams; `analyze` decodes every video frame and returns plain data;
app/video_evidence.py stores it.

Per frame, on a downscaled grey copy, it measures:
- the presentation timestamp, from the container's time base;
- brightness: mean luma, 0 to 255;
- sharpness: variance of a 4-neighbour Laplacian (low means blurred or flat);
- change from the frame `lag_s` earlier (0.2 s: at 30 fps, consecutive
  frames hide slow, smooth movement):
  - the global shift, by phase correlation (the whole image moved: a camera
    or a zoom);
  - the share of pixels that changed after removing that shift (local
    change);
- whether it repeats the previous frame (a stalled feed).

Each window (1 s by default) is classified, first match wins:
- insufficient_evidence: frames are missing;
- low_quality: too dark to measure change;
- insufficient_evidence: frames repeat with no change over the lag (a stalled
  feed and a perfectly still compressed scene can't be told apart);
- low_quality: too blurred or flat to measure change;
- camera_motion: the whole image shifted, so local change can't be
  attributed;
- movement: local change above the movement threshold;
- small_change: between the two thresholds, so uncertain;
- no_significant_movement: below the lower threshold.

What it never does: decide whether anyone is asleep, awake, breathing or
safe. "No significant movement" is a measurement of pixels, not a sleep
state. No breathing rate, no health inference. The thresholds are
configuration, recorded with every run, and were not validated against any
ground truth.

Decode and analysis are one streaming pass: frames are measured as they are
decoded and never stored.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import av
import numpy as np

PROCESSOR_VERSION = "motion-baseline-2"
ALLOWED_CONTAINERS = ("matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2")
ALLOWED_CODECS = ("vp8", "vp9", "h264")
KINDS = ("insufficient_evidence", "low_quality", "camera_motion", "movement", "small_change",
         "no_significant_movement")


@dataclass(frozen=True)
class Limits:
    """Resource limits, checked before and during decoding."""
    max_bytes: int = 64 * 1024 * 1024
    max_duration_s: float = 120.0
    max_frames: int = 5000
    max_pixels: int = 3840 * 2160
    max_decode_s: float = 60.0


@dataclass(frozen=True)
class Config:
    """Analysis settings. Part of a run's identity: a change is a new run."""
    analysis_width: int = 160            # frames are measured at this width
    window_s: float = 1.0
    lag_s: float = 0.2                   # compare each frame with the one this much earlier
    pixel_delta: float = 12.0            # grey levels; a pixel "changed" above this
    movement_fraction: float = 0.03      # >= 3% of pixels changed: movement
    small_fraction: float = 0.005        # >= 0.5%: small change (uncertain)
    dark_mean: float = 25.0              # mean luma below this: too dark
    blur_variance: float = 12.0          # Laplacian variance below this: too blurred
    min_coverage: float = 0.8            # share of expected frames present
    camera_shift_px: float = 1.5         # global shift at analysis width
    camera_peak: float = 0.15            # phase-correlation peak needed to trust a shift
    frozen_ratio: float = 0.8            # share of repeated consecutive frames (a lossy codec refines
                                         # a repeated picture for a few frames, so not 1.0)
    frozen_mad: float = 0.1              # mean absolute difference below this: a repeated frame (a
                                         # stalled feed, or a still scene a codec stripped of noise)
    sample_every_s: float = 0.5          # spacing of the stored frame samples

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


class MediaError(Exception):
    """A file that can't be processed. `code` is stable for clients and tests;
    `retryable` is False: the same bytes will fail the same way."""

    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sniff(head: bytes) -> Optional[str]:
    """The container family from the file's first bytes, never its name."""
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"
    if head[4:8] == b"ftyp":
        return "mp4"
    return None


def inspect(path: Path, limits: Limits = Limits()) -> dict[str, Any]:
    """Validate a file before decoding it: size, magic bytes, container,
    codec, dimensions and duration. Raises MediaError."""
    size = path.stat().st_size
    if size == 0:
        raise MediaError("EMPTY_FILE", "the file is empty")
    if size > limits.max_bytes:
        raise MediaError("TOO_LARGE", "the file is larger than the limit", size=size, limit=limits.max_bytes)
    with path.open("rb") as f:
        family = sniff(f.read(16))
    if family is None:
        raise MediaError("NOT_A_VIDEO", "the file's content isn't a supported video container")
    try:
        container = av.open(str(path))
    except (av.error.FFmpegError, OSError) as exc:
        raise MediaError("UNREADABLE", "the container couldn't be opened", reason=type(exc).__name__)
    with container:
        if container.format.name not in ALLOWED_CONTAINERS:
            raise MediaError("NOT_A_VIDEO", "unsupported container", container=container.format.name)
        if not container.streams.video:
            raise MediaError("NO_VIDEO_STREAM", "the file has no video stream")
        stream = container.streams.video[0]
        codec = stream.codec_context.name
        if codec not in ALLOWED_CODECS:
            raise MediaError("UNSUPPORTED_CODEC", "unsupported video codec", codec=codec, allowed=list(ALLOWED_CODECS))
        width, height = stream.codec_context.width, stream.codec_context.height
        if not width or not height or width * height > limits.max_pixels:
            raise MediaError("TOO_MANY_PIXELS", "the frame size is outside the limit", width=width, height=height)
        duration = (container.duration / 1_000_000) if container.duration else None
        if duration is not None and duration > limits.max_duration_s:
            raise MediaError("TOO_LONG", "the video is longer than the limit", duration_s=duration,
                             limit=limits.max_duration_s)
        rate = float(stream.average_rate) if stream.average_rate else None
        return {"container": container.format.name, "family": family, "codec": codec, "width": width,
                "height": height, "fps": rate, "duration_s": duration, "size_bytes": size,
                "has_audio": bool(container.streams.audio)}


def video_only_copy(source: Path, target: Path) -> None:
    """Copy the first video stream into a new file without re-encoding, and
    without any audio or other stream. Raises MediaError if it can't."""
    try:
        with av.open(str(source)) as src:
            vin = src.streams.video[0]
            if "matroska" in src.format.name:
                muxer = "webm" if vin.codec_context.name in ("vp8", "vp9") else "matroska"
            else:
                muxer = "mp4"
            with av.open(str(target), "w", format=muxer) as dst:
                vout = dst.add_stream_from_template(vin)
                for packet in src.demux(vin):
                    if packet.dts is None:
                        continue
                    packet.stream = vout
                    dst.mux(packet)
    except (av.error.FFmpegError, OSError, IndexError, ValueError) as exc:
        raise MediaError("UNREADABLE", "the video stream couldn't be copied", reason=type(exc).__name__)


def _laplacian_variance(g: np.ndarray) -> float:
    lap = -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())


def _blur(g: np.ndarray) -> np.ndarray:
    """3x3 box blur: removes pixel noise before counting changed pixels."""
    p = np.pad(g, 1, mode="edge")
    return sum(p[dy:dy + g.shape[0], dx:dx + g.shape[1]] for dy in range(3) for dx in range(3)) / 9.0


def _phase_shift(a: np.ndarray, b: np.ndarray, window: np.ndarray) -> tuple[int, int, float]:
    """Global translation from a to b (dy, dx) and the correlation peak (0 to 1)."""
    fa = np.fft.fft2((a - a.mean()) * window)
    fb = np.fft.fft2((b - b.mean()) * window)
    cross = fa * np.conj(fb)
    r = np.fft.ifft2(cross / (np.abs(cross) + 1e-9)).real
    dy, dx = np.unravel_index(int(np.argmax(r)), r.shape)
    peak = float(r[dy, dx])
    h, w = a.shape
    dy = dy - h if dy > h // 2 else dy
    dx = dx - w if dx > w // 2 else dx
    return int(dy), int(dx), peak


def _local_change(a: np.ndarray, b: np.ndarray, dy: int, dx: int, delta: float) -> float:
    """Share of pixels that changed after aligning b back onto a by (dy, dx).
    b is first brought to a's brightness and contrast, so a lighting change
    (a lamp, night vision switching) isn't counted as movement."""
    sa, sb = float(a.std()), float(b.std())
    b = (b - float(b.mean())) * (sa / sb if sb > 1e-6 else 1.0) + float(a.mean())
    aligned = np.roll(b, shift=(dy, dx), axis=(0, 1))
    m = max(abs(dy), abs(dx)) + 1
    diff = np.abs(_blur(a)[m:-m, m:-m] - _blur(aligned)[m:-m, m:-m])
    return float((diff > delta).mean()) if diff.size else 0.0


def _r(x: Optional[float], n: int = 4) -> Optional[float]:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), n)


def analyze(path: Path, config: Config = Config(), limits: Limits = Limits(),
            on_frame: Any = None) -> dict[str, Any]:
    """Decode every video frame and measure it. Deterministic for the same
    bytes, config and library versions. Raises MediaError. `on_frame(n)` is
    called as frames decode (tests use it to interrupt a run)."""
    probe = inspect(path, limits)
    started = time.monotonic()
    frames: list[dict[str, Any]] = []
    without_ts = 0
    prev: Optional[np.ndarray] = None
    recent: list[np.ndarray] = []
    recent_t: list[float] = []
    window: Optional[np.ndarray] = None
    try:
        container = av.open(str(path))
    except (av.error.FFmpegError, OSError) as exc:
        raise MediaError("UNREADABLE", "the container couldn't be opened", reason=type(exc).__name__)
    with container:
        stream = container.streams.video[0]
        # The header's frame size isn't binding: a stream can switch to huge
        # keyframes later. Cap the decoder and check every frame (review, 2026-10-11).
        stream.codec_context.options = {"max_pixels": str(limits.max_pixels)}
        width = config.analysis_width
        height = max(2, int(round(probe["height"] * width / probe["width"] / 2)) * 2)
        lag = max(1, int(round(config.lag_s * (probe["fps"] or 30.0))))
        try:
            for frame in container.decode(stream):
                if len(frames) + without_ts >= limits.max_frames:
                    raise MediaError("TOO_MANY_FRAMES", "more frames than the limit", limit=limits.max_frames)
                if time.monotonic() - started > limits.max_decode_s:
                    raise MediaError("DECODE_TIMEOUT", "decoding took longer than the limit",
                                     limit_s=limits.max_decode_s)
                if frame.width * frame.height > limits.max_pixels:
                    raise MediaError("TOO_MANY_PIXELS", "a decoded frame is larger than the limit",
                                     width=frame.width, height=frame.height)
                if frame.time is None:
                    without_ts += 1
                    continue
                if frame.time > limits.max_duration_s:
                    raise MediaError("TOO_LONG", "the video runs longer than the limit", limit=limits.max_duration_s)
                g = frame.reformat(width=width, height=height, format="gray").to_ndarray().astype(np.float32)
                if window is None:
                    window = np.outer(np.hanning(g.shape[0]), np.hanning(g.shape[1])).astype(np.float32)
                row = {"t": float(frame.time), "brightness": float(g.mean()), "sharpness": _laplacian_variance(g),
                       "local_change": None, "ref_t": None, "shift": None, "peak": None, "identical": False}
                if prev is not None:
                    row["identical"] = bool(float(np.abs(prev - g).mean()) < config.frozen_mad)
                if len(recent) >= lag:
                    ref = recent[-lag]
                    dy, dx, peak = _phase_shift(ref, g, window)
                    if peak < config.camera_peak:
                        dy, dx = 0, 0                     # no trustworthy global shift
                    row["shift"] = math.hypot(dy, dx)
                    row["peak"] = peak
                    row["local_change"] = _local_change(ref, g, dy, dx, config.pixel_delta)
                    row["ref_t"] = recent_t[-lag]
                frames.append(row)
                prev = g
                recent.append(g)
                recent_t.append(row["t"])
                if len(recent) > lag:
                    recent.pop(0)
                    recent_t.pop(0)
                if on_frame is not None:
                    on_frame(len(frames))
        except av.error.FFmpegError as exc:
            raise MediaError("CORRUPT_STREAM", "the video stream is damaged", frames_decoded=len(frames),
                             reason=type(exc).__name__)
    if not frames:
        raise MediaError("NO_FRAMES", "no frame could be decoded", frames_without_timestamp=without_ts)
    return _summarize(frames, probe, config, without_ts, round((time.monotonic() - started) * 1000))


def _summarize(frames: list[dict[str, Any]], probe: dict[str, Any], config: Config, without_ts: int,
               elapsed_ms: int) -> dict[str, Any]:
    frames.sort(key=lambda f: f["t"])
    times = [f["t"] for f in frames]
    gaps = [b - a for a, b in zip(times, times[1:])]
    nominal = 1.0 / probe["fps"] if probe.get("fps") else (float(np.median(gaps)) if gaps else config.window_s)
    missing_gaps = [{"after_s": _r(a), "gap_s": _r(b - a)} for a, b in zip(times, times[1:]) if b - a > 1.5 * nominal]
    start = 0.0
    # The container's duration when it has one: frames missing at the end are
    # missing evidence. Otherwise the last frame's own interval.
    end = probe.get("duration_s") or (times[-1] + nominal)
    n_windows = max(1, math.ceil((end - start) / config.window_s - 1e-9))
    # A sliver at the end (a duration a few milliseconds past a whole window)
    # joins the window before it: a 0-length interval can't be stored.
    if n_windows > 1 and end - (start + (n_windows - 1) * config.window_s) < 0.05 * config.window_s:
        n_windows -= 1
    expected = config.window_s / nominal
    windows = []
    for i in range(n_windows):
        lo = start + i * config.window_s
        hi = end if i == n_windows - 1 else min(end, start + (i + 1) * config.window_s)
        inside = [f for f in frames if lo <= f["t"] < hi]
        pairs = [f for f in inside if f["local_change"] is not None]
        # Pairs whose earlier frame is also in this window: the change that
        # happened here, not the previous window's carried over the lag.
        own = [f for f in pairs if f["ref_t"] >= lo]
        coverage = min(1.0, len(inside) / (expected * (hi - lo) / config.window_s)) if hi > lo else 0.0
        w = {"t_start": _r(lo), "t_end": _r(hi), "frames": len(inside), "coverage": _r(coverage),
             "brightness": _r(np.mean([f["brightness"] for f in inside])) if inside else None,
             "sharpness": _r(np.median([f["sharpness"] for f in inside])) if inside else None,
             "local_change": _r(np.mean([f["local_change"] for f in pairs])) if pairs else None,
             "local_change_max": _r(max(f["local_change"] for f in pairs)) if pairs else None,
             "local_change_within": _r(np.mean([f["local_change"] for f in own])) if own else None,
             "camera_shift_max": _r(max(f["shift"] for f in pairs)) if pairs else None,
             "camera_shift_median": _r(np.median([f["shift"] for f in pairs])) if pairs else None,
             "identical_ratio": _r(sum(f["identical"] for f in inside[1:]) / (len(inside) - 1))
             if len(inside) > 1 else None}
        w["kind"], w["reason"] = _classify(w, config)
        windows.append(w)
    samples, next_t = [], 0.0
    for f in frames:
        if f["t"] >= next_t:
            samples.append({"t": _r(f["t"]), "brightness": _r(f["brightness"], 2), "sharpness": _r(f["sharpness"], 2),
                            "local_change": _r(f["local_change"]), "camera_shift": _r(f["shift"], 2)})
            next_t = f["t"] + config.sample_every_s
    return {
        "processor_version": PROCESSOR_VERSION,
        "config": asdict(config), "config_digest": config.digest(),
        "input": probe,
        "frames": {"decoded": len(frames) + without_ts, "analyzed": len(frames), "without_timestamp": without_ts,
                   "first_t": _r(times[0]), "last_t": _r(times[-1]), "nominal_interval_s": _r(nominal),
                   "measured_duration_s": _r(times[-1] + nominal),
                   "timestamp_gaps": missing_gaps[:50], "timestamp_gap_count": len(missing_gaps)},
        "windows": windows,
        "intervals": _merge(windows),
        "frame_samples": samples,
        "elapsed_ms": elapsed_ms,
        "library": {"av": av.__version__, "numpy": np.__version__},
    }


def _classify(w: dict[str, Any], c: Config) -> tuple[str, str]:
    """First match wins: what stops a measurement comes before any measurement."""
    if w["frames"] == 0 or (w["coverage"] or 0) < c.min_coverage:
        return "insufficient_evidence", "frames_missing"
    if w["brightness"] is not None and w["brightness"] < c.dark_mean:
        return "low_quality", "too_dark"
    # Frames that repeat and show no change over the lag either: a stalled feed
    # and a perfectly still, heavily compressed scene look the same, so this is
    # missing evidence, never "still". Change over the lag wins (review: H.264
    # drops sensor noise, so consecutive frames repeat while things move), but
    # only change inside the window counts: a feed that stalls right after a pan
    # still compares its first frames with the pan (cold review, 2026-10-11).
    within = w.get("local_change_within", w["local_change"])
    if w["identical_ratio"] is not None and w["identical_ratio"] >= c.frozen_ratio \
            and (within is None or within < c.small_fraction):
        return "insufficient_evidence", "frames_repeated"
    if w["sharpness"] is not None and w["sharpness"] < c.blur_variance:
        return "low_quality", "too_blurred_or_flat"
    if w["local_change"] is None:
        return "insufficient_evidence", "too_few_frames"
    # Sustained whole-image motion, not one jolt: the median shift in the window.
    if (w["camera_shift_median"] or 0) >= c.camera_shift_px:
        return "camera_motion", "whole_image_shifted"
    if w["local_change"] >= c.movement_fraction:
        return "movement", "local_change_above_threshold"
    if w["local_change"] >= c.small_fraction:
        return "small_change", "local_change_between_thresholds"
    return "no_significant_movement", "local_change_below_threshold"


def _merge(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Consecutive windows of the same kind and reason become one interval."""
    out: list[dict[str, Any]] = []
    for w in windows:
        last = out[-1] if out else None
        if last and (last["kind"], last["reason"]) == (w["kind"], w["reason"]):
            last["t_end"] = w["t_end"]
            last["_w"].append(w)
        else:
            out.append({"kind": w["kind"], "reason": w["reason"], "t_start": w["t_start"], "t_end": w["t_end"],
                        "_w": [w]})
    for i in out:
        ws = i.pop("_w")
        changes = [w["local_change"] for w in ws if w["local_change"] is not None]
        i.update({"windows": len(ws), "frames": sum(w["frames"] for w in ws),
                  "coverage_min": _r(min(w["coverage"] for w in ws)),
                  "local_change_mean": _r(np.mean(changes)) if changes else None,
                  "local_change_max": _r(max((w["local_change_max"] for w in ws if w["local_change_max"] is not None),
                                             default=None)) if changes else None,
                  "brightness_mean": _r(np.mean([w["brightness"] for w in ws if w["brightness"] is not None]))
                  if any(w["brightness"] is not None for w in ws) else None})
    return out
