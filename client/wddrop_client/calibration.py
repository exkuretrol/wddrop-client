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
from dataclasses import asdict, dataclass, field, replace
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


# How far outside the dialogue box to still read, in pixels. Enough for the box's own border
# and a glyph that overhangs its cell; not enough to reach the scenery.
READ_MARGIN = 24

# THE GAME'S OWN LAYOUT, READ OUT OF THE GAME (Steam build, WizardryVariantsDaphne_Data,
# level1..level11, the CanvasScaler on the UI Canvas):
#
#     m_UiScaleMode          1   ScaleWithScreenSize
#     m_ReferenceResolution  1080 x 1920
#     m_ScreenMatchMode      1   Expand
#
# Expand means scale = MIN(width/1080, height/1920), so every UI element is a fixed size in
# CANVAS UNITS and its pixel size follows that one number. Nothing here needs to be fitted
# per resolution, and nothing has to be guessed for a resolution nobody has recorded: the
# canvas is 1920 units tall on any screen wider than 9:16, and 1080 units wide on any screen
# narrower than that.
#
# Confirmed against both recorded resolutions before being relied on — see PANEL_BOX_UNITS.
UI_REFERENCE = (1080.0, 1920.0)


def ui_scale(frame_size) -> float:
    """Canvas units -> pixels, for this screen. See UI_REFERENCE."""
    width, height = float(frame_size[0]), float(frame_size[1])
    return min(width / UI_REFERENCE[0], height / UI_REFERENCE[1])


# The mining result panel's box, in canvas units. Measured three independent ways and they
# agree to a unit, which is what makes the scaling law above trustworthy rather than fitted:
#
#     1920x1080  panel edges x637-1281      644px / 0.5625 = 1145 units
#      704x1241  the panel is CLIPPED by the screen — 1144 units is 740px on a 704px screen,
#                and it is drawn full-bleed, which is exactly what the recording shows
#     the ▼      sits 58px inside the right edge at 1080 and 67px at 704: 103 and 104 units
#
# That last one is the strong evidence. At 704x1241 the panel's right edge is off-screen, so
# the marker's position can only be predicted from the box the game THINKS it is drawing —
# and it is where this model says it is.
PANEL_BOX_UNITS = 1144.0

# A CAPTURED FRAME THAT IS A SCALED COPY OF A CALIBRATED ONE
# ----------------------------------------------------------
# The game's fullscreen is always BORDERLESS, and nothing outside it can change that: the
# mode it asks Windows for is fixed, so `-window-mode exclusive` and the stored preference
# are both overwritten the moment it applies its own display settings. Borderless means the
# WINDOW is the size of the desktop while the render stays at the resolution the player
# chose, so the compositor scales it: a 1920x1080 game on a 2560x1440 screen is captured at
# 2560x1440, and on a 4K screen at 3840x2160.
#
# Every region in a profile is absolute pixels, so such a frame matches nothing — but it is
# not a DIFFERENT picture, it is the same picture enlarged. Resampled back down it reads
# almost as well as the native one. Measured over 15 confirmed chest lines at 1920x1080,
# round-tripped through each display size and read with the shipped fit:
#
#     native 1920x1080          mean 0.9055   min 0.8632
#     via 2560x1440 (4/3)       mean 0.8905   min 0.8473    0 names changed
#     via 3840x2160 (2x)        mean 0.8885   min 0.8450    0 names changed
#
# ~0.016 against a 0.60 gate. So the scale is worth undoing rather than refusing, and worth
# undoing rather than calibrating for: the enlarged ink is blurred while the templates stay
# crisp, and no fit can recover what the upscale smeared.
#
# Only DOWN. A capture smaller than a calibration is not the same picture at all — it holds
# less than the fit needs, and enlarging it would invent detail and read confidently from it.
MAX_CAPTURE_SCALE = 4.0
# How far the two axes may disagree before this stops being a uniform scale. A pixel of
# rounding at 4K is 0.0005 of the width; anything past this is a different aspect ratio,
# which means letterboxing or stretching and is refused rather than guessed at.
SCALE_TOLERANCE = 0.005


