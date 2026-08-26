"""
Render-and-compare recogniser — reads the drop line without an OCR model.

THE IDEA
--------
General OCR is the wrong tool here. Our problem is unusually constrained:

  * the answer is one of ~3,400 known names (a CLOSED vocabulary), and
  * the game ships its own fonts, which we extract per locale
    (BaseFont_ChineseTraditional.ttf and friends).

So instead of recognising glyphs, render every candidate in the game's own font and pick the
one whose pixels match the screen. Validated end to end on a real frame from a recording,
against the FULL 2,558-name item vocabulary:

    calibrated  BaseFont_ChineseTraditional @ 26px, offset (0, -1), score 0.862
    recognised  初始的冥刻雜物  score 0.862  margin +0.084  in 233 ms

WHAT THIS BUYS
--------------
  * No OCR engine to bundle, and no per-machine language packs — so the instrument is
    IDENTICAL for every player. For a measurement study that matters more than raw speed:
    an engine whose accuracy varies by machine puts a per-player bias into the data itself.
  * The simplified/traditional problem disappears: candidates are rendered FROM the
    vocabulary, so there is no script to normalise.
  * Deterministic and inspectable — a wrong answer can be reproduced exactly.

WHY ONLY THE PREFIX+NAME IS COMPARED
------------------------------------
The full line is 「獲得了<name> × <qty>！！」 and the quantity is unknown before reading, so
rendering whole lines would need a candidate per (name, quantity) pair. Instead we render
`before + name` — where `before` is the template's fixed text ahead of the placeholder —
and compare it against the LEFT portion of the observed line, ignoring everything after the
name. That is quantity-independent, and it generalises to name-first locales (ja, ko) where
`before` is simply empty.

CALIBRATION IS REQUIRED, AND IS THE REAL WORK
---------------------------------------------
Size and offset are fitted per resolution against a drop the player confirms, and both are
unforgiving:

    wrong size    25px -> rank 7 (margin 0.003); 26px -> rank 1 (margin 0.084)
    wrong offset  a guessed offset scored 0.390 where the fitted one scored 0.862

Font choice matters far less than it first appears. `BaseFont_<locale>.ttf` and
`ScenarioFont_<locale>.ttf` are BYTE-IDENTICAL for zh_tw, zh_cn, ko, de and en — the same
face extracted from two bundles. They differ only for Japanese. The font list is still swept
during calibration (it costs nothing, and ja genuinely needs it), but for most locales the
fit is decided entirely by size and offset.

THE MARGIN GATE
---------------
Top-1 score alone is not enough — a wrong size still produces a confident-looking 0.43. A
result is accepted only when it clears an absolute score AND beats the runner-up by a
margin; otherwise it is refused and falls through to the review queue, exactly like a
low-confidence text match.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("wddrop.glyph")

# Ink threshold for locating text against the dungeon behind it.
INK_LEVEL = 150
# Alignment search, in pixels, around the anchored origin.
DX_RANGE = (-2, 7)
DY_RANGE = (-3, 6)
# Fallback comparison window (w, h). REAL deployments must size this from the vocabulary via
# required_window(): a fixed 380px covered only 82% of zh_tw names at 26px, so the longest
# 18% would have been silently truncated and never matchable. The window must also be wide
# enough to contain the whole tail, since quantity is read from it.
WINDOW = (380, 30)
# Slack above the anchor, so a slightly high glyph is not clipped.
PAD = 3
# Acceptance gates. A candidate must both look right AND be clearly better than the next.
# Measured on a real frame: correct answer 0.862 with margin 0.084; the best WRONG size
# produced 0.433 with margin 0.003. Both gates are needed — score alone would accept a
# confident-looking but undiscriminated match.
MIN_SCORE = 0.60
MIN_MARGIN = 0.03
# A candidate wider than the observed line cannot be a prefix of it; allow a little slack
# for anti-aliasing and hinting.
WIDTH_SLACK_PX = 6

# SECOND LOOK, AT A WIDER ALIGNMENT.
# The band's search is +-1px around the calibrated offset, which is enough when the glyphs on
# screen advance exactly as the atlas says they do. Names written in ASCII DIGITS do not: the
# game's digits are narrower than the atlas renders them, so the mismatch ACCUMULATES along
# the line and by the last character the observation sits several pixels left of the template.
# Measured on 「100バイン紙幣×2を手に入れた!!」, a real chest line that was silently dropped:
#
#     +-1 (the band's own search)   0.5428   refused, under the 0.60 gate
#     +-2                          0.6265   accepted, margin 0.1413
#     +-3                          0.6884   accepted, margin 0.1942
#
# Widening the search for EVERY candidate is not affordable — 35ms -> 228ms per recognition,
# against a 50ms frame budget at 20fps, and a frame not sampled is a line that can never be
# recovered. So only the handful that came top of the cheap pass are looked at again: 8
# candidates over +-3px costs 3.7ms beside the full pass's 35. They are re-scored against each
# other under the same gates, so the second look changes the alignment, not the standard of
# proof.
REFIT_RADIUS = 3
REFIT_CANDIDATES = 8

# The template matrix is stored at half resolution in float32. Measured on a real 635x45
# Steam window over 2,558 candidates:
#     float64 full  585 MB  143 ms  rank 1  margin 0.0627
#     float32 full  292 MB   89 ms  rank 1  margin 0.0627
#     float32 /2     75 MB   28 ms  rank 1  margin 0.0552   <- chosen
#     float32 /3     33 MB    3.6ms rank 2  margin 0.0002   <- breaks
# 770 MB resident and ~230 ms per match was making the game lag; /3 is where discrimination
# collapses, so /2 is the last safe step rather than an arbitrary one.
TEMPLATE_DOWNSCALE = 2
TEMPLATE_DTYPE = "float32"


@dataclass(frozen=True)
class RenderMatch:
    name: str | None
    score: float
    margin: float
    accepted: bool
    runner_up: str | None = None
    # The best candidate WHETHER OR NOT it was accepted. `name` is deliberately None on a
    # refusal, so that a caller cannot use a rejected reading by accident — but a caller that
    # wants to disambiguate the top two needs to know what they were.
    best: str | None = None
    # Ink width of the winning template. Comparing it against the observed ink width tests
    # directly whether the line on screen was COMPLETE, which is what a half-drawn line
    # actually differs in -- score only proxies for it, and proxies badly: a legitimate line
    # whose name is ASCII digits scored 0.644 where CJK names score 0.83+.
    template_width: int = 0
    # The best few candidates, in order, whether or not any was accepted. A caller that wants
    # to look again -- at a wider alignment, or over the columns that separate the top two --
    # needs the shortlist rather than a verdict. See RenderRecognizer.refit.
    shortlist: tuple[str, ...] = ()


def _np():
    import numpy as np

    return np


# A column must have at least this many lit pixels to count as text. Measured cost of not
# having it: a SINGLE lit pixel at column 0 -- one pixel, in a 1920-wide band -- anchored the
# comparison window 733px left of the actual message and lost a whole chest. A glyph column
# lights several pixels; scene bleed and UI edges light one.
MIN_COLUMN_INK = 2
# ...but the glyph the box then starts at may itself begin with a column too thin to count.
# 「上」 opens with a short horizontal tick lighting ONE pixel per column for nine columns, so
# the box began nine pixels inside the character and every rendered candidate was compared
# against a line shifted out from under it — measured on a real mining panel: 0.545 and the
# wrong name, against 0.866 and the right one from the same frame anchored properly.
#
# Recovered by walking back over columns that still carry ink and stopping at the first empty
# one, which is what separates a thin stroke of the SAME glyph from the stray single pixel
# MIN_COLUMN_INK exists to reject: the stray one has empty columns beside it. Bounded so a
# lit background cannot walk the anchor off to the left edge.
ANCHOR_RECOVER_MAX = 40


def ink_bbox(gray, level: int = INK_LEVEL, min_column_ink: int = MIN_COLUMN_INK):
    """Tight box around the lit pixels, ignoring columns too sparse to be text."""
    np = _np()
    mask = np.asarray(gray, dtype=float) > level
    if not mask.any():
        return None
    cols = np.where(mask.sum(axis=0) >= min_column_ink)[0]
    if len(cols) == 0:
        return None
    rows = np.where(mask[:, cols].any(axis=1))[0]
    return int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1


def zncc(a, b) -> float:
    """Zero-mean normalised cross-correlation. Insensitive to overall brightness, which
    varies wildly with whatever dungeon is showing behind the text."""
    np = _np()
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denom) if denom else 0.0


class GlyphRenderer:
    """Renders text in the game's own font, onto a FIXED-SIZE canvas.

    Fixed size, not cropped to ink: see RenderRecognizer for why that is the whole trick.

    LETTER SPACING IS FITTED, NOT INHERITED FROM THE FONT
    ----------------------------------------------------
    The game advances characters slightly differently from PIL. Over a short name that is
    invisible; over a long one the error accumulates until the later glyphs no longer line
    up at all, and the correlation collapses. Measured on a real 14-character line:

        spacing +0.0   true 0.560   rival 0.574   -> the WRONG item scores higher
        spacing +0.4   true 0.822   rival 0.787   -> correct, with a usable margin

    That is why the fix could not have been "lower the threshold": at the font's own spacing
    the wrong candidate wins outright, so a lower floor would have recorded the wrong name.
    Text is therefore drawn character by character with a fitted per-character delta.
    """

    def __init__(self, font_path: str | Path, size: int, window: tuple[int, int] = WINDOW,
                 letter_spacing: float = 0.0):
        from PIL import ImageFont

        self.font_path = str(font_path)
        self.size = size
        self.window = window
        self.letter_spacing = letter_spacing
        self._font = ImageFont.truetype(self.font_path, size)

    def render(self, text: str):
        from PIL import Image, ImageDraw

        np = _np()
        w, h = self.window
        canvas = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(canvas)
        x = 0.0
        for ch in text:
            draw.text((x, 0), ch, font=self._font, fill=255)
            advance = self._font.getlength(ch)
            # Full-width glyphs only, for the reason measured in AtlasRenderer._spacing_for:
            # the spacing is a correction to the FULL-WIDTH advance, and adding it to a
            # narrow glyph over-advances by most of its own value on every character. A font
            # has no `reference`, so full-width is read as "about as wide as it is tall",
            # which is what a CJK em square is.
            x += advance + (self.letter_spacing if advance >= self.size * 0.9 else 0.0)
        return np.asarray(canvas, dtype=float)

    def ink_width(self, text: str) -> int:
        box = ink_bbox(self.render(text))
        return 0 if box is None else box[2] - box[0]


# Composition happens at this multiple of the target size, then the whole line is downscaled
# once. Per-glyph placement at the target size rounds to whole pixels and loses ~0.06 of
# correlation; supersampling keeps the error sub-pixel.
SUPERSAMPLE = 3

# The atlas sheet is a single large image (5616x5616 for zh_tw) and calibration constructs a
# renderer for every size/spacing combination it sweeps -- around a thousand. Re-decoding the
# PNG each time made calibration take minutes; it is decoded once per path instead.
_SHEET_CACHE: dict[str, object] = {}
_META_CACHE: dict[str, dict] = {}


class AtlasRenderer:
    """Renders text from a glyph atlas instead of a font file.

    Drop-in for GlyphRenderer. Exists so the client can ship no font: the game's typeface is
    commercially licensed and lives only inside encrypted bundles, but a bitmap-per-character
    atlas plus advance widths reproduces it — measured slightly BETTER than the font itself
    (0.9085 vs 0.8889), because storing glyphs large and scaling down approximates the game's
    anti-aliasing more closely than rendering straight at the target size.

    Glyphs are stored at one reference size and scaled to whatever calibration fitted, since
    the fitted size varies per player and resolution.
    """

    def __init__(self, atlas_path: str | Path, size: int, window: tuple[int, int] = WINDOW,
                 letter_spacing: float = 0.0):
        import json

        from PIL import Image

        key = str(atlas_path)
        meta = _META_CACHE.get(key)
        if meta is None:
            meta = json.loads(Path(atlas_path).read_text(encoding="utf-8"))
            _META_CACHE[key] = meta
        self.size = size
        self.window = window
        self.letter_spacing = letter_spacing
        self.reference = meta["reference_size"]
        self.cell = meta["cell"]
        self.pad = meta["pad"]
        self._index = meta["index"]
        sheet = _SHEET_CACHE.get(key)
        if sheet is None:
            sheet = Image.open(Path(atlas_path).with_suffix(".png")).convert("L")
            sheet.load()
            _SHEET_CACHE[key] = sheet
        self._sheet = sheet
        self._scale = size / self.reference
        self._cache: dict[str, object] = {}
        self.missing: set[str] = set()

    def _glyph(self, ch: str):
        """Glyph scaled to the SUPERSAMPLED size, cached — names reuse the same characters."""
        from PIL import Image

        if ch in self._cache:
            return self._cache[ch]
        entry = self._index.get(ch)
        if entry is None:
            # Recorded rather than silently skipped: a missing glyph means the atlas and the
            # vocabulary disagree, which would otherwise surface as unexplained misses.
            #
            # AND SAID OUT LOUD, because `missing` was written down and read by nothing. A
            # character the sheet lacks is drawn as a hole; the name then scores like a
            # misread, is refused on margin, and is absent from a record that still looks
            # complete -- so the only evidence it ever happened lived in a set nobody asked.
            # `ui._refresh_setup` now catches the ordinary cause before a session starts (an
            # atlas older than the item table) and rebuilds; this is the witness for every
            # other way it can happen -- a template character no item name contains, a sheet
            # replaced under a running session, a locale whose fonts could not draw it.
            #
            # ONCE PER CHARACTER PER RENDERER, and only because the cache on the next line
            # makes this branch unreachable a second time. `_glyph` runs per character of
            # every candidate and `build` renders thousands of them, so a log line that could
            # repeat here would bury the trace this file exists to write.
            self.missing.add(ch)
            self._cache[ch] = None
            log.debug("wddrop: the atlas cannot draw %r (U+%04X) -- every name containing it "
                      "renders with a hole and is refused", ch, ord(ch))
            return None
        box = (entry["x"], entry["y"], entry["x"] + self.cell, entry["y"] + self.cell)
        side = max(1, int(round(self.cell * self._scale * SUPERSAMPLE)))
        glyph = self._sheet.crop(box).resize((side, side), Image.LANCZOS)
        self._cache[ch] = glyph
        return glyph

    def render(self, text: str):
        """Compose supersampled, then downscale ONCE.

        Pasting each glyph straight at the target size rounds its position to a whole pixel,
        and the padding compensation rounds again -- up to half a pixel of error per glyph,
        which measured 0.8301 against the font's 0.8889. Composing at SUPERSAMPLE x and
        downscaling the finished line keeps those errors sub-pixel and recovers the full
        score.
        """
        from PIL import Image

        np = _np()
        w, h = self.window
        ss = SUPERSAMPLE
        canvas = Image.new("L", (w * ss, h * ss), 0)
        offset = self.pad * self._scale * ss
        x = 0.0
        for ch in text:
            glyph = self._glyph(ch)
            if glyph is not None:
                canvas.paste(glyph, (int(round(x - offset)), int(round(-offset))), glyph)
            entry = self._index.get(ch)
            raw = entry["advance"] if entry else self.reference * 0.5
            advance = raw * self._scale
            x += (advance + self._spacing_for(raw)) * ss
        return np.asarray(canvas.resize((w, h), Image.LANCZOS), dtype=float)

    def _spacing_for(self, raw_advance: float) -> float:
        """The fitted spacing, for FULL-WIDTH glyphs only.

        LETTER SPACING IS NOT TRACKING, AND A NARROW GLYPH DOES NOT GET IT. It is the
        difference between what the atlas says a glyph advances and what the game actually
        advances it, and that difference was fitted against text made entirely of full-width
        characters — where a constant and a proportion are indistinguishable. Applied to an
        ASCII glyph too, it over-advances by most of its own value, EVERY character, and the
        error accumulates until the rest of the line no longer lines up at all.

        Measured on 「10,000バイン紙幣を手に入れた!!」, a real chest line that was silently
        dropped. Best alignment per character at the fitted 25px/+1.1:

            1:-2  0:-4  ,:-5  0:-6  0:-7  0:-8  バ:-9  イ:-9  ン:-9  紙:-9  幣:-10
                  └──────── drifts 1-2px per ASCII glyph ────────┘└─ then stops ─┘

        Nine pixels by the time the kana begin, and flat from there — the full-width advance
        is right and only the narrow ones are wrong. Every glyph matched its own bitmap at
        0.82-0.95 throughout: the shapes were never the problem, only where they were put.

        Whole-line score for that name, same frame: 0.3771 flat, 0.8257 this way, against a
        0.60 gate. Widening the alignment search cannot fix it — the drift is internal to the
        line, so +-12px still only reached 0.548.

        NOTHING MADE OF FULL-WIDTH CHARACTERS CHANGES BY A HAIR, which is most of this
        vocabulary and every fit already made: with `raw == reference` the two models are the
        same arithmetic. Measured on the same session, to four decimals — 金の針 0.8531,
        ランペール金貨 0.8131, 北穿の幽霊城の妖なる乳白色のガラクタ 0.7282, before and after.
        """
        return self.letter_spacing if raw_advance >= self.reference else 0.0

    def ink_width(self, text: str) -> int:
        box = ink_bbox(self.render(text))
        return 0 if box is None else box[2] - box[0]


def make_renderer(source: str | Path, size: int, window: tuple[int, int] = WINDOW,
                  letter_spacing: float = 0.0):
    """Renderer for a font file or a glyph atlas, chosen by extension.

    One entry point so calibration, the runner and the tools cannot disagree about which is
    in use: a .json is an atlas, anything else is a font.
    """
    renderer = (AtlasRenderer if str(source).lower().endswith(".json") else GlyphRenderer)(
        source, size, window, letter_spacing
    )
    # WHAT IT WAS BUILT FROM, kept on it. A caller that wants the same face at another size
    # or spacing — the panel's fit, the band's second look at spacing — would otherwise have
    # to carry the path alongside every renderer it holds, and one of them would drift.
    renderer.source = str(source)
    return renderer


def _text_left(band_gray, x0: int, level: int = INK_LEVEL) -> int:
    """The true left edge of the text, given the thresholded box's edge.

    See ANCHOR_RECOVER_MAX: the box starts at the first column with enough ink to be sure it
    is text, which can be several columns into a glyph that opens thinly. Walking back over
    columns that have ANY ink recovers those without re-admitting the isolated pixel the
    threshold is there to reject.
    """
    np = _np()
    lit = np.asarray(band_gray, dtype=float).max(axis=0) > level
    x = x0
    while x > 0 and x0 - x < ANCHOR_RECOVER_MAX and lit[x - 1]:
        x -= 1
    return x


def anchor_window(frame_gray, band: tuple[int, int], window: tuple[int, int] = WINDOW,
                  x0_fixed: int | None = None, columns: tuple[int, int] | None = None):
    """Cut the comparison window, anchored on where the text actually starts.

    `band` is (top, bottom) of the calibrated message region. Anchoring at a known origin is
    what lets a rendered candidate be compared position-for-position, so name LENGTH becomes
    part of the signal.

    `x0_fixed` is the message's left edge as measured during calibration, and is preferred
    when available: the drop line is left-aligned at a constant x, so deriving the origin
    from whatever happens to be lit in the band makes every reading hostage to stray pixels
    elsewhere on that row.

    `columns` bounds where that ink is looked for. It fixes the OTHER half of the anchor: the
    row the text sits on comes from the ink box either way, and on a whole frame at 1920x1080
    the brightest thing in the band's rows is usually the dungeon, not the message.
    """
    np = _np()
    arr = np.asarray(frame_gray, dtype=float)
    top, bottom = band
    left, right = columns if columns else (0, arr.shape[1])
    strip = arr[top:bottom, left:right]
    box = ink_bbox(strip)
    if box is None:
        return None
    x0 = x0_fixed if x0_fixed is not None else left + _text_left(strip, box[0])
    y0 = top + box[1]
    w, h = window
    out = np.zeros((h, w))
    src = arr[max(0, y0 - PAD) : y0 - PAD + h, max(0, x0 - PAD) : x0 - PAD + w]
    out[: src.shape[0], : src.shape[1]] = src
    return out


class RenderRecognizer:
    """Matches an observed line window against rendered vocabulary candidates.

    WHY A FIXED WINDOW, NOT A CROPPED TEMPLATE
    ------------------------------------------
    Cropping each candidate to its own ink and sliding it was tried and FAILS: it throws
    away name length and absolute position, so every candidate of similar width looks alike.
    Measured on a real frame, tight-cropping put the true name at rank 4-9 of 464 with a
    margin of 0.0002-0.014 — i.e. the top answer was wrong.

    Rendering each candidate into a window the same size as the observation, anchored at the
    same origin, keeps that signal: a name that is too long or too short mismatches in the
    tail. Same frame, same vocabulary, full 1,071 candidates: **rank 1, score 0.862, margin
    +0.084**.

    BLUR WAS ALSO TRIED, AND IS HARMFUL
    -----------------------------------
    Gaussian blur lifts the absolute score (0.275 -> 0.694) by tolerating stroke-level
    differences, but it collapses discrimination: at r=3 the margin fell to 0.0002 and the
    top match was wrong. Absolute score is not the objective; separation is.

    SIZE CALIBRATION IS MAKE-OR-BREAK
    ---------------------------------
    One pixel of font size ruins it — 26px gave rank 1 (margin 0.084) where 25px gave rank 7
    (margin 0.003) on the same frame. Conveniently that makes calibration self-scoring:
    the correct size is simply the one whose best candidate scores highest.
    """

    def __init__(
        self,
        renderer: GlyphRenderer,
        prefix: str,
        names: list[str],
        *,
        min_score: float = MIN_SCORE,
        min_margin: float = MIN_MARGIN,
        shifts: tuple[range, range] | None = None,
    ):
        self.renderer = renderer
        # Template text before the {0} placeholder: 獲得了 for zh_tw, "" for ja/ko.
        self.prefix = prefix
        self.min_score = min_score
        self.min_margin = min_margin
        self.dx_range, self.dy_range = shifts or (range(*DX_RANGE), range(*DY_RANGE))
        self._names: list[str] = []
        self._templates = []
        self._widths = []
        self.build(names)

    def build(self, names: list[str]) -> None:
        """Pre-render every candidate once and store it PRE-NORMALISED.

        ZNCC against a fixed observation is a dot product once both sides are zero-meaned
        and unit-scaled, so the whole vocabulary becomes one matrix multiply per alignment
        offset. Doing that here rather than per drop is what makes recognition fast enough
        to run inline: the naive per-candidate Python loop took 5.2s for 2,558 candidates.
        """
        np = _np()
        self._names, rows, widths = [], [], []
        # Deduplicate by NAME. The vocabulary lists the same display name more than once
        # (107 of 3,381 entries in zh_tw — an item and an equipment family sharing a name,
        # or repeated item rows). Two identical names render identically, so the runner-up
        # scores exactly the same and the margin is 0.0000 — and the ambiguity gate then
        # rejects the LEAST ambiguous case there is, where both candidates are the same word.
        # Measured: a real drop scoring 0.877 was thrown away this way, and 107 names could
        # never have been recognised at all.
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            try:
                tpl = self.renderer.render(self.prefix + name)
            except Exception as exc:  # a glyph the font cannot draw
                log.debug("wddrop: cannot render %r: %s", name, exc)
                continue
            box = ink_bbox(tpl)
            flat = tpl[::TEMPLATE_DOWNSCALE, ::TEMPLATE_DOWNSCALE].astype(TEMPLATE_DTYPE).ravel()
            flat = flat - flat.mean()
            norm = float(np.sqrt((flat * flat).sum()))
            self._names.append(name)
            rows.append(flat / norm if norm else flat)
            widths.append(0 if box is None else box[2] - box[0])
        self._templates = np.stack(rows) if rows else np.zeros((0, 1))
        self._widths = np.asarray(widths)
        self._shape = self.renderer.window[1], self.renderer.window[0]
        log.info("wddrop: render index built (%d candidates)", len(self._names))

    def recognize(self, window, observed_ink_width: int | None = None) -> RenderMatch:
        """Identify the name in an observed window (from `anchor_window`)."""
        np = _np()
        if len(self._names) == 0:
            return RenderMatch(None, 0.0, 0.0, False)

        # Width prefilter: the rendered prefix+name cannot be wider than the observed line.
        # Cheap, and it removes most of the vocabulary before any correlation runs.
        candidates = range(len(self._names))
        if observed_ink_width:
            keep = np.where(self._widths <= observed_ink_width + WIDTH_SLACK_PX)[0]
            if len(keep):
                candidates = keep

        subset = self._templates[candidates] if not isinstance(candidates, range) else self._templates
        names = ([self._names[i] for i in candidates]
                 if not isinstance(candidates, range) else self._names)
        if subset.shape[0] == 0:
            return RenderMatch(None, 0.0, 0.0, False)

        best = self._correlate(window, subset, self.dx_range, self.dy_range)
        return self._verdict(best, names)

    def _correlate(self, window, subset, dx_range, dy_range):
        """Best ZNCC per template over the alignment search.

        Rolling the OBSERVATION is equivalent to rolling every template the other way, and
        lets the pre-normalised template matrix be reused across offsets.
        """
        np = _np()
        best = np.full(subset.shape[0], -1.0, dtype=TEMPLATE_DTYPE)
        for dy in dy_range:
            for dx in dx_range:
                shifted = np.roll(np.roll(window, -dy, 0), -dx, 1)
                shifted = shifted[::TEMPLATE_DOWNSCALE, ::TEMPLATE_DOWNSCALE]
                shifted = shifted.astype(TEMPLATE_DTYPE).ravel()
                shifted = shifted - shifted.mean()
                norm = float(np.sqrt((shifted * shifted).sum()))
                if not norm:
                    continue
                np.maximum(best, subset @ (shifted / norm), out=best)
        return best

    def _verdict(self, best, names) -> RenderMatch:
        """Apply the acceptance gates to a scored candidate set."""
        np = _np()
        order = np.argsort(-best)
        top_score, top_name = float(best[order[0]]), names[order[0]]
        runner, runner_name = (
            (float(best[order[1]]), names[order[1]]) if len(order) > 1 else (0.0, None)
        )
        margin = top_score - runner
        accepted = top_score >= self.min_score and margin >= self.min_margin
        try:
            width = int(self._widths[self._names.index(top_name)])
        except (ValueError, IndexError):
            width = 0
        return RenderMatch(
            name=top_name if accepted else None,
            best=top_name,
            score=top_score,
            margin=margin,
            accepted=accepted,
            runner_up=runner_name,
            template_width=width,
            shortlist=tuple(names[i] for i in order[:REFIT_CANDIDATES]),
        )

    def refit(self, window, names, *, shifts) -> RenderMatch:
        """Score a shortlist again, over a different alignment search.

        For the case the cheap pass cannot decide: the right name is in the shortlist but sits
        a few pixels off the alignment the calibration predicted, so it scores below the gate
        (see REFIT_RADIUS). The shortlist competes under the SAME gates, which is what keeps
        this a second look rather than a lower bar — a line that is not in the vocabulary at
        all does not become one by being measured more carefully.
        """
        np = _np()
        index = {name: i for i, name in enumerate(self._names)}
        keep = [index[n] for n in dict.fromkeys(names) if n in index]
        if not keep:
            return RenderMatch(None, 0.0, 0.0, False)
        dx_range, dy_range = shifts
        best = self._correlate(window, self._templates[keep], dx_range, dy_range)
        return self._verdict(best, [self._names[i] for i in keep])


# Per-character spacing deltas tried during calibration, in pixels. The measured correction
# on a 1920x1080 Steam client was +0.4; the range is deliberately wider than any value seen,
# since it depends on UI scale.
SPACING_STEPS = tuple(i / 10 for i in range(-6, 21))


def calibrate(
    window,
    known_name: str,
    prefix: str,
    font_paths: list[str | Path],
    sizes: range,
    window_size: tuple[int, int] = WINDOW,
) -> tuple[str, int, tuple[int, int], float, float]:
    """Fit (font, size) against a drop whose name the player has confirmed.

    Both matter and neither can be assumed: ScenarioFont scored 0.28 where BaseFont scored
    0.86 on the same crop, and one pixel of size is the difference between rank 1 and rank 7.
    """
    np = _np()
    best = ("", 0, (0, 0), -1.0, 0.0)
    for path in font_paths:
        for size in sizes:
            for spacing in SPACING_STEPS:
                try:
                    tpl = make_renderer(path, size, window_size, spacing).render(prefix + known_name)
                except Exception:
                    continue
                for dy in range(*DY_RANGE):
                    for dx in range(*DX_RANGE):
                        score = zncc(window, np.roll(np.roll(tpl, dy, 0), dx, 1))
                        if score > best[3]:
                            best = (str(path), size, (dx, dy), score, spacing)
    if best[3] < 0:
        raise ValueError("no font/size candidate could be fitted")
    # The offset is returned so recognition can search a TIGHT range around it. Guessing it
    # instead is not survivable: an offset wrong by a few pixels scored 0.390 where the
    # fitted one scored 0.862 on the same frame.
    log.info("wddrop: calibrated font=%s size=%d offset=%s score=%.3f spacing=%+.1f", *best)
    return best


def calibrate_on_invariant(
    window,
    invariant: str,
    font_paths: list[str | Path],
    sizes: range,
    window_size: tuple[int, int] = WINDOW,
) -> tuple[str, int, tuple[int, int], float, float]:
    """Fit (font, size, spacing) against text whose POSITION in the line is not known.

    `calibrate` fits against text that starts where the line starts, which is what the
    invariant is in a name-last locale: 「獲得了…」 begins every Chinese drop line, so it can
    be rendered at the origin and correlated in place.

    A name-FIRST locale has no such text. 「{0}を手に入れた!!」 puts the only invariant at the
    END, after a name that is a different width every time, so there is no fixed x to render
    it at — and asking `calibrate` for it means fitting against the empty string, which is
    what Japanese calibration was doing: it returned size 12 (the smallest in the sweep) at
    score 0.000, and the name-reader downstream then recognised nothing at all, every time.

    It is anchored on the line's RIGHT EDGE rather than searched for. The invariant is the
    last thing on the line, so wherever the name ended, the two end together — which turns an
    unknown position into a known one and costs the same as `calibrate` does. Sliding it
    across the band instead was tried and is not affordable: every (size, spacing, dy) then
    scans ~700 columns, and one proposal took 112 seconds.

    The returned dx is 0, not the fitted one: it aligns the invariant, and the caller anchors
    the NAME on the band's own left edge.
    """
    np = _np()
    box = ink_bbox(window)
    if box is None:
        raise ValueError("nothing lit in the window to fit the invariant against")
    right = box[2]
    # From the WINDOW, not the caller. The observation is cut to hold a whole line, which is
    # not the fixed default the renderer would otherwise draw into, and a canvas of a
    # different height cannot be correlated against it at all — it raises rather than scoring
    # badly, so every candidate was skipped and the fit came back empty.
    window_size = (window.shape[1], window.shape[0])
    best = ("", 0, (0, 0), -1.0, 0.0)
    for path in font_paths:
        for size in sizes:
            for spacing in SPACING_STEPS:
                try:
                    tpl = make_renderer(path, size, window_size, spacing).render(invariant)
                except Exception:
                    continue
                shape = _hcrop(tpl)
                if shape is None or shape.shape[1] >= window.shape[1]:
                    continue
                width = shape.shape[1]
                for dy in range(*DY_RANGE):
                    shifted = np.roll(shape, dy, 0)
                    for dx in range(-4, 5):
                        x = right - width + dx
                        if x < 0 or x + width > window.shape[1]:
                            continue
                        score = zncc(window[:, x:x + width], shifted)
                        if score > best[3]:
                            best = (str(path), size, (0, dy), score, spacing)
    if best[3] < 0:
        raise ValueError("no font/size candidate could be fitted to the invariant text")
    log.info("wddrop: fitted on invariant font=%s size=%d offset=%s score=%.3f spacing=%+.1f",
             *best)
    return best


def centred_shifts(offset: tuple[int, int], radius: int = 1) -> tuple[range, range]:
    """Tight search around a calibrated offset, for per-drop recognition.

    Calibration pins the alignment; at run time only jitter needs absorbing. Narrowing the
    search from the calibration-wide range is a straight speed win.
    """
    dx, dy = offset
    return range(dx - radius, dx + radius + 1), range(dy - radius, dy + radius + 1)


# A plausibility cap, no longer a search bound. It WAS 99, on the assumption that a single
# line could not pay out more — and then a chest paid 600 Gil, which could not be a candidate
# at all and was recorded as unknown. Digits are read one at a time now, so the cap costs
# nothing to set: it only decides when a number stops being believable.
#
# Five digits, on a player's report that a single line does pay that much. The largest
# quantity WE have observed is 600 Gil, so this is headroom rather than a measurement, and it
# is the right direction to be wrong in: too low silently converts a real payout into
# `qty_unknown`, which looks exactly like a reading failure and cannot be told from one
# afterwards. Too high costs nothing — the digits still have to be legible one at a time, and
# an unreadable one is refused on its own margin.
#
# This lines up with MAX_DIGITS, so the cap no longer rejects anything the digit count would
# have allowed. It stays as the knob to tighten if a five-digit misread is ever seen.
MAX_QUANTITY = 99_999
# Longer than this is not a quantity — it is the reader having found something that is not a
# number, and an answer built out of that many noise segments is worse than no answer. Kept
# above MAX_QUANTITY's own width on purpose: this rejects NOISE, the cap rejects the
# IMPLAUSIBLE, and a four-digit line that really is a number should be refused as the latter.
MAX_DIGITS = 5
# How far the winning digit must beat the runner-up. Measured over every quantity 1..99 drawn
# with the game's own font and read back with each font:
#
#                       ungated              gated at 0.05
#     game font      99 right,  0 wrong    99 right,  0 wrong,  0 unknown
#     PMingLiU       81 right, 18 wrong    75 right,  1 wrong, 23 unknown
#
# Free for the font the study runs on, and for a substitute it turns 17 of 18 wrong numbers
# into `qty_unknown`. That is the trade this project wants: an unknown is a gap in the data,
# a wrong number is a false measurement that nothing downstream can detect.
QTY_MIN_MARGIN = 0.05
# Whether a 「×」 is on screen at all is what separates a line showing a quantity from one
# showing none, so this floor decides when a number could get fabricated. Over the verified
# session the gap is wide, and the same for a font that is not the game's:
#
#     separator present   game 0.92-0.96   PMingLiU 0.81-0.85
#     no separator        game 0.37-0.48   PMingLiU 0.32-0.39   (the tail of the last glyph)
#
# Wrong LOW invents a quantity on a line that has none; wrong HIGH loses a real one to
# `qty_unknown`, which is the safe side.
SEPARATOR_MIN_SCORE = 0.60
# How far the separator may sit from where the rendered name ENDS. A font that is not the
# game's accumulates a per-glyph error across the whole name, so this is a font-mismatch
# budget, not a layout one.
QTY_SEARCH_LEFT, QTY_SEARCH_RIGHT = 30, 50
# A candidate whose rendered digits are far wider or narrower than the ink actually on screen
# is not that number. This is what kills the ×62 class outright: two digits do not fit in a
# ten-pixel span however the correlation falls.
DIGIT_WIDTH_TOLERANCE = 0.35
# How much a captured glyph may be wider or narrower than its own rendering before the shape
# comparison stops recognising it. One pixel: that is what antialiasing and a PNG do to a
# stroke, measured on both the game's own font and a substitute.
DIGIT_WIDTH_SLACK = 1
# A whole LINE is found with MIN_COLUMN_INK=2, which suppresses stray antialiasing across
# hundreds of columns. One digit cannot afford it: the outermost column of a 「3」 is the tip
# of an arc, one or two pixels tall, so a 2-pixel floor reads it as empty and the walk that
# skips the gap eats it. Measured: a 「3」 10 columns wide arrived as 6 and lost to 「1」.
DIGIT_MIN_COLUMN_INK = 1
# The digits' own ink threshold, as a fraction of how bright they actually are — see
# _digit_ink_level. The floor keeps an empty region from thresholding at nearly zero.
DIGIT_INK_FRACTION = 0.5
DIGIT_INK_FLOOR = 75


def _lit(window, x: int, level: float = INK_LEVEL) -> bool:
    return int((window[:, x] >= level).sum()) >= DIGIT_MIN_COLUMN_INK


def _digit_ink_level(window, x0: int, x1: int) -> float:
    """Ink threshold for the digits, taken from how bright THEY are.

    A fixed 150 is right for finding a line across a dungeon and wrong for one small glyph.
    The 1920x1080 message band draws dimmer than the mining panel: a 「3」 there peaked at 204
    with its left columns at 133/128/142 — under the fixed threshold, so three of its nine
    columns were invisible, the span measured 6px, and 「1」 (5px) beat 「3」 (9px) on width.
    A wrong quantity, recorded as measurement.

    Half the local peak, floored so a blank region cannot produce a threshold of nearly zero
    and call the background text.

    MEASURED OVER THE SEPARATOR, NOT OVER THE WHOLE SPAN, and that is the difference between
    reading 「×10」 and reporting `qty_unknown`. The span reaches past the digits into the
    words after them, and CJK is drawn with thicker strokes than half-width digits: on a real
    1920x1080 line the 「を手に入れた!!」 peaked at 203 while the digits peaked at 153, so half
    the local peak was 101 — above the whole left half of the 「1」. The walk then started
    inside it, the span came out one digit wide, and a 10-item chest was recorded as unknown.
    The 「×」 is the same size and weight as the digits it introduces, and it is at a position
    that is already known exactly, so it is the honest thing to measure.
    """
    np = _np()
    region = np.asarray(window, dtype=float)[:, max(0, x0):max(x0 + 1, x1)]
    return max(DIGIT_INK_FLOOR, float(region.max()) * DIGIT_INK_FRACTION)


def _hcrop(img):
    """Columns of ink, full height — so a slide keeps the vertical alignment of the band."""
    box = ink_bbox(img, min_column_ink=DIGIT_MIN_COLUMN_INK)
    return None if box is None else img[:, box[0]:box[2]]


def _slide(window, shape, x_from: int, x_to: int, dy: int = 0):
    """Best (score, x) for `shape` over the window's columns in [x_from, x_to]."""
    np = _np()
    best, at = -1.0, None
    width = shape.shape[1]
    x_from, x_to = max(0, x_from), min(window.shape[1] - width, x_to)
    for ddy in (dy - 1, dy, dy + 1):
        shifted = np.roll(shape, ddy, 0)
        for x in range(x_from, x_to + 1):
            got = zncc(window[:, x:x + width], shifted)
            if got > best:
                best, at = got, x
    return best, at


