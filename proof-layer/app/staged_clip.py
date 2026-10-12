"""Generated video clips: the public staged sample and the test fixtures.

A staged clip contains no person and no infant: a textured background, a
soft moving shape, and deliberate trouble (darkness, a camera pan, a stalled
feed). Every frame is computed from a fixed seed, so the same call writes the
same pictures. The encoder is single-threaded to keep its output stable.

Segments are (seconds, kind) in order. Kinds:
- "still": the scene with sensor-like noise and nothing moving;
- "move": the shape travels across the scene;
- "small": the shape drifts a few pixels (a small, uncertain change);
- "corner": a second, smaller shape moves in a corner (background movement);
- "dim": the still scene at 60% brightness (a lighting change, nothing moving);
- "dark": the scene at a few percent of its brightness;
- "pan": the whole image shifts each frame (the camera moved);
- "frozen": one frame repeated (a stalled feed);
- "gap": no frames at all for the segment (timestamps jump).
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Iterable, Optional

import av
import numpy as np

# Whole seconds, so each one-second analysis window sees one condition. A
# window that straddles two is a separate, tested case.
STAGED_SEGMENTS = (("still", 2.0), ("move", 3.0), ("still", 2.0), ("small", 2.0), ("dark", 2.0),
                   ("pan", 2.0), ("frozen", 2.0), ("move", 2.0))
SMALL_PX = 16.0         # how far "small" sways, in pixels of a 320-wide frame (measured: 14 is the least that registers)
STAGED_CAPTION = "Staged test clip: generated shapes, no person or infant."


def _background(h: int, w: int, rng: np.random.Generator) -> np.ndarray:
    """A soft texture with enough detail to measure sharpness and shift."""
    base = rng.integers(70, 190, size=(h // 8 + 2, w // 8 + 2)).astype(np.float32)
    big = np.kron(base, np.ones((8, 8), dtype=np.float32))[:h, :w]
    yy, xx = np.mgrid[0:h, 0:w]
    stripes = 18 * np.sin(xx / 7.0) * np.cos(yy / 11.0)
    return np.clip(big * 0.6 + 60 + stripes, 0, 255)


def _blob(frame: np.ndarray, cy: float, cx: float, r: float) -> None:
    yy, xx = np.mgrid[0:frame.shape[0], 0:frame.shape[1]]
    mask = np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * r * r)))
    frame += (235 - frame) * mask


def frames(segments: Iterable[tuple[str, float]], size: tuple[int, int] = (240, 320), fps: int = 30,
           seed: int = 7, jitter_ms: float = 0.0) -> Iterable[tuple[Optional[np.ndarray], float]]:
    """(grey frame or None for a gap, timestamp in seconds) in order."""
    h, w = size
    rng = np.random.default_rng(seed)
    bg = _background(h, w, rng)
    t, n = 0.0, 0
    last: Optional[np.ndarray] = None
    for kind, seconds in segments:
        count = int(round(seconds * fps))
        for i in range(count):
            ts = t + i / fps + (rng.uniform(-jitter_ms, jitter_ms) / 1000.0 if jitter_ms else 0.0)
            if kind == "gap":
                yield None, ts
                continue
            if kind == "frozen" and last is not None:
                yield last, ts
                continue
            f = bg.copy()
            if kind == "pan":
                f = np.roll(bg, shift=(0, 3 * (i + 1)), axis=(0, 1))
            # Every kind starts and ends with the blob where "still" has it, so a
            # segment boundary adds no jump the script doesn't describe.
            if kind == "move":
                # Out and back, slowing to a stop at both ends.
                _blob(f, h * 0.5, w * (0.5 + 0.3 * np.sin(np.pi * i / count) ** 2), h * 0.12)
            elif kind == "corner":
                _blob(f, h * 0.15, w * (0.05 + 0.25 * i / max(1, count - 1)), h * 0.08)
            elif kind == "small":
                _blob(f, h * 0.5, w * 0.5 + SMALL_PX * np.sin(2 * np.pi * i / count), h * 0.12)
            else:
                _blob(f, h * 0.5, w * 0.5, h * 0.12)
            f = f + rng.normal(0, 1.5, size=f.shape)        # sensor-like noise: a live feed is never identical
            if kind == "dark":
                f = f * 0.05
            elif kind == "dim":
                f = f * 0.6
            last = np.clip(f, 0, 255).astype(np.uint8)
            yield last, ts
        t += count / fps
        n += count


def write(path: Path, segments: Iterable[tuple[str, float]] = STAGED_SEGMENTS, *, size: tuple[int, int] = (240, 320),
          fps: int = 30, codec: str = "libvpx", container: str = "webm", seed: int = 7,
          jitter_ms: float = 0.0, audio: bool = False, live: bool = False, crf: int | None = None) -> Path:
    """Encode a clip. Timestamps are written explicitly (millisecond time
    base), so gaps and jitter survive into the file. `audio` adds a quiet
    tone track; `live` writes no duration header (as a recorder streaming to
    disk does); `crf` sets an x264 quality."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = av.open(str(path), mode="w", format=container, options={"live": "1"} if live else {})
    stream = out.add_stream(codec, rate=fps)
    stream.width, stream.height = size[1], size[0]
    stream.pix_fmt = "yuv420p"
    stream.time_base = Fraction(1, 1000)
    stream.codec_context.thread_count = 1
    if codec == "libvpx":
        stream.options = {"deadline": "good", "cpu-used": "4", "b": "800k"}
    elif codec == "libx264" and crf is not None:
        stream.options = {"crf": str(crf), "preset": "veryfast"}
    sound = None
    if audio:
        sound = out.add_stream("libopus" if container == "webm" else "aac", rate=48000)
        sound.layout = "mono"
    last_pts = -1
    for grey, ts in frames(segments, size, fps, seed, jitter_ms):
        if grey is None:
            continue
        rgb = np.repeat(grey[:, :, None], 3, axis=2)
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24").reformat(format="yuv420p")
        pts = max(last_pts + 1, int(round(ts * 1000)))
        frame.pts, frame.time_base = pts, Fraction(1, 1000)
        last_pts = pts
        for packet in stream.encode(frame):
            out.mux(packet)
    for packet in stream.encode():
        out.mux(packet)
    if sound is not None:
        seconds = sum(s for _k, s in segments)
        tone = (0.1 * np.sin(2 * np.pi * 440 * np.arange(int(48000 * seconds)) / 48000)).astype(np.float32)
        for i in range(0, len(tone), 960):
            chunk = tone[i:i + 960]
            if len(chunk) < 960:
                break
            frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="flt", layout="mono")
            frame.sample_rate, frame.pts = 48000, i
            for packet in sound.encode(frame):
                out.mux(packet)
        for packet in sound.encode():
            out.mux(packet)
    out.close()
    return path