def scaled_from(captured, calibrated) -> tuple[tuple[int, int], float] | None:
    """Which calibrated size `captured` is an enlargement of, and by how much.

    Returns (size, scale) or None. `calibrated` is any iterable of (w, h).

    Ties are broken toward the LARGEST calibrated size that fits, i.e. the smallest scale:
    it is the one that threw away least, and at 4K both 1920x1080 (2x) and 704x1241 would
    otherwise be candidates on aspect alone.
    """
    if not captured:
        return None
    width, height = int(captured[0]), int(captured[1])
    best = None
    for size in calibrated:
        if not size:
            continue
        cw, ch = int(size[0]), int(size[1])
        if cw <= 0 or ch <= 0 or (cw, ch) == (width, height):
            continue
        sx, sy = width / cw, height / ch
        if sx < 1.0 or sy < 1.0 or sx > MAX_CAPTURE_SCALE or sy > MAX_CAPTURE_SCALE:
            continue
        if abs(sx - sy) > SCALE_TOLERANCE * max(sx, sy):
            continue                      # a different aspect: letterboxed or stretched
        scale = (sx + sy) / 2.0
        if best is None or scale < best[1]:
            best = ((cw, ch), scale)
    return best


def read_columns(profile) -> tuple[int, int] | None:
    """The columns the message band is written in — everything outside is scenery.

    THE BOX IS CENTRED AND THE TEXT IS LEFT-ALIGNED IN IT, so one measured number describes
    both edges: the box reaches as far right of the centre as `text_x0` sits left of it.
    Verified on both fitted resolutions — 704x1241 has text_x0 93 and wraps at 611, and
    1920x1080 has 732 and wraps at 1188, each the mirror of the other to a pixel. In canvas
    units both are the same box: 801 and 811 units of text.

    Measured rather than derived because it CAN be measured here — calibration reads a real
    drop line on this machine, and a number taken from the player's own screen beats one
    taken from a prefab. The mining panel has no such measurement, which is what
    `panel_columns` is for.

    It matters twice, and the second time is not about speed:

      * capture copies these columns instead of the full width, which at 1920x1080 is a
        quarter of the pixels it was moving per frame;
      * the band's key covered the whole row, so on a whole frame the scenery either side
        changed it every frame and the line never counted as held still.

    None when the profile predates `text_x0` or the number is nonsense (at or past the
    centre), in which case every caller falls back to the full width it used before.
    """
    x0 = getattr(profile, "text_x0", None)
    size = getattr(profile, "frame_size", None)
    if not x0 or not size:
        return None
    width = int(size[0])
    if x0 * 2 >= width:
        return None
    return max(0, int(x0) - READ_MARGIN), min(width, width - int(x0) + READ_MARGIN)