# A cut this close to the anchor is not a line with a short name in it — it is the invariant
# text matching somewhere it should not, and masking there would blank the name itself.
MIN_NAME_PX = 8
# What the separator must score to move the cut LEFT of the template's words.
#
# Far above SEPARATOR_MIN_SCORE, and the difference is the search, not the glyph. The quantity
# reader looks for 「×」 in a 80px window either side of where it already knows the name ends,
# and 0.60 separates a real one from a false one there. Here the search runs across the whole
# name, so the best of a hundred positions is being asked to prove that any of them is real —
# and on a line with NO quantity at all, some stroke always wins. Measured across nine item
# names, both forms of the line:
#
#     a real 「×」            0.97 - 1.00
#     the best false one     0.43 - 0.66      <- over the 0.60 gate, so it cut the NAME
#
# 「モニヨン銀貨を手に入れた!!」 cut at 87px, mid-name, and read as nothing. Nothing else about
# that item is unusual; it is simply which kana it happens to be spelled with.
SEPARATOR_STRONG = 0.85


def _cut_at_a_wrapped_separator(window, renderer, suffix: str, separator: str, dy: int):
    """Mask a line whose wording wrapped onto a row this window cannot see.

    Returns (window, cut) like `mask_after_name`, and (window, None) unless BOTH hold:

      * the separator is unmistakable — SEPARATOR_STRONG, not the ordinary bar. A 「×」 shape
        appears inside plenty of glyphs, and cutting on a weak one lands in the middle of a
        name, which reads nothing at all;
      * the wording's FIRST CHARACTER follows it within a few digits' width. That is the
        wrap's signature: the line really does continue, it simply continues below. A name
        that merely contains an ×-like stroke has no 「を」 sitting a digit later.

    Measured on the frame this was written for — 「…のガラクタ×3を」 with 「手に入れた!!」 on the
    row below: separator 0.907, 「を」 0.918, and the true name 0.6687 -> 0.7026 once the tail
    is off the comparison.
    """
    np = _np()
    sep = _hcrop(renderer.render(separator)) if separator else None
    if sep is None or sep.shape[1] >= window.shape[1]:
        return window, None
    got, sep_x = _slide(window, sep, MIN_NAME_PX, window.shape[1] - sep.shape[1], dy)
    if sep_x is None or got < SEPARATOR_STRONG or sep_x < MIN_NAME_PX:
        return window, None
    head = list(_tail_prefixes(suffix))
    first = head[-1] if head else ""
    shape = _hcrop(renderer.render(first)) if first else None
    if shape is None or shape.shape[1] >= window.shape[1]:
        return window, None
    digits = [s for _d, s in _digit_shapes(renderer) if s is not None]
    reach = sep.shape[1] + MAX_DIGITS * (max((s.shape[1] for s in digits), default=0) + 4) + 8
    seen, _at = _slide(window, shape, sep_x, sep_x + reach, dy)
    if seen < SEPARATOR_STRONG:
        return window, None
    masked = np.asarray(window, dtype=float).copy()
    masked[:, sep_x:] = 0.0
    return masked, sep_x


