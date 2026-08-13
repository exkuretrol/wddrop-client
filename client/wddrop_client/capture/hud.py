"""
Minimap-HUD presence detector — the client's cheap state signal.

Verified on frame samples from the recordings:

    battle (t=1s, 5s) ............ minimap ABSENT
    chest interaction (t=9s) ..... minimap ABSENT
    trap panel (t=10s) ........... minimap ABSENT
    chest dialogue (t=21s) ....... minimap ABSENT
    walking (t=28.5s) ............ minimap PRESENT

So `hud_present` cleanly separates "walking/idle in the dungeon" from every interactive
state. Two things follow:

* It is the session-level signal (HUD gone for a long time = left the dungeon), and it
  brackets episodes (HUD returning ends one).
* Most of a session is walking, and during walking there is nothing to read. Gating the
  expensive OCR path on `not hud_present` is what keeps the client cheap.

MATCH THE CHROME, NOT THE MAP
-----------------------------
The map interior changes constantly as the player explores, so correlating against the map
itself would drift and produce false negatives on well-explored floors. The panel's button
bar and collapse chevron are fixed furniture — that is what gets matched.

The reference template is captured during calibration on the player's own machine, so it
picks up their resolution, UI scale and theme rather than assuming ours.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("wddrop.hud")

# Region of the frame holding the minimap panel's button bar, as fractions of width/height
# so the same numbers survive a resolution change. Measured from the 704x1242 recording:
# the bar sits at roughly x 510-685, y 190-250.
DEFAULT_CHROME_REGION = (0.724, 0.153, 0.973, 0.201)  # (left, top, right, bottom)

# Correlation above this counts as "HUD present". Calibration re-derives it per machine by
# sampling both states, so this is only the fallback.
DEFAULT_THRESHOLD = 0.60


@dataclass(frozen=True)
class HudReading:
    present: bool
    score: float


def _to_gray_array(image, size):
    """PIL image -> flat list of floats, resized so template and candidate always align."""
    import numpy as np

    return np.asarray(image.convert("L").resize(size), dtype=float).ravel()


def _zncc(a, b) -> float:
    """Zero-mean normalised cross-correlation, in [-1, 1].

    Zero-mean and unit-variance normalisation is what makes this robust to the dungeon
    behind the panel being bright or dark — only the panel's STRUCTURE should decide.
    """
    import numpy as np

    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom == 0.0:
        return 0.0
    return float((a * b).sum() / denom)


class HudDetector:
    """Correlates a fixed screen region against a reference of the HUD's panel chrome."""

    def __init__(
        self,
        template,
        region: tuple[float, float, float, float] = DEFAULT_CHROME_REGION,
        threshold: float = DEFAULT_THRESHOLD,
        sample_size: tuple[int, int] = (64, 24),
    ):
        self.region = region
        self.threshold = threshold
        self.sample_size = sample_size
        self._template = _to_gray_array(template, sample_size)

    @classmethod
    def from_frame(cls, frame, **kwargs) -> "HudDetector":
        """Build a detector from a frame KNOWN to show the HUD (the calibration shot)."""
        region = kwargs.pop("region", DEFAULT_CHROME_REGION)
        return cls(crop_region(frame, region), region=region, **kwargs)

    @classmethod
    def from_template_file(cls, path: Path, **kwargs) -> "HudDetector":
        from PIL import Image

        return cls(Image.open(path), **kwargs)

    @classmethod
    def from_profile(cls, profile) -> "HudDetector":
        """Build from a calibration profile, refusing loudly if the template is absent.

        Never returns None: a silently-disabled detector reports hud_present=0 forever, which
        looks identical to "the player never walked" and hides the real fault.
        """
        from ..calibration import decode_template

        template = decode_template(profile)
        if template is None:
            raise SystemExit(
                "[!] this profile has no HUD template.\n"
                "    Re-run `calibrate` including --walk-shot (a screenshot taken while\n"
                "    walking in a dungeon, with the minimap visible)."
            )
        if not profile.hud_region:
            raise SystemExit("[!] this profile has no HUD region; re-run `calibrate`.")
        return cls(template, region=tuple(profile.hud_region))

    def read(self, frame) -> HudReading:
        candidate = _to_gray_array(crop_region(frame, self.region), self.sample_size)
        score = _zncc(self._template, candidate)
        return HudReading(present=score >= self.threshold, score=score)

    def present(self, frame) -> bool:
        return self.read(frame).present


def crop_region(frame, region: tuple[float, float, float, float]):
    """Crop by fractional coordinates so callers never hardcode pixels."""
    w, h = frame.size
    left, top, right, bottom = region
    return frame.crop((int(left * w), int(top * h), int(right * w), int(bottom * h)))


def choose_threshold(present_scores, absent_scores) -> float:
    """Pick the midpoint of the gap between the two observed score clusters.

    Calibration collects a few frames of each state and calls this, so the threshold fits
    the player's own machine instead of inheriting ours. A NEGATIVE gap (clusters overlap)
    means the region is mis-calibrated — the caller should re-run calibration rather than
    ship a detector that cannot separate the states.
    """
    if not present_scores or not absent_scores:
        raise ValueError("need samples of BOTH states to choose a threshold")
    lo = min(present_scores)
    hi = max(absent_scores)
    if lo <= hi:
        log.warning(
            "wddrop: HUD score clusters overlap (present min %.3f <= absent max %.3f); "
            "region is probably mis-calibrated", lo, hi,
        )
    return (lo + hi) / 2.0