def panel_columns(profile) -> tuple[int, int] | None:
    """The columns the MINING PANEL can draw in. A different box from the message band's.

    Wider, and that is the point: at 1920x1080 the message band's text is 504 columns and the
    panel is 644, so reading the panel between the band's columns cuts 70 pixels off each
    end — which is where the ▼ advance marker lives (x1217-1229 of a box ending at 1281).
    The marker is the game SAYING the panel is finished, and without it a swing dismissed
    inside two frames is never read at all.

    Centred, and clipped to the screen: at 704x1241 the box is 740px wide on a 704px screen
    and the game simply lets it bleed off both sides.
    """
    size = getattr(profile, "frame_size", None)
    if not size:
        return None
    width = int(size[0])
    box = PANEL_BOX_UNITS * ui_scale(size)
    left = max(0, int(round((width - box) / 2)))
    return left, min(width, width - left)


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
    # The panel's own TYPEFACE, which is not always the band's: the game ships a scenario
    # twin of its UI font and the two readers do not have to be drawn in the same one. At
    # 1920x1080 the band fits the scenario face and the panel is drawn in the plain one, and
    # reading the panel in the band's face scores 0.726 where the right face scores 0.905 —
    # so nothing in a mining panel was read at all. See runner._panel_faces.
    panel_font_path: str | None = None
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

    def resolve_panel_font(self, near: str | Path | None = None) -> str | None:
        """The MINING PANEL's typeface, tolerating a moved folder — or None if it is not here.

        The same search as `resolve_font`, for the same reason, and it matters more here than
        it looks: the runner takes the stored panel face only when the file EXISTS, so an
        absolute path from another machine silently falls back to the band's face — which at
        1920x1080 reads the panel at 0.726 where its own face reads 0.905, i.e. nothing in a
        mining panel is read at all. A SHIPPED profile carries a bare filename precisely so
        that it resolves on a machine that has never seen the folder it was fitted in.

        None rather than an exit: a profile fitted before the face was is a profile with no
        answer here, and the fit simply runs again.
        """
        stored = getattr(self, "panel_font_path", None)
        if not stored:
            return None
        from .config import bundled_dir, data_dir, program_dir

        name = PurePosixPath(str(stored).replace("\\", "/")).name
        roots = [Path(near) if near else None, Path.cwd(), data_dir(), program_dir(),
                 bundled_dir()]
        candidates = [Path(stored)] + [root / name for root in filter(None, roots)]
        for c in candidates:
            if c.exists():
                return str(c)
        return None

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

    def sizes(self) -> list[tuple[int, int]]:
        """Every frame size this store has a fit for, as (w, h) — for asking whether a
        captured frame is an enlargement of one of them. See `scaled_from`."""
        return [tuple(p.frame_size) for p in self._profiles.values() if p.frame_size]

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


# How far from the fitted geometry to look for one that SEPARATES better, and how close two
# scores have to be before separation is allowed to decide between them.
SHARPEN_SIZES = 1
# THE WHOLE PLAUSIBLE RANGE, not a neighbourhood of the spacing already chosen. The right
# spacing at a DIFFERENT size is nowhere near the one at this size — they trade off against
# each other, which is the whole reason the first pass cannot separate 18px from 19px — and
# on the shot this was written for the two optima are +0.9 and -0.2, over a whole point
# apart. Searching +-0.4 around the incumbent could never have found it.
SHARPEN_SPACINGS = [round(step / 10.0, 1) for step in range(-6, 21)]


