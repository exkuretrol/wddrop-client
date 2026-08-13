"""
Per-machine calibration profile.

Everything resolution-dependent lives here, so the recogniser and the HUD detector carry no
assumptions about anyone's screen. All reference material so far is a 704x1242 MOBILE
recording while the client targets Windows/Steam landscape, so nothing about geometry may be
hardcoded — it is fitted from the player's own screenshots.

WHAT IS FITTED, AND FROM WHAT
-----------------------------
    message band + font size + offset   from a screenshot showing a drop line,
                                        plus the item name the player confirms
    HUD region + template               from a screenshot taken while walking

Two screenshots and one typed name is the whole setup. The pieces that cannot be guessed are
exactly the ones that are unforgiving: on a real frame, one pixel of font size moved the true
answer from rank 1 (margin 0.084) to rank 7 (margin 0.003), and a guessed alignment offset
scored 0.390 where the fitted one scored 0.862.

SELF-VALIDATING
---------------
Because the confirmed name is known, calibration can immediately verify itself: it re-runs
recognition over the full vocabulary and checks the answer comes back. A profile that cannot
recognise the very frame it was fitted on is rejected rather than saved, which turns a silent
"collects nothing forever" failure into an error at setup time.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

log = logging.getLogger("wddrop.calibration")

# Text is looked for in the lower part of the frame; the drop message is a bottom dialogue
# line in every observation so far.
SEARCH_TOP_FRACTION = 0.55
# A text row has at least this many lit pixels — enough to ignore stray UI sparkle.
MIN_ROW_INK = 8
# Rows closer together than this belong to the same block of text.
ROW_GAP_TOLERANCE = 6
# Plausible rendered heights for a dialogue line, as a fraction of frame height. Guards
# against locking onto a HUD strip or a full-screen panel.
MIN_BAND_FRACTION, MAX_BAND_FRACTION = 0.010, 0.060


@dataclass
class Profile:
    """Everything fitted to one machine + resolution."""

    frame_size: tuple[int, int]
    message_band: tuple[int, int]                 # (top, bottom) in pixels
    font_path: str
    font_size: int
    offset: tuple[int, int]
    calibration_score: float
    # Per-character advance correction, fitted because the game spaces glyphs slightly
    # differently from PIL. Negligible on short names, decisive on long ones.
    letter_spacing: float = 0.0
    # Computed from the vocabulary at the fitted font size — never a fixed constant, or the
    # longest names fall outside it and become unmatchable.
    window: tuple[int, int] = (380, 30)
    # Left edge of the drop message, in pixels. Fixed by the game's layout, so it is measured
    # once here rather than re-derived per frame from whatever is lit in the band.
    text_x0: int | None = None
    # The game language this was fitted against. EMPTY means unknown, which is what a profile
    # written before the field existed is — and unknown must not be a guess: `ProfileStore`
    # treats "" as "usable for any language" and a named locale as a claim, so a wrong guess
    # here silently hands one language's geometry to another. It used to default to "zh_tw",
    # which made every unlabelled profile claim to be Chinese.
    locale: str = ""
    hud_region: tuple[float, float, float, float] | None = None   # fractional
    hud_threshold: float | None = None
    # The template is EMBEDDED, not referenced. A path can be moved, cleaned up or lost while
    # the profile still names it, and the old code then fell back to "no HUD detector" in
    # silence — producing hud_present=0 forever with no error, which is exactly how a live
    # session recorded zero chests while pinning a CPU core. A self-contained profile cannot
    # fail that way.
    hud_template_b64: str | None = None
    hud_template_path: str | None = None   # legacy, still honoured when present
    # The MINING PANEL's geometry, which is not the message band's. Fitted at run time the
    # first time a vein is worked, then kept here so later sessions build one index instead
    # of searching for the right one. See runner._fit_panel.
    #
    # Stamped with the version of the data it was fitted against: the fit is a statement
    # about how THIS atlas renders THIS vocabulary, so new game data invalidates it. Getting
    # that wrong would not fail loudly — it would read plausible wrong item names.
    panel_font_size: int | None = None
    panel_letter_spacing: float | None = None
    panel_data_version: str | None = None
    notes: dict = field(default_factory=dict)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")

    def resolve_font(self, near: str | Path | None = None) -> str:
        """The font file, tolerating a moved folder.

        Falls back to the same filename beside the profile (and in ./fonts) before giving up,
        so copying the whole directory elsewhere does not require re-calibrating. The stored
        path may also carry the other platform's separator, which is normalised here.
        """
        from .config import bundled_dir, data_dir, program_dir

        candidates = [Path(self.font_path)]
        name = PurePosixPath(self.font_path.replace("\\", "/")).name
        # The program's own folders are searched too. The stored path is absolute and points
        # at wherever the client was when the profile was fitted — which is a folder the
        # player is free to move, rename or reinstall, while the profile now lives somewhere
        # else entirely. Without these, moving the client silently costs a calibration.
        roots = [Path(near) if near else None, Path.cwd(), data_dir(), program_dir(),
                 bundled_dir()]
        for root in filter(None, roots):
            candidates += [root / name, root / "fonts" / name]
        for c in candidates:
            if c.exists():
                return str(c)
        raise SystemExit(
            f"[!] font not found: {self.font_path}\n"
            f"    Looked for {name} beside the profile and in .\\fonts.\n"
            f"    Re-run `calibrate`, or restore the fonts folder."
        )

    @staticmethod
    def _coerce(raw: dict) -> dict:
        raw = dict(raw)
        for key in ("frame_size", "message_band", "offset", "window"):
            if raw.get(key) is not None:
                raw[key] = tuple(raw[key])
        if raw.get("hud_region"):
            raw["hud_region"] = tuple(raw["hud_region"])
        return raw

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        return cls(**cls._coerce(json.loads(Path(path).read_text(encoding="utf-8"))))


def data_version(*paths) -> str:
    """Identity of the game data a fit was made against.

    A DECLARED version wins when the files carry one — that is what the data repo should
    stamp, and a human-readable "1.34.5" beats a hash in a log or a bug report. Files built
    before that existed carry nothing, so their content is fingerprinted instead: the panel
    fit is a claim about how a specific atlas renders a specific vocabulary, and a silently
    stale one reads plausible WRONG item names rather than failing.

    Cheap enough to do at startup — a few megabytes of json and png, single-digit
    milliseconds — and cheaper by far than being wrong.
    """
    import hashlib

    declared = []
    digest = hashlib.sha256()
    for path in sorted(str(p) for p in paths if p):
        path = Path(path)
        if not path.is_file():
            continue
        if path.suffix == ".json":
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("version"):
                    declared.append(str(raw["version"]))
            except ValueError:
                pass
        digest.update(path.name.encode())
        digest.update(str(path.stat().st_size).encode())
        digest.update(path.read_bytes())
    if declared:
        return "+".join(sorted(set(declared)))
    return digest.hexdigest()[:16]


class ProfileStore:
    """Calibrations keyed by resolution.

    A profile is only valid for the resolution it was fitted at -- every region in it is
    absolute pixels -- so switching between windowed and fullscreen, or moving to another
    monitor, previously meant recalibrating and losing the old fit. Keying by "WxH" lets both
    live side by side and the right one be chosen from the frame size, which is known before
    a session starts.
    """

    FILENAME = "profiles.json"
    # Calibrations that ship WITH the client, for resolutions that have been verified against
    # real recordings. A player at one of those sizes never has to calibrate, and — the
    # reason this exists — never has to survive calibration getting it wrong: the fit that
    # shipped is the fit that was tested.
    SHIPPED = "profiles.shipped.json"

    def __init__(self, profiles: dict[str, "Profile"] | None = None):
        self._profiles = dict(profiles or {})

    @staticmethod
    def key_for(size) -> str:
        return f"{int(size[0])}x{int(size[1])}"

    def __len__(self) -> int:
        return len(self._profiles)

    def keys(self) -> list[str]:
        return sorted(self._profiles)

    def get(self, size) -> "Profile | None":
        return self._profiles.get(self.key_for(size))

    def put(self, profile: "Profile") -> None:
        self._profiles[self.key_for(profile.frame_size)] = profile

    def only(self) -> "Profile | None":
        """The single profile, when there is exactly one. Used when the size is not yet known."""
        return next(iter(self._profiles.values())) if len(self._profiles) == 1 else None

    @classmethod
    def shipped(cls, locale: str | None = None) -> "ProfileStore":
        """The verified calibrations that came with the client, for one game language.

        A calibration is fitted against a LANGUAGE as well as a resolution: it names the atlas
        it was rendered from, and it was scored on the words that language writes. Handing a
        Japanese client a Chinese fit is not a near miss — it points at an atlas that player
        has no reason to have built, and the font resolver stops the session dead.

        So an entry may be tagged with the locale it is for, as `704x1241@zh_tw`, and only the
        resolution part is the key. Untagged entries are kept for whichever locale they say
        they were fitted in. Asking for none of them in particular gets all of them, which is
        what the tests and the settings page want.
        """
        from .config import bundled_dir, program_dir

        path = next((root / cls.SHIPPED for root in (program_dir(), bundled_dir())
                     if root and (root / cls.SHIPPED).exists()), None)
        if path is None:
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        keep: dict[str, Profile] = {}
        for key, value in raw.items():
            size_key, _, tag = key.partition("@")
            profile = Profile(**Profile._coerce(value))
            if locale and (tag or profile.locale) not in ("", locale):
                continue
            # A tagged entry is the specific answer and beats an untagged one for the same
            # resolution, whichever order the file happens to list them in.
            if tag or size_key not in keep:
                keep[size_key] = profile
        return cls(keep)

    @classmethod
    def load(cls, directory: str | Path) -> "ProfileStore":
        directory = Path(directory)
        path = directory / cls.FILENAME
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls({k: Profile(**Profile._coerce(v)) for k, v in raw.items()})
        # Fall back to a single legacy profile.json so existing setups keep working.
        legacy = directory / "profile.json"
        if legacy.exists():
            profile = Profile.load(legacy)
            return cls({cls.key_for(profile.frame_size): profile})
        return cls()

    def save(self, directory: str | Path) -> None:
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / self.FILENAME).write_text(
            json.dumps({k: asdict(v) for k, v in self._profiles.items()},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )


def find_text_bands(frame_gray, top_fraction: float = SEARCH_TOP_FRACTION) -> list[tuple[int, int]]:
    """Find horizontal bands of text in the lower part of a frame.

    Row-ink projection rather than anything cleverer: the dialogue line is bright text on a
    dark, dimmed backdrop, which is the easiest possible case, and staying simple keeps the
    failure mode legible when it does not work.
    """
    import numpy as np

    from .capture.glyph import INK_LEVEL

    arr = np.asarray(frame_gray.convert("L"), dtype=float)
    h, _ = arr.shape
    start = int(h * top_fraction)
    rows = (arr[start:] > INK_LEVEL).sum(axis=1)

    bands, run_start, last = [], None, None
    for i, n in enumerate(rows):
        if n >= MIN_ROW_INK:
            if run_start is None:
                run_start = i
            last = i
        elif run_start is not None and last is not None and i - last > ROW_GAP_TOLERANCE:
            bands.append((start + run_start, start + last + 1))
            run_start, last = None, None
    if run_start is not None and last is not None:
        bands.append((start + run_start, start + last + 1))

    keep = [
        (a, b) for a, b in bands
        if MIN_BAND_FRACTION * h <= (b - a) <= MAX_BAND_FRACTION * h
    ]
    log.debug("wddrop: text bands %s (kept %s)", bands, keep)
    return keep


def propose_item_name(
    frame,
    prefix: str,
    font_paths: list[str],
    vocabulary: list[str],
    *,
    suffix: str = "",
    sizes: range = range(12, 60),
) -> tuple[str, float, float] | None:
    """Read the item out of the calibration shot, so the player can CONFIRM instead of type.

    A PROPOSAL, NEVER A DECISION, and the difference is the whole guarantee. `fit_message_
    profile` refuses to save a profile that cannot read back the name it was given — that
    check is what makes a calibration trustworthy. Feed it a name this function guessed from
    the same image and the check becomes circular: it would confirm the guess against
    itself. So this fills the box in and the player still says yes.

    Two stages, because the obvious approach does not fit in a lifetime. Fitting the
    geometry needs a known line, and sweeping every name would multiply an already
    seconds-long fit (fonts x 48 sizes x spacings x dx x dy) by several thousand. But the
    message is `prefix + name`, and the PREFIX is known from the game's own string table
    whatever the item is. So:

      1. fit font, size, offset and spacing against the prefix alone;
      2. read the item at that geometry with the ordinary recogniser — one index over the
         vocabulary, the same work a single frame does at run time.

    The stage-1 geometry is loose (measured on a real shot: size 25 where the true fit was
    26, spacing 1.1 where it was 0.2) which is why stage 2 searches a wider shift than
    recognition normally does. Loose was still enough — that shot read correctly at 0.856
    with a 0.216 margin — but "enough on the one shot we have" is exactly why the answer is
    offered rather than taken.

    Returns (name, score, margin), or None when nothing was read confidently.
    """
    import numpy as np

    from .capture.glyph import (
        PAD, RenderRecognizer, anchor_window, calibrate, calibrate_on_invariant,
        centred_shifts, ink_bbox, make_renderer, mask_after_name, required_window,
    )

    gray = frame.convert("L")
    bands = find_text_bands(gray)
    if not bands:
        return None
    # WHICHEVER END THE INVARIANT IS AT. A name-last locale puts it before the name
    # (「獲得了…」) where it can be rendered at the origin; a name-first one puts it after
    # (「…を手に入れた!!」) where its x depends on the name's width. Fitting the second case
    # as if it were the first means fitting against the empty string, which is what Japanese
    # was doing — and it made this return None for every Japanese shot ever taken, so the
    # box was never filled in and the player had to find their item among 3,500 names in a
    # script they may not be able to type.
    # Both are handed the window's own size to render into. The observation is cut to hold a
    # whole LINE now, not the fixed default, and `calibrate` correlates shape-for-shape — so
    # a mismatch does not score badly, it raises, and the `except ValueError` below would
    # swallow every candidate and report that nothing could be read.
    stage_one = (
        (lambda w: calibrate(w, "", prefix, font_paths, sizes, (w.shape[1], w.shape[0])))
        if prefix.strip()
        else (lambda w: calibrate_on_invariant(w, suffix, font_paths, sizes))
    )
    if not prefix.strip() and not suffix.strip():
        return None

    best = None
    for band in bands:
        # WIDE ENOUGH TO HOLD THE WHOLE LINE. The default window is 380px, which is ample for
        # text that starts where the line starts and useless for text that ENDS it: a real
        # Japanese drop line is ~480px of ink, so the invariant this has to fit against fell
        # outside the window entirely and nothing could be matched.
        lit = ink_bbox(np.asarray(gray, dtype=float)[band[0]:band[1], :])
        if lit is None:
            continue
        window = anchor_window(
            gray, band, (lit[2] - lit[0] + 2 * PAD, band[1] - band[0] + 2 * PAD))
        if window is None:
            continue
        try:
            # The one part of the line that is the same whatever dropped — see stage_one.
            font, size, offset, score, spacing = stage_one(window)
        except ValueError:
            continue
        if best is None or score > best[3]:
            best = (font, size, offset, score, spacing, band)
    if best is None:
        return None

    font, size, offset, _score, spacing, band = best
    box = ink_bbox(np.asarray(gray, dtype=float)[band[0]:band[1], :])
    text_x0 = int(box[0]) if box else None
    window = required_window(make_renderer(font, size, (1600, 80), spacing), prefix, vocabulary)
    renderer = make_renderer(font, size, window, spacing)
    recognizer = RenderRecognizer(
        renderer, prefix, vocabulary,
        # Wider than the 1 recognition uses: that assumes a FITTED offset, and this one came
        # from the invariant alone.
        shifts=centred_shifts(offset, 2),
    )
    observed = anchor_window(gray, band, window, x0_fixed=text_x0)
    # MASK THE TAIL, for the same reason the runner does. A candidate only ever covers the
    # NAME, so whatever the template puts after it is ink nothing can match — two characters
    # in Chinese and seven in Japanese, on names that are often seven. Recognising the whole
    # line instead is how the reader itself used to fail: the right name still ranked first,
    # at 0.543, under a 0.60 gate.
    if observed is not None and suffix.strip():
        observed, _cut = mask_after_name(observed, renderer, suffix)
    match = recognizer.recognize(observed)
    if not match.accepted or not match.name:
        return None
    log.info("wddrop: proposed %r (score %.3f, margin %.4f)",
             match.name, match.score, match.margin)
    return match.name, match.score, match.margin


def fit_message_profile(
    frame,
    confirmed_name: str,
    prefix: str,
    font_paths: list[str],
    vocabulary: list[str],
    *,
    # REQUIRED, not defaulted. A fit is a claim about one language's geometry — the game sets
    # its font size and line spacing per language — so the caller has to say which one it
    # measured. It used to default to "zh_tw", so forgetting to pass it mislabelled the
    # result rather than failing.
    locale: str,
    sizes: range = range(12, 60),
    suffix: str = "",
    separator: str = "×",
) -> Profile:
    """Fit band, font, size and offset from one screenshot plus the confirmed item name.

    Every candidate band is tried, because the frame may also contain other text (an AUTO
    button, a party strip); the band that actually fits the known line wins on score rather
    than on a positional guess.

    `suffix` is the template's own wording after the item name — 「！！」 for zh_tw,
    「を手に入れた!!」 for ja. It is not decoration: it decides how wide the window must be to
    hold a whole line, and it is masked out of the fit exactly as it is masked out of every
    later reading, so the size and spacing chosen here are the ones the runner will use.
    """
    import numpy as np

    from .capture.glyph import (
        MAX_QUANTITY, RenderRecognizer, _text_left, anchor_window, calibrate, centred_shifts,
        ink_bbox, make_renderer, mask_after_name, required_window,
    )

    def np_asarray(img):
        return np.asarray(img, dtype=float)

    gray = frame.convert("L")
    bands = find_text_bands(gray)
    if not bands:
        raise ValueError("no candidate text band found — is a drop message on screen?")

    best = None
    for band in bands:
        window = anchor_window(gray, band)
        if window is None:
            continue
        try:
            font, size, offset, score, spacing = calibrate(
                window, confirmed_name, prefix, font_paths, sizes
            )
        except ValueError:
            continue
        # Then again on the pixels the runner will actually match: the first pass has to
        # include the tail because nothing yet knows how big it is, and the size it picks is
        # the one that best fits a line half of which no candidate can cover. Measured on a
        # Japanese frame: pass one chose 24px/+1.6 (0.449), pass two 25px/+1.1 (0.793) — and
        # only the second reads the frame at all.
        named, cut = mask_after_name(
            window, make_renderer(font, size, window.shape[::-1], spacing), suffix, separator)
        if cut is not None:
            try:
                font, size, offset, score, spacing = calibrate(
                    named, confirmed_name, prefix, [font],
                    range(max(sizes.start, size - 3), min(sizes.stop, size + 4)),
                )
            except ValueError:
                pass
        if best is None or score > best[0]:
            best = (score, band, font, size, offset, spacing)

    if best is None:
        raise ValueError("could not fit any band to the confirmed name")
    score, band, font, size, offset, spacing = best
    strip = np_asarray(gray)[band[0]:band[1], :]
    box = ink_bbox(strip)
    # The same left edge `anchor_window` derives when it is not given one, or the profile
    # would pin every later reading to a different origin than the fit was made at.
    text_x0 = _text_left(strip, int(box[0])) if box else None
    # Sized for the WHOLE line, tail and all: the quantity reader anchors on the template's
    # own wording, and a window that stops short of it reports every long name as
    # `qty_unknown` without ever raising. `ja` needs ~120px more than `zh_tw` for this.
    window = required_window(
        make_renderer(font, size, (1600, 80), spacing), prefix, vocabulary,
        tail_sample=f"{separator}{MAX_QUANTITY}{suffix}" if suffix else None)
    log.info("wddrop: window sized to %s for %d candidates", window, len(vocabulary))

    profile = Profile(
        frame_size=frame.size,
        message_band=band,
        # Absolute, so the profile keeps working from any working directory. Stored relative
        # it silently depended on being run from the folder it was created in.
        font_path=str(Path(font).resolve()),
        font_size=size,
        offset=offset,
        calibration_score=score,
        letter_spacing=spacing,
        window=window,
        text_x0=text_x0,
        locale=locale,
    )

    # Self-check: the profile must recognise the frame it was fitted on, against the FULL
    # vocabulary rather than just the one name it was given.
    renderer = make_renderer(font, size, window, spacing)
    recognizer = RenderRecognizer(
        renderer, prefix, vocabulary, shifts=centred_shifts(offset, 1),
    )
    observed = anchor_window(gray, band, window, x0_fixed=text_x0)
    named, cut = mask_after_name(observed, renderer, suffix, separator)
    match = recognizer.recognize(named)
    profile.notes = {
        "self_check_name": match.name,
        "self_check_score": round(match.score, 4),
        "self_check_margin": round(match.margin, 4),
        # Where the item's name ended. None means the tail could not be found, which is worth
        # seeing in a profile: the fit still passed, but it passed the harder way.
        "name_ends_at": cut,
        "candidate_bands": len(bands),
        "window": list(window),
        "letter_spacing": spacing,
        "text_x0": text_x0,
    }
    if match.name != confirmed_name:
        raise ValueError(
            f"calibration failed its own check: fitted band {band} font {size}px scored "
            f"{score:.3f}, but recognition returned {match.name!r} instead of "
            f"{confirmed_name!r} (margin {match.margin:.4f}). Refusing to save a profile "
            f"that cannot read the frame it was built from."
        )
    log.info("wddrop: calibrated band=%s size=%dpx offset=%s spacing=%+.1f score=%.3f",
             band, size, offset, spacing, score)
    return profile


# Where to look for the minimap panel, and how big it plausibly is. Searched rather than
# assumed: the mobile reference put it at x 0.72-0.97 of a portrait frame, while the Steam
# client at 1920x1080 puts it at x 0.91-0.99 — a fixed fractional region fitted to one
# captured almost none of the other.
HUD_SEARCH_X, HUD_SEARCH_Y = 0.55, 0.35
HUD_WINDOW_SHAPES = ((0.08, 0.10), (0.09, 0.14), (0.10, 0.18), (0.12, 0.22))
# Fraction of the panel, measured from its bottom, holding the button bar. Only the chrome
# may be matched: the map interior redraws constantly as the floor is explored.
HUD_CHROME_FRACTION = 0.35
# A panel is a RECTANGLE: a column of its border carries a vertical edge down most of its
# height, a row across most of its width. Dungeon architecture has plenty of edge ENERGY and
# almost no full-length straight lines, which is the difference edge density alone cannot
# see — and did not: at 1920x1080 the search settled on a rock face at x 0.69-0.77 while the
# minimap sat at x 0.91-0.99, so the stored "HUD template" was a photograph of a wall. It
# matched 13 frames out of 2341, episodes never closed, and four chests were recorded as one.
# Measured: 1080 minimap chrome 0.292, the rock the search preferred 0.157, 704 chrome
# 0.666. It separates them, but 0.29 against 0.16 is not enough of a gap to REFUSE a
# calibration on — a legitimate panel at some other resolution could sit in between. So this
# warns, and the two verified resolutions ship a fit instead of relying on it.
HUD_STRAIGHTNESS_MIN = 0.22
HUD_EDGE_LEVEL = 40


def hud_straightness(frame, region: tuple[float, float, float, float]) -> float:
    """How much this region looks like the EDGE of a UI panel rather than scenery.

    The best single column of vertical edge plus the best single row of horizontal edge, each
    as a fraction of the region's height/width. Measured over real frames:

        1920x1080 minimap chrome   0.79        rock the search preferred   0.18
         704x1241 minimap chrome   0.63
    """
    import numpy as np

    from .capture.hud import crop_region

    a = np.asarray(crop_region(frame, region).convert("L"), dtype=float)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return 0.0
    vertical = (np.abs(np.diff(a, axis=1)) > HUD_EDGE_LEVEL).mean(axis=0).max()
    horizontal = (np.abs(np.diff(a, axis=0)) > HUD_EDGE_LEVEL).mean(axis=1).max()
    return float(vertical + horizontal) / 2.0


def detect_hud_region(frame) -> tuple[float, float, float, float]:
    """Locate the minimap panel's button bar, as fractional coordinates.

    The panel is found by edge density: its grid is far denser than dungeon walls. A SLIDING
    WINDOW is used rather than a global row/column threshold, because the latter also catches
    the architecture — on a real Steam frame it returned x 0.60-0.99, y 0.02-0.44, i.e. most
    of the quadrant, instead of the panel.
    """
    import numpy as np

    gray = np.asarray(frame.convert("L"), dtype=float)
    h, w = gray.shape
    gx = np.abs(np.diff(gray, axis=1))
    gy = np.abs(np.diff(gray, axis=0))
    edges = np.zeros_like(gray)
    edges[:, :-1] += gx
    edges[:-1, :] += gy
    integral = edges.cumsum(0).cumsum(1)

    def density(y0, x0, y1, x1):
        total = integral[y1 - 1, x1 - 1]
        if y0 > 0:
            total -= integral[y0 - 1, x1 - 1]
        if x0 > 0:
            total -= integral[y1 - 1, x0 - 1]
        if y0 > 0 and x0 > 0:
            total += integral[y0 - 1, x0 - 1]
        return total / ((y1 - y0) * (x1 - x0))

    best = None
    step = max(4, w // 160)
    for fw, fh in HUD_WINDOW_SHAPES:
        bw, bh = int(w * fw), int(h * fh)
        if bw < 8 or bh < 8:
            continue
        for y0 in range(0, max(1, int(h * HUD_SEARCH_Y) - bh), step):
            for x0 in range(int(w * HUD_SEARCH_X), max(int(w * HUD_SEARCH_X) + 1, w - bw), step):
                d = density(y0, x0, y0 + bh, x0 + bw)
                if best is None or d > best[0]:
                    best = (d, x0, y0, bw, bh)
    if best is None:
        raise ValueError("could not locate the minimap panel")
    _, x0, y0, bw, bh = best
    chrome_top = y0 + int(bh * (1.0 - HUD_CHROME_FRACTION))
    region = (x0 / w, chrome_top / h, (x0 + bw) / w, (y0 + bh) / h)
    log.info("wddrop: HUD panel at x %d-%d y %d-%d -> chrome region %s",
             x0, x0 + bw, y0, y0 + bh, tuple(round(v, 4) for v in region))
    return region


def encode_template(image) -> str:
    """PNG-encode a crop into the profile itself."""
    import base64
    import io

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def decode_template(profile: "Profile"):
    """Load the HUD template from the profile, or from the legacy sidecar file."""
    import base64
    import io

    from PIL import Image

    if profile.hud_template_b64:
        return Image.open(io.BytesIO(base64.b64decode(profile.hud_template_b64)))
    if profile.hud_template_path and Path(profile.hud_template_path).exists():
        return Image.open(profile.hud_template_path)
    return None


def fit_hud(profile: Profile, walking_frame, region: tuple[float, float, float, float] | None = None,
            template_path: str | Path | None = None) -> Profile:
    """Capture the HUD reference from a frame taken while walking in a dungeon.

    The template is cut from the player's own screen so it carries their resolution, UI scale
    and theme. It must be the panel CHROME (button bar / chevron), not the map interior — the
    map changes constantly as the floor is explored, so matching it would drift.
    """
    from .capture.hud import DEFAULT_CHROME_REGION, crop_region

    if region is None:
        try:
            region = detect_hud_region(walking_frame)
        except Exception as exc:
            log.warning("wddrop: HUD auto-detect failed (%s); falling back to default", exc)
            region = DEFAULT_CHROME_REGION
    crop = crop_region(walking_frame.convert("L"), region)
    profile.hud_template_b64 = encode_template(crop)
    profile.hud_region = region
    if template_path:
        # Still written out, purely so the fit can be eyeballed; nothing reads it.
        Path(template_path).parent.mkdir(parents=True, exist_ok=True)
        crop.save(template_path)
        profile.hud_template_path = str(template_path)
    log.info("wddrop: HUD template %dx%d embedded in the profile", crop.size[0], crop.size[1])
    return profile