def mask_after_name(window, renderer, suffix: str, separator: str = "×",
                    dy: int = 0, min_score: float = SEPARATOR_MIN_SCORE):
    """Blank everything in the observation that is not the item's NAME.

    WHY THE OBSERVATION IS CUT DOWN RATHER THAN THE TEMPLATES BUILT UP
    -----------------------------------------------------------------
    A candidate is rendered as `prefix + name`, and correlated against a window holding the
    whole line. Whatever the line says after the name — 「×3」, and the template's own words —
    is ink no candidate covers, and it drags every score down by the share of the line it
    occupies. That share is small in a locale that puts the name last (zh_tw: 「獲得了{0}！！」,
    two characters of tail) and it is HALF THE LINE in one that puts it first (ja:
    「{0}を手に入れた!!」). Measured on the same Japanese frame, same vocabulary:

        as-is    ランペール金貨  0.543, margin 0.146   <- under the 0.60 gate: nothing recorded
        masked   ランペール金貨  0.793, margin 0.278

    Rendering the tail into every candidate instead cannot work: the quantity sits BETWEEN the
    name and the tail, so the tail's position depends on a number that is not known yet, and a
    template carrying it in the wrong place scored no better than one without it (0.436).

    The tail is invariant text, so it is found ONCE per line rather than per candidate: the
    template's WORDS are located in the real pixels and everything from there on is blanked.
    A tail that cannot be found confidently leaves the window untouched — the reading is then
    exactly as good, or as bad, as it was before.

    The separator only moves that cut further left, and only when it is unmistakable. It is
    an optimisation, not the mechanism: cutting at the words alone reads every one of nine
    item names in both forms of the line, at 0.72 to 0.88. Cutting at a separator that is not
    there reads none of them, because the cut lands in the middle of the name — see
    SEPARATOR_STRONG for what that cost and why the bar is where it is.

    Returns (window, cut). `cut` is None when nothing was masked.
    """
    np = _np()
    if not suffix.strip():
        return window, None
    tail = _hcrop(renderer.render(suffix))
    if tail is None or tail.shape[1] >= window.shape[1]:
        return window, None
    got, at = _slide(window, tail, 0, window.shape[1] - tail.shape[1], dy)
    if at is None or got < min_score or at < MIN_NAME_PX:
        # THE WORDING MAY BE ON THE NEXT ROW. The game wraps rather than clips, and this
        # window is one row, so on a long line most of 「を手に入れた!!」 is somewhere the
        # client cannot see: 「北穿の幽霊城の常なる冥刻のガラクタ×3を」 leaves ONE character of
        # it. Measured on that frame — the full wording scores 0.390 here and the 「を」 alone
        # 0.918 — and with nothing masked the 「×3を」 that no candidate covers drags the true
        # name from 0.86 to 0.67, under every gate. The line was in the recording and the
        # chest was recorded one item short.
        #
        # So the separator is allowed to carry the cut on its own, but only on the evidence
        # that this IS a wrapped line rather than a stroke inside a name: the 「×」 must be
        # unmistakable AND the wording's first character must follow it within a few digits'
        # width — which is exactly where the quantity reader already looks for it.
        return _cut_at_a_wrapped_separator(window, renderer, suffix, separator, dy)

    cut = at
    sep = _hcrop(renderer.render(separator)) if separator else None
    if sep is not None and sep.shape[1] < cut:
        # The number belongs to the tail: it is read separately, by a reader that anchors on
        # this same separator, and leaving it in costs a little of what the words cost.
        #
        # Searched only where a real one CAN be — a 「×」 is followed by the number and then
        # immediately by the words, so it sits within a few digits' width of them. That is
        # most of what stops a stroke in the middle of a name being mistaken for it; the
        # score bar is the rest.
        digits = [s for _d, s in _digit_shapes(renderer) if s is not None]
        reach = sep.shape[1] + MAX_DIGITS * (max((s.shape[1] for s in digits), default=0) + 4) + 8
        got, sep_x = _slide(window, sep, max(0, cut - reach), cut - sep.shape[1], dy)
        if sep_x is not None and got >= SEPARATOR_STRONG and sep_x >= MIN_NAME_PX:
            cut = sep_x

    masked = np.asarray(window, dtype=float).copy()
    masked[:, cut:] = 0.0
    return masked, cut


