"""
Tests for the minimap-HUD presence detector.

Two layers:

* Synthetic tests always run — they pin the correlation maths and the threshold logic.
* The recording test runs only when frames are available, because the source clips are the
  player's own screen recordings and are deliberately not committed. Regenerate with:

      ffmpeg -i with-techcheck.mp4  -vf fps=1 <dir>/w1_%03d.png
      ffmpeg -i with-techcheck2.mp4 -vf fps=1 <dir>/w2_%03d.png
      WDDROP_TEST_FRAMES=<dir> pytest tests/test_hud.py

  Measured on those clips: HUD-present frames score +0.866..+1.000 and absent frames
  -0.222..+0.179 — a gap of +0.687 and 0/56 misclassified.
"""
from __future__ import annotations

import glob
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))

import pytest  # noqa: E402

Image = pytest.importorskip("PIL.Image", reason="pillow not installed")
pytest.importorskip("numpy", reason="numpy not installed")

from wddrop_client.capture.hud import (  # noqa: E402
    DEFAULT_THRESHOLD, HudDetector, choose_threshold, crop_region,
)

# Ground truth established by inspecting the top-right region of every frame.
TRUTH_PRESENT = {("w1", i) for i in (9, 27, 28, 29, 30)} | {("w2", i) for i in range(21, 27)}
TEMPLATE_FRAME = ("w1", 28)


def _noise(size, seed):
    import numpy as np
    from PIL import Image as I

    rng = np.random.default_rng(seed)
    return I.fromarray(rng.integers(0, 255, (size[1], size[0]), dtype="uint8"), mode="L")


def test_identical_region_scores_one():
    frame = _noise((200, 400), seed=1)
    det = HudDetector.from_frame(frame)
    assert det.read(frame).score == pytest.approx(1.0, abs=1e-6)
    assert det.present(frame)


def test_unrelated_content_scores_near_zero_and_reads_absent():
    det = HudDetector.from_frame(_noise((200, 400), seed=1))
    reading = det.read(_noise((200, 400), seed=2))
    assert abs(reading.score) < 0.3
    assert reading.present is False


def test_brightness_and_contrast_shifts_do_not_flip_the_verdict():
    """The dungeon behind the panel varies from bright sky to near-black corridor, so only
    the panel's structure may decide — not overall luminance."""
    from PIL import ImageEnhance

    frame = _noise((200, 400), seed=3)
    det = HudDetector.from_frame(frame)
    brighter = ImageEnhance.Brightness(frame.convert("L")).enhance(1.8)
    lower_contrast = ImageEnhance.Contrast(frame.convert("L")).enhance(0.4)
    assert det.present(brighter.convert("RGB"))
    assert det.present(lower_contrast.convert("RGB"))


def test_crop_region_is_fractional():
    frame = _noise((400, 200), seed=4).convert("RGB")
    assert crop_region(frame, (0.0, 0.0, 0.5, 0.5)).size == (200, 100)
    assert crop_region(frame, (0.25, 0.5, 0.75, 1.0)).size == (200, 100)


def test_choose_threshold_sits_between_the_clusters():
    thr = choose_threshold([0.9, 0.95, 1.0], [0.1, -0.2, 0.18])
    assert 0.18 < thr < 0.9


def test_choose_threshold_needs_both_states():
    with pytest.raises(ValueError):
        choose_threshold([0.9], [])


def test_overlapping_clusters_are_flagged(caplog):
    """Overlap means the region is mis-calibrated. It must warn rather than silently ship a
    detector that cannot separate the two states."""
    with caplog.at_level("WARNING"):
        choose_threshold([0.30], [0.55])
    assert any("mis-calibrated" in r.message or "overlap" in r.message.lower()
               for r in caplog.records)


# -- against the real recordings ------------------------------------------------
FRAMES_DIR = os.environ.get("WDDROP_TEST_FRAMES")


@pytest.mark.skipif(not FRAMES_DIR, reason="set WDDROP_TEST_FRAMES to a dir of 1fps frames")
def test_detector_separates_real_recording_frames():
    frames = []
    for f in sorted(glob.glob(os.path.join(FRAMES_DIR, "*.png"))):
        m = re.search(r"(w\d)_(\d+)\.png$", f)
        if m:
            frames.append((m.group(1), int(m.group(2)), Image.open(f)))
    assert frames, f"no w<N>_<NNN>.png frames in {FRAMES_DIR}"

    template = next(im for t, i, im in frames if (t, i) == TEMPLATE_FRAME)
    det = HudDetector.from_frame(template)

    present, absent, wrong = [], [], []
    for tag, idx, im in frames:
        reading = det.read(im)
        truth = (tag, idx) in TRUTH_PRESENT
        (present if truth else absent).append(reading.score)
        if reading.present != truth:
            wrong.append((tag, idx, truth, round(reading.score, 3)))

    assert not wrong, f"misclassified: {wrong}"
    # The clusters must stay far apart, not merely land on the right side of the line.
    assert min(present) - max(absent) > 0.4
    assert min(present) > DEFAULT_THRESHOLD