def _sharpened(gray, band, text_x0, font, offset, prefix, name, rivals, window,
               size: int, spacing: float, suffix: str, separator: str):
    from .capture.glyph import anchor_window, make_renderer, mask_after_name, zncc
    """The (size, spacing) near this one that tells `name` from its closest rivals best.

    THE FIT CHOOSES BY SCORE, AND SCORE IS NOT WHAT RECOGNITION NEEDS. Two geometries can sit
    within a few thousandths of each other on the confirmed name and be worlds apart on
    whether that name can be told from the four others in its family — and the second is the
    property every later reading depends on.

    Measured on a real 1600x900 calibration shot, same face, same window:

        18px +0.9   score 0.8226   margin over the family +0.0222   <- chosen by score
        19px -0.2   score 0.8180   margin over the family +0.0302   <- chosen here

    A rounding difference in score, and the geometry the score preferred could not read
    「10,000バイン紙幣」 at all: 0.5982 against a 0.60 gate, where the other reads it at
    0.7979. That was a whole chest, missing from a session, with nothing in the log to say
    why — the line simply scored under the bar.

    Names written in ASCII digits are where this shows, because their advances differ most
    from the atlas's; a CJK-only calibration shot cannot see it, and most are CJK-only.
    """
    best = None
    for candidate_size in range(size - SHARPEN_SIZES, size + SHARPEN_SIZES + 1):
        if candidate_size < 8:
            continue
        for candidate_spacing in SHARPEN_SPACINGS:
            renderer = make_renderer(font, candidate_size, window, candidate_spacing)
            observed = anchor_window(gray, band, window, x0_fixed=text_x0)
            if observed is None:
                continue
            named, _cut = mask_after_name(observed, renderer, suffix, separator)
            import numpy as np

            def scored(text):
                template = renderer.render(prefix + text)
                return max(zncc(np.roll(np.roll(named, -dy, 0), -dx, 1), template)
                           for dy in range(-2, 3) for dx in range(-2, 3))
            mine = scored(name)
            rival = max((scored(other) for other in rivals if other != name), default=0.0)
            # RANKED BY THE MASKED SCORE, with the margin as the tie-break. The first pass
            # ranks by a score taken over the whole line in a fixed 380px window; this one
            # scores the NAME's own pixels in the window the runner will use, which is the
            # comparison every later reading makes. On the shot this was written for the two
            # disagree: the first pass has 18px +0.9 at 0.8226 and 19px -0.2 at 0.8180 — a
            # rounding apart — while masked, at each size's own best spacing, 19px reads the
            # calibration name at 0.8133 against 18px's 0.7872 AND the digit name at 0.8536
            # against 0.6646. Size is what the first pass cannot see, because a smaller size
            # with more spacing fits a CJK-only name just as well.
            if best is None or (mine, mine - rival) > (best[1], best[0]):
                best = (mine - rival, mine, candidate_size, candidate_spacing)
    return best