def _walk_out(window, start: int, step: int, level: float = INK_LEVEL) -> int:
    """From an anchor's edge, cross any ink of that anchor still ahead, then the gap after it.

    Both halves matter, and their order is the whole trick. Crossing ink FIRST absorbs the
    difference between the anchor as rendered and as drawn on screen — a substitute font's
    「×」 is a couple of pixels wider, and a span that starts inside it reads its right stroke
    as a leading digit. Skipping the gap SECOND lands on the digits.

    Never start this walk in the MIDDLE of an anchor: the suffix 「！！」 is two glyphs with a
    gap between them, so a walk from its centre crosses nothing and the span keeps the first
    「！」 — which read every quantity as a two-digit number.
    """
    x = start
    while 0 <= x < window.shape[1] and _lit(window, x, level):
        x += step
    while 0 <= x < window.shape[1] and not _lit(window, x, level):
        x += step
    return x


def _digit_shapes(renderer):
    """The ten digits, rendered once per renderer.

    Cached on the renderer because that is what they vary with — a new size or spacing is a
    new renderer. Rendering per call put 160ms on every read that succeeded.
    """
    shapes = getattr(renderer, "_qty_digits", None)
    if shapes is None:
        shapes = renderer._qty_digits = [
            (d, _hcrop(renderer.render(str(d)))) for d in range(10)
        ]
    return shapes


def _segments(window, left: int, right: int, min_width: int,
              level: float = INK_LEVEL) -> list[tuple[int, int]]:
    """The runs of ink between `left` and `right` — one per digit.

    Digits are drawn with a gap: measured at the fitted sizes, 「600」 is three 12px runs
    separated by 2px in the game's font and three 10px runs separated by 4px in PMingLiU.
    Reading them one at a time is what lets a quantity be any size — enumerating whole
    numbers instead capped this at 99, and a chest that paid 600 Gil was read as unknown.

    A run far narrower than the narrowest digit is not a digit, so it is folded into its
    neighbour. At 1920x1080 the arc tip of a 「3」 detaches into its own one-pixel column
    (`#.######`), and treating that as a separate glyph turned a readable 3 into unknown.

    FAR narrower, not merely narrower — HALF the narrowest digit. The narrowest digit IS the
    「1」, so a bar that rounds one column thinner than the atlas draws it was folded into the
    digit beside it: 「×10」 came out as a single 23px run, no single digit is that wide, and
    a ten-item chest was recorded as `qty_unknown`. A detached arc tip is one or two columns;
    a real 「1」 is within a column of its rendered width. Half is the gap between them.
    """
    # Half the narrowest digit, but never under two columns — a one-column run is antialiasing
    # in any font.
    narrow = max(2, min_width // 2)
    runs, start = [], None
    for x in range(left, right):
        if _lit(window, x, level):
            start = x if start is None else start
        elif start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, right))

    merged: list[tuple[int, int]] = []
    for run in runs:
        if merged and (run[1] - run[0] < narrow or merged[-1][1] - merged[-1][0] < narrow):
            merged[-1] = (merged[-1][0], run[1])
        else:
            merged.append(run)
    return merged