def _with_the_tie_broken(match, named, renderer, prefix, confirmed_name, offset):
    """`match`, accepted on the columns the top two differ in when the whole line cannot.

    THE CHECK MUST NOT BE STRICTER THAN THE READER IT CHECKS. `recognize` alone refuses a
    thin margin and the runner does not: it hands the top two to `break_tie`. Junk families
    are exactly where that matters — 「北穿の幽霊城の妖なる四鱗のガラクタ」 against 「…冥刻…」 —
    and a player whose calibration chest happened to hold one could not calibrate at all, at
    any resolution, while the client would have read that same line correctly every time.

    Returns (match, what the tie-break measured) — the second is None when it was not needed.
    """
    if match.name == confirmed_name or not (match.best and match.runner_up):
        return match, None
    from .capture.glyph import break_tie, centred_shifts

    winner, tie_margin, fit = break_tie(named, renderer, prefix, match.best, match.runner_up,
                                        shifts=centred_shifts(offset, 1))
    if (winner == confirmed_name and tie_margin >= SELF_CHECK_TIE_MARGIN
            and fit >= SELF_CHECK_TIE_MIN_SCORE):
        return replace(match, name=winner, accepted=True), (round(tie_margin, 4),
                                                            round(fit, 4))
    return match, None


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
        MAX_QUANTITY, MIN_MARGIN, RenderRecognizer, _text_left, anchor_window, calibrate,
        centred_shifts, ink_bbox, make_renderer, mask_after_name, required_window,
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
    # THE CHECK MUST NOT BE STRICTER THAN THE READER IT CHECKS. `recognize` alone refuses a
    # thin margin, and the runner does not: it hands the top two to `break_tie`, which
    # compares only the columns where they differ. Junk families are exactly where that
    # matters — 「北穿の幽霊城の妖なる四鱗のガラクタ」 against 「…冥刻…」 — and a player whose
    # calibration chest happened to hold one could not calibrate at all, at any resolution,
    # while the client would have read that same line correctly every time.
    #
    # Measured on the 1600x900 shot this was written for: the fit scored 0.823 and the
    # self-check returned None at margin 0.0183; the tie-break separates the same two
    # candidates by 0.28 at fit 0.90.
    match, tie_used = _with_the_tie_broken(match, named, renderer, prefix, confirmed_name,
                                          offset)
    # A GEOMETRY THAT READS THIS NAME IS NOT YET A GEOMETRY THAT TELLS IT FROM ITS FAMILY.
    # When the self-check needed the tie-break to get there, the fit is sitting on a score
    # that another geometry matches within a rounding error while separating far better —
    # and separation is what every later reading depends on. See _sharpened for the shot
    # that cost a chest.
    if match.name == confirmed_name and match.margin < MIN_MARGIN and match.shortlist:
        sharper = _sharpened(gray, band, text_x0, font, offset, prefix, confirmed_name,
                             list(match.shortlist), window, size, spacing, suffix, separator)
        if sharper and (sharper[2], sharper[3]) != (size, spacing):
            new_size, new_spacing = sharper[2], sharper[3]
            log.info("wddrop: %dpx %+.1f separates it better than %dpx %+.1f "
                     "(margin %+.4f against %+.4f); re-checking there",
                     new_size, new_spacing, size, spacing, sharper[0], match.margin)
            # THE WINDOW IS SIZED FOR THE NEW GEOMETRY, not carried over. It holds the
            # longest candidate plus its tail at a given size, and a larger size needs more
            # of it — reusing the old one would truncate exactly the longest names.
            new_window = required_window(
                make_renderer(font, new_size, (1600, 80), new_spacing), prefix, vocabulary,
                tail_sample=f"{separator}{MAX_QUANTITY}{suffix}" if suffix else None)
            candidate_renderer = make_renderer(font, new_size, new_window, new_spacing)
            candidate_index = RenderRecognizer(candidate_renderer, prefix, vocabulary,
                                               shifts=centred_shifts(offset, 1))
            candidate_obs = anchor_window(gray, band, new_window, x0_fixed=text_x0)
            candidate_named, candidate_cut = mask_after_name(
                candidate_obs, candidate_renderer, suffix, separator)
            candidate = candidate_index.recognize(candidate_named)
            candidate, candidate_tie = _with_the_tie_broken(
                candidate, candidate_named, candidate_renderer, prefix, confirmed_name,
                offset)
            # ADOPTED ONLY IF IT STILL READS THE SHOT, with more room than before. The fit
            # this replaces passed its own check; a replacement that does not is not better.
            if candidate.best == confirmed_name and candidate.margin > match.margin:
                size, spacing, window = new_size, new_spacing, new_window
                renderer, match, cut = candidate_renderer, candidate, candidate_cut
                tie_used = candidate_tie
                profile.font_size, profile.letter_spacing = size, spacing
                profile.window = window

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
        # Present only when the whole line could not separate the top two and the columns
        # they differ in did. Worth seeing in a profile: it says this fit was checked the
        # harder way, on a name whose family is close.
        **({"self_check_tie": tie_used} if tie_used else {}),
    }
    if match.name != confirmed_name:
        # WHAT IT NEARLY READ, not just that it refused. The commonest cause is a name typed
        # from memory — 「北穿の幽霊城の四鱗のガラクタ」 for a shot that says 「…の妖なる四鱗
        # …」 — and the closest candidate says so at a glance, where a bare None sends
        # somebody looking for a fault in the fit.
        near = f"{match.best!r}" if match.best else "nothing"
        if match.runner_up:
            near += f" (then {match.runner_up!r})"
        raise ValueError(
            f"calibration failed its own check: fitted band {band} font {size}px scored "
            f"{score:.3f}, but recognition returned {match.name!r} instead of "
            f"{confirmed_name!r} (margin {match.margin:.4f}). The closest reading was "
            f"{near} — if that is what the shot says, use it as the name. Refusing to save "
            f"a profile that cannot read the frame it was built from."
        )
    log.info("wddrop: calibrated band=%s size=%dpx offset=%s spacing=%+.1f score=%.3f",
             band, size, offset, spacing, score)
    return profile


# The self-check's tie-break gates, which are the RUNNER's — see MINING_TIE_* in runner.py.
# Set apart from it only because calibration cannot import the runner without dragging the
# capture loop in behind it.
SELF_CHECK_TIE_MARGIN = 0.10
SELF_CHECK_TIE_MIN_SCORE = 0.60


# Where to look for the minimap panel, and how big it plausibly is. Searched rather than
# assumed: the mobile reference put it at x 0.72-0.97 of a portrait frame, while the Steam
# client at 1920x1080 puts it at x 0.91-0.99 — a fixed fractional region fitted to one
# captured almost none of the other.
# THE MINIMAP IS IN A CORNER, IN BOTH LAYOUTS, AND THE SEARCH NOW STARTS THERE.
#
# It began at 0.55 of the width, which is most of the right-hand side of the screen, and
# edge density alone cannot tell a panel from a rock face — so at 1920x1080 it chose a wall
# at x 0.70-0.78 and stored a photograph of it as the HUD template. Episodes then never
# close, and four chests are recorded as one.
#
# Measured on a real walking frame at each resolution:
#
#     1920x1080   floor 0.55 -> x 0.700-0.780  straightness 0.216   a rock
#                 floor 0.85 -> x 0.912-0.992  straightness 0.493   the minimap
#      704x1241   floor 0.55 -> x 0.891-0.970  straightness 0.482
#                 floor 0.85 -> x 0.895-0.974  straightness 0.482   unchanged
#
# So the tighter floor fixes the landscape layout and leaves the portrait one where it was.
# It is a floor rather than a fixed region because the two layouts put the minimap at
# different heights — y 0.16 in portrait, y 0.07 in landscape — and a region fitted to one
# captures almost none of the other.
HUD_SEARCH_X, HUD_SEARCH_Y = 0.85, 0.35
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
        # CLAMPED so the window cannot start where it will not fit. The old bound was
        # `max(int(w * HUD_SEARCH_X) + 1, w - bw)`, which for a wide window and a high floor
        # yields a single start beyond the frame — the crop then indexes past the edge and
        # calibration dies with `index 1957 is out of bounds for axis 1 with size 1920`,
        # which says nothing about minimaps to whoever reads it.
        last = max(0, w - bw)
        for y0 in range(0, max(1, int(h * HUD_SEARCH_Y) - bh), step):
            for x0 in range(min(int(w * HUD_SEARCH_X), last), last + 1, step):
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


# Bands to consider for the template, as fractions of the frame. Wider than tall: the chrome
# is a BAR under the map, and a band deep enough to reach into the map interior carries the
# part that redraws.
HUD_BAND_SHAPES = ((0.08, 0.035), (0.09, 0.05), (0.10, 0.07), (0.12, 0.09))
# A band must look the same on ANOTHER walking frame — that is the whole job of the template
# and the only property a single screenshot cannot show. Measured at 1920x1080 over two
# walking frames taken in different corridors: the icon bar under the map scores 0.694, the
# map interior the old search chose scores 0.221, and the run it produced matched 0 frames
# in 135.
HUD_MIN_STABILITY = 0.45
# ...and must NOT look like the frame with no HUD in it. The drop shot is exactly that frame
# and calibration already has it. 0.15 keeps a comfortable gap under any threshold worth
# setting: the icon bar scores -0.03 against the drop shot, the map's top edge +0.26.
HUD_MAX_LEAK = 0.15
# Where the threshold is allowed to land once both scores are known. The floor exists because
# a threshold fitted from one pair of frames is a thin measurement; the ceiling because 0.60
# was a guess that no correct region at 1920x1080 could clear.
HUD_THRESHOLD_FLOOR, HUD_THRESHOLD_CEILING = 0.35, 0.60
# Two bands this close in stability are the same answer, and the tie goes to the lower one.
HUD_STABILITY_TIE = 0.02
# Mean grey levels the walking frames must differ by for the comparison to mean anything.
# A dungeon supplies more than this from torchlight alone; a player who never moved does not.
HUD_MIN_MOVEMENT = 1.0