def _fitted_aspect(shape, like):
    """`shape` scaled by HEIGHT alone and placed in `like`'s box — its width kept.

    A DIGIT'S WIDTH IS PART OF ITS SHAPE, and `_fitted` throws it away: it resizes both axes
    onto the observed box, so a 「1」 stretched to the width of a 「4」 is compared as if it had
    always been that wide. At 18px (1600x900) that is enough to win — a real 「4」 8px wide was
    read as 「1」 at 0.4886 against the 4's own 0.3847, and 「×14」 was recorded as ×11.

    Keeping the aspect makes the same comparison 0.7868 for the 4 against 0.1186 for the 1.
    It is tried FIRST for that reason; `_fitted` remains as the fallback, because a substitute
    face genuinely draws narrower digits (its 「1」 is 3px where the game's is 6px) and
    stretching is the only way to read one of those at all.
    """
    from PIL import Image

    np = _np()
    box = ink_bbox(shape, min_column_ink=DIGIT_MIN_COLUMN_INK)
    if box is None:
        return None
    cut = shape[box[1]:box[3], box[0]:box[2]]
    height, width = like.shape
    natural = max(1, int(round(cut.shape[1] * (height / cut.shape[0]))))

    def placed(target: int):
        img = np.asarray(
            Image.fromarray(cut.astype("uint8")).resize((target, height), Image.LANCZOS),
            dtype=float)
        if target == width:
            return img
        out = np.zeros((height, width), dtype=float)
        if target < width:                   # centred, as a narrower glyph sits in its cell
            start = (width - target) // 2
            out[:, start:start + target] = img
        else:
            start = (target - width) // 2
            out[:] = img[:, start:start + width]
        return out

    # A PIXEL EITHER WAY, because that is what a screenshot does to ink. Blur widens a glyph
    # by about a pixel, and at 18px a 「3」 that arrives 9px wide then matches the 「8」 (9px
    # rendered) EXACTLY while its own 8px rendering sits a pixel short — 0.6774 against
    # 0.4391, a confident 18 for a line that says 13. With the slack the true digit is
    # measured at the width it actually has, and 13, 23, 14, 600 and 1000 all read at every
    # fitted geometry there is.
    best = None
    for target in range(max(1, natural - DIGIT_WIDTH_SLACK), natural + DIGIT_WIDTH_SLACK + 1):
        candidate = placed(target)
        score = zncc(like, candidate)
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1]


def _fitted(shape, like):
    """`shape` resized onto `like`'s box, so a font that draws digits a little smaller is not
    punished for the size that was fitted for the NAME."""
    from PIL import Image

    np = _np()
    box = ink_bbox(shape, min_column_ink=DIGIT_MIN_COLUMN_INK)
    if box is None:
        return None
    cut = shape[box[1]:box[3], box[0]:box[2]]
    img = Image.fromarray(cut.astype("uint8")).resize(
        (like.shape[1], like.shape[0]), Image.LANCZOS)
    return np.asarray(img, dtype=float)


def _tail_prefixes(template_after: str):
    """The template's trailing words, then every shorter prefix of them, longest first.

    A whole tail is the strongest anchor and is tried first. What follows is for a line the
    game has WRAPPED, where only the first character or two of it are on this row.
    """
    text = template_after.strip()
    for length in range(len(text), 0, -1):
        yield text[:length]


def recognize_quantity(
    window,
    renderer: "GlyphRenderer",
    prefix: str,
    name: str,
    template_after: str,
    separator: str = "\u00d7",
    offset: tuple[int, int] = (0, 0),
    max_quantity: int = MAX_QUANTITY,
) -> tuple[int | None, float]:
    """Read the quantity for an already-identified name.

    Two stages rather than one: identifying the name from `prefix + name` is
    quantity-independent, and only once the name is known is it possible to say where the
    digits begin. Returns (quantity, margin); a None quantity means the digits could not be
    read confidently, which must NOT be silently turned into 1.

    The comparison is LOCAL — the 「×」 and the suffix are located in the real pixels and only
    the ink between them is matched. Rendering the whole candidate line instead, as this did
    until 2026-08-10, makes total line WIDTH the deciding evidence, so a font whose advances
    differ by a couple of pixels per glyph buys the mismatch back with a wrong number: with
    PMingLiU every ×2 read as ×62, because at 286px of real ink the ×62 candidate landed
    1px out and the correct ×2 landed 13px short. Anchoring locally means the name no longer
    votes on the number — measured as the same failures for every name, whatever its length.
    """
    dy = offset[1]

    head = ink_bbox(renderer.render(prefix + name))
    sep_shape = _hcrop(renderer.render(separator))
    if head is None or sep_shape is None or head[2] >= window.shape[1]:
        return None, 0.0
    got, sep_x = _slide(
        window, sep_shape, head[2] - QTY_SEARCH_LEFT, head[2] + QTY_SEARCH_RIGHT, dy)
    if sep_x is None or got < SEPARATOR_MIN_SCORE:
        # No separator on screen: the line shows no number at all. Equipment and boosted
        # lines are legitimately like this, and inventing one for them is the worst failure
        # available here — it enters the study as a measurement.
        return None, 0.0

    glyphs = [(d, s) for d, s in _digit_shapes(renderer) if s is not None]
    if not glyphs:
        return None, 0.0                # the font cannot draw digits: nothing to match
    # How far the digits could possibly run, from the font's own widest digit rather than a
    # fixed number of pixels. Measured the hard way: a 60px suffix search and a 40px ink scan
    # fit three digits and CUT THE FOURTH, so 「×1000」 came back as 100 — a wrong number,
    # under the cap, indistinguishable from a real one. Anything bounding the span has to be
    # derived from how wide MAX_DIGITS digits actually are.
    span_limit = MAX_DIGITS * (max(s.shape[1] for _, s in glyphs) + 4) + 8
    # Decided BEFORE the walks, because the walks are what clipped a dim glyph: they stop on
    # the first column over the threshold, so too high a threshold silently shortens the span
    # and a narrower digit wins on width.
    level = _digit_ink_level(window, sep_x, sep_x + sep_shape.shape[1])

    left = _walk_out(window, sep_x + sep_shape.shape[1], +1, level)
    right = None
    # The words after the number, then SHORTER AND SHORTER PREFIXES of them.
    #
    # The game wraps a long message rather than clipping it, so on a wrapped line most of
    # the tail is on a second row that this window does not cover — 「…×3を」 and then 「手に
    # 入れた!!」 below. Looking for the whole tail then finds nothing (measured: 0.27), the
    # digits lose their right-hand bound, and the fallback below takes the 「を」 as part of
    # the number and gives up. Its first character alone scores 0.82 in the same place.
    #
    # Safe here in a way it would not be for the name mask: this search runs in the narrow
    # span just past the separator, not across the whole line, so a short needle has few
    # places to match by accident.
    for needle in _tail_prefixes(template_after):
        shape = _hcrop(renderer.render(needle))
        if shape is None or shape.shape[1] >= window.shape[1]:
            continue
        got, suf_x = _slide(window, shape, left, left + span_limit, dy)
        if suf_x is not None and got >= SEPARATOR_MIN_SCORE:
            right = _walk_out(window, suf_x, -1, level) + 1
            break
    if right is None or right <= left:
        # No suffix to lean on (a locale whose format ends at the number): take the ink that
        # is actually there instead.
        right = left
        for x in range(left, min(window.shape[1], left + span_limit)):
            if _lit(window, x, level):
                right = x + 1
    if right <= left:
        return None, 0.0
    runs = _segments(window, left, right, min(s.shape[1] for _, s in glyphs), level)
    if not runs or len(runs) > MAX_DIGITS:
        return None, 0.0                # nothing legible after the separator, or not a number

    # SHAPE FIRST, STRETCH SECOND. `_fitted_aspect` keeps the candidate's width, which is
    # part of what a digit IS; `_fitted` resizes both axes and lets a 「1」 stand in for a
    # 「4」. Reading the same span both ways and preferring the first that is unambiguous
    # keeps the game's own font sharp — 0.7868 against 0.1186 where stretching gave 0.6327
    # against 0.4213 — without losing a substitute face, whose digits really are narrower
    # and can only be read stretched.
    digits, worst = [], 1.0
    for fit in (_fitted_aspect, _fitted):
        digits, worst = _read_digits(window, runs, glyphs, level, fit)
        if digits:
            break
    if not digits:
        return None, worst

    if digits[0] == 0:
        return None, worst              # no quantity is written with a leading zero
    quantity = int("".join(str(d) for d in digits))
    if quantity > max_quantity:
        return None, worst              # implausible for one line: report it as unread
    return quantity, worst