def choose_hud_region(walking, absent=None):
    """Pick the template's band by MEASURING the three things it has to do.

    Returns (region, stability, leak).

    WHY ONE SCREENSHOT CANNOT DECIDE THIS
    -------------------------------------
    The old rule was edge density plus a straightness check, and both are properties of a
    single picture. The minimap's interior satisfies them beautifully — it is a dense, ruled
    grid — and it is the one part of the panel that must never be matched, because it redraws
    as the floor is explored and scrolls with the player. At 1920x1080 that is what got
    chosen: a 153x38 crop of map interior, which then matched 0 of 135 frames of the session
    it was fitted for. Episodes never closed, and a whole dive came back as one.

    The property that separates chrome from map is not visible in one frame. It is:

        stability   the same band on ANOTHER walking frame still correlates
        leak        the same band on a frame with NO HUD does not
        straightness it looks like a panel edge rather than scenery

    So this takes several walking frames and, when it has one, the drop shot as the negative.
    With fewer than two walking frames there is nothing to measure and it falls back to the
    density search, which is what every profile fitted before this used.
    """
    from .capture.hud import _to_gray_array, _zncc

    frames = [f.convert("L") for f in (walking if isinstance(walking, (list, tuple))
                                       else [walking])]
    if len(frames) < 2:
        return detect_hud_region(frames[0]), None, None

    import numpy as np

    # DID THE PLAYER ACTUALLY WALK? If the frames are the same picture, every band is
    # perfectly stable — including the map interior — and stability has stopped being
    # evidence of anything. Say so and use the rule that does not need it.
    first = np.asarray(frames[0], dtype=float)
    moved = max(float(np.abs(np.asarray(f, dtype=float) - first).mean()) for f in frames[1:])
    if moved < HUD_MIN_MOVEMENT:
        log.warning("wddrop: the walking shots are the same picture (%.2f levels apart); "
                    "falling back to the density search. Keep walking while it captures.",
                    moved)
        return detect_hud_region(frames[0]), None, None

    negative = absent.convert("L") if absent is not None else None
    w, h = frames[0].size
    sample = (64, 24)
    step = max(4, w // 160)
    found = []
    for fw, fh in HUD_BAND_SHAPES:
        bw, bh = int(w * fw), int(h * fh)
        if bw < 8 or bh < 8:
            continue
        last = max(0, w - bw)
        for y0 in range(0, max(1, int(h * HUD_SEARCH_Y) - bh), step):
            for x0 in range(min(int(w * HUD_SEARCH_X), last), last + 1, step):
                box = (x0, y0, x0 + bw, y0 + bh)
                cuts = [_to_gray_array(f.crop(box), sample) for f in frames]
                stability = min(_zncc(cuts[0], c) for c in cuts[1:])
                if stability < HUD_MIN_STABILITY:
                    continue
                region = (x0 / w, y0 / h, (x0 + bw) / w, (y0 + bh) / h)
                if hud_straightness(frames[0], region) < HUD_STRAIGHTNESS_MIN:
                    continue
                leak = 0.0
                if negative is not None:
                    leak = _zncc(cuts[0], _to_gray_array(negative.crop(box), sample))
                    if leak > HUD_MAX_LEAK:
                        continue
                found.append((stability, leak, region, y0))
    if not found:
        log.warning("wddrop: no band held still between the walking shots; falling back to "
                    "the density search. Were they taken standing in one place?")
        return detect_hud_region(frames[0]), None, None
    # AMONG THE BANDS THAT ARE EQUALLY STABLE, TAKE THE LOWEST. The chrome is under the map in
    # both layouts — buttons and a collapse chevron below a panel of map — so when several
    # bands hold still equally well, depth is the thing that tells them apart. Without this
    # the scan order decides, and the scan starts at the top, i.e. inside the map.
    ceiling = max(s for s, _l, _r, _y in found)
    stability, leak, region, _y0 = max(
        (c for c in found if c[0] >= ceiling - HUD_STABILITY_TIE), key=lambda c: c[3])
    log.info("wddrop: HUD band %s stability %.3f leak %+.3f",
             tuple(round(v, 4) for v in region), stability, leak)
    return region, stability, leak


# What the pair of calibration shots has to prove about itself, as findings rather than as
# printed lines. It lives here because BOTH front ends need it and only one of them had it:
# the command line printed these and the window did not, so a fit made in the window could
# carry a HUD template cut from a wall and say nothing at all. That failure ships silently —
# episodes never close and a dive comes back as one chest.
HUD_SEPARATION_MIN = 0.3


def hud_findings(profile, walk_image, drop_image) -> list[str]:
    """Everything wrong with this pair of shots, in the order a person would notice it.

    Empty means the pair is sound. The template is cut FROM the walk shot, so it scores ~1.0
    there by construction; what matters is that it scores low on the drop shot, where the
    minimap is not showing.
    """
    from .capture.hud import HudDetector

    found: list[str] = []
    if profile.hud_region:
        straight = hud_straightness(walk_image, tuple(profile.hud_region))
        if straight < HUD_STRAIGHTNESS_MIN:
            found.append(
                f"the region found for the minimap does not look like a panel edge "
                f"(straightness {straight:.2f}, expected {HUD_STRAIGHTNESS_MIN:.2f}+). "
                f"If hud_template.png shows a wall, take the walking shot with the minimap "
                f"open.")
    try:
        detector = HudDetector.from_profile(profile)
    except SystemExit:
        return found + ["this profile has no HUD template, so chests cannot be bracketed."]
    walk = detector.read(walk_image.convert("L")).score
    drop = detector.read(drop_image.convert("L")).score
    if walk - drop < HUD_SEPARATION_MIN:
        found.append(
            f"the two shots look alike to the HUD detector (walking {walk:+.3f}, chest "
            f"{drop:+.3f}). The walking one should show the minimap and the chest one should "
            f"not, or chests will never be bracketed.")
    return found


def fit_hud(profile: Profile, walking_frame, region: tuple[float, float, float, float] | None = None,
            template_path: str | Path | None = None, absent=None) -> Profile:
    """Capture the HUD reference from frames taken while walking in a dungeon.

    The template is cut from the player's own screen so it carries their resolution, UI scale
    and theme. It must be the panel CHROME (button bar / chevron), not the map interior — the
    map changes constantly as the floor is explored, so matching it would drift.

    `walking_frame` may be a LIST. Given more than one, the band is chosen by what it does
    across them rather than by how it looks in one — see `choose_hud_region`, and the profile
    then also carries a threshold fitted to the two scores instead of the built-in guess.
    """
    from .capture.hud import DEFAULT_CHROME_REGION, crop_region

    frames = list(walking_frame) if isinstance(walking_frame, (list, tuple)) else [walking_frame]
    walking_frame = frames[0]
    stability = leak = None
    if region is None:
        try:
            region, stability, leak = choose_hud_region(frames, absent)
        except Exception as exc:
            log.warning("wddrop: HUD auto-detect failed (%s); falling back to default", exc)
            region = DEFAULT_CHROME_REGION
    if stability is not None and leak is not None:
        # BETWEEN WHAT WAS MEASURED, not the built-in 0.60 — which is above every score a
        # correct band scored at 1920x1080, and so refused the panel it was cut from.
        profile.hud_threshold = min(HUD_THRESHOLD_CEILING,
                                    max(HUD_THRESHOLD_FLOOR, (stability + leak) / 2))
        profile.notes = dict(profile.notes or {})
        profile.notes.update(hud_stability=round(stability, 4), hud_leak=round(leak, 4),
                             hud_threshold=round(profile.hud_threshold, 4))
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