def _read_digits(window, runs, glyphs, level, fit):
    """One digit per run, or ([], margin) when any of them is unreadable or ambiguous.

    `fit` is how a candidate is brought onto the observed box — see `_fitted_aspect`.
    """
    digits, worst = [], 1.0
    for x0, x1 in runs:
        box = ink_bbox(window[:, x0:x1], level=level, min_column_ink=DIGIT_MIN_COLUMN_INK)
        if box is None:
            return [], 0.0
        seen = window[:, x0:x1][box[1]:box[3], box[0]:box[2]]
        scored: list[tuple[float, int]] = []
        for d, shape in glyphs:
            if abs(shape.shape[1] - seen.shape[1]) > max(3, DIGIT_WIDTH_TOLERANCE * seen.shape[1]):
                # No single digit is this wide: two of them ran together, or this is not a
                # digit at all. Unknown — guessing here is how a 10px span became ×47.
                continue
            candidate = fit(shape, seen)
            if candidate is not None:
                scored.append((zncc(seen, candidate), d))
        if not scored:
            return [], 0.0
        scored.sort(reverse=True)
        margin = scored[0][0] - (scored[1][0] if len(scored) > 1 else 0.0)
        if margin < QTY_MIN_MARGIN:
            return [], margin           # legible but ambiguous: unknown, never a guess
        digits.append(scored[0][1])
        worst = min(worst, margin)
    return digits, worst


def required_window(renderer: "GlyphRenderer", prefix: str, names: list[str],
                    tail_sample: str | None = None, slack: int | None = None) -> tuple[int, int]:
    """Window size that fits the LONGEST candidate plus its tail, at this font size.

    Sized from the data rather than guessed: at 26px the longest zh_tw entry renders 662px
    wide, so a 380px window would drop 18% of the vocabulary without any error being raised.

    The tail is sized from MAX_QUANTITY, not from a hard-coded 「×99！！」. The quantity is read
    out of THIS window, so a window a digit too narrow clips the 「！！」 the reader anchors
    on — and a clipped anchor is reported as `qty_unknown`, silently, on exactly the longest
    names. Raising the cap without widening the window would have done that.
    """
    widest = max((renderer.ink_width(prefix + n) for n in names), default=0)
    # ink_width can only measure what fits on the renderer it is given. A probe narrower than
    # the longest name returns its own canvas width and the result looks plausible, so the
    # window comes out short and the longest names lose their tail — silently, as
    # `qty_unknown`. Callers pass a deliberately huge probe; this catches the day one does not.
    if widest >= renderer.window[0] - 2:
        raise ValueError(
            f"required_window: the probe renderer is only {renderer.window[0]}px wide, and a "
            f"candidate already fills it. Measure with a wider canvas — the result would be "
            f"short by however much was cut off."
        )
    tail = renderer.ink_width(tail_sample or f"\u00d7{MAX_QUANTITY}!!")
    # The two halves are measured SEPARATELY, so their sum is short by the bearings and
    # advances between them: measured, a line whose parts summed to 452px rendered to 476.
    # The old fixed 24px slack was exactly that shortfall, which put the longest names hard
    # against the canvas edge — and a clipped 「！！」 is reported as `qty_unknown` rather than
    # as an error. Scaled with the size, since that is what the shortfall scales with.
    height = int(renderer.size * 1.8) + 6
    return widest + tail + (24 + renderer.size if slack is None else slack), height


# How much two rendered candidates must differ in a column for it to count as discriminating.
DIFF_LEVEL = 24
# Columns either side of a differing one, so a stroke that spills past the glyph box counts.
DIFF_PAD = 2


def discriminating_columns(a, b, level: int = DIFF_LEVEL, pad: int = DIFF_PAD):
    """Column indices where two rendered lines actually differ."""
    np = _np()
    diff = np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)).max(axis=0)
    cols = np.where(diff > level)[0]
    if len(cols) == 0:
        return cols
    lo, hi = max(0, cols.min() - pad), min(diff.shape[0], cols.max() + 1 + pad)
    return np.arange(lo, hi)


def break_tie(window, renderer, prefix: str, top: str, runner: str,
              shifts: tuple[range, range] | None = None) -> tuple[str, float]:
    """Re-score two near-identical candidates over ONLY the part that differs.

    Item names come in graded families — 下級/中級/上級/特級鐵礦石 — that differ by a single
    character in an otherwise identical line. Whole-line correlation is dominated by the
    identical remainder, so the true answer and its rival land within ~0.02 of each other and
    the ambiguity gate correctly refuses both. Measured on a real mining panel: 下級鐵礦石
    0.8624 against 上級鐵礦石 at margin +0.0213, under a 0.03 gate.

    The quantity reader already had this problem and solved it the same way: "the digit
    occupies a small slice of a long line, so a whole-line comparison is dominated by the
    identical prefix". Isolating the columns where the two renderings differ turns a
    hair's-breadth margin into a decisive one, WITHOUT lowering any threshold — which would
    have admitted genuinely ambiguous readings everywhere else.

    Returns (winner, margin over the discriminating columns, the winner's own score there).

    THAT THIRD VALUE IS NOT DECORATION. The margin says which of the two fits better; it does
    NOT say that either fits at all, and over a few dozen columns two bad renderings can be
    0.11 apart while both are noise. Measured on real panels:

        下級鐵礦石 vs 上級鐵礦石     winner 0.77-0.91, loser 0.37-0.59   correct
        精煉石（攻擊力+4～6）        winner 0.2173,    loser 0.1096      WRONG, and it won

    In that last case the whole-line pass had the right answer and this overturned it on a
    comparison of two things that both looked like nothing. The caller gates on the score.

    ALIGNMENT IS SEARCHED HERE TOO, over the same shifts the whole-line pass used. It was not,
    and that made the score it is gated on meaningless: a whole line absorbs a pixel of
    misalignment almost for free, while a comparison of thirty columns around one character
    does not. Measured on a real Japanese panel — the same row, the same renderer:

        fixed at (0,0)      上級鉄鉱石 fits 0.1431   refused, and the row went unread
        searched +-2        上級鉄鉱石 fits 0.8579   read

    The winner was right in both; only the evidence for it was destroyed.
    """
    np = _np()
    a = renderer.render(prefix + top)
    b = renderer.render(prefix + runner)
    cols = discriminating_columns(a, b)
    if len(cols) == 0:
        # Two candidates that render identically: genuinely indistinguishable, so say so
        # rather than picking by iteration order.
        return top, 0.0, 0.0
    dxs, dys = shifts or (range(*DX_RANGE), range(*DY_RANGE))
    window = np.asarray(window, dtype=float)
    views = [np.roll(np.roll(window, -dy, 0), -dx, 1)[:, cols] for dy in dys for dx in dxs]
    score_top = max(zncc(obs, a[:, cols]) for obs in views)
    score_runner = max(zncc(obs, b[:, cols]) for obs in views)
    if score_runner > score_top:
        return runner, score_runner - score_top, score_runner
    return top, score_top - score_runner, score_top
