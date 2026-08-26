"""Build a glyph atlas — a sheet of rendered characters, plus each one's advance.

WHY THE CLIENT CARRIES THIS
---------------------------
The recogniser works by rendering candidate names and comparing pixels, so it needs the same
typeface the game draws with. That typeface is licensed, and the client must not distribute
it — so the client BUILDS the atlas instead, on the player's machine, from a font that is
already on it.

Nothing here knows anything about the game. It takes a font file and a vocabulary and writes
a sheet: exactly what any program that wanted to compare rendered text would do. Where the
font comes from is the caller's problem, which is what keeps this half of the work ordinary.

WHY AN ATLAS AND NOT THE FONT
-----------------------------
It stores a bitmap per character at one reference size plus each character's advance width,
and names are composed from those. Measured against a real screen, that matches BETTER than
rendering the font directly at the fitted size:

    native TTF at the fitted size      0.8889
    fixed 128px atlas -> scaled        0.9085

Supersampling and downscaling approximates the game's own anti-aliasing more closely than
rendering straight at the target size. The reference size is FIXED rather than per-size,
because each player's fitted size differs with their resolution — an atlas built for one
size would be useless to anyone else.

ONE FONT IS RARELY ENOUGH
-------------------------
No single face covers every character a locale's item names use, and drawing .notdef where
the screen shows a glyph is a template that can never match. Each character is therefore
taken from the first font in the chain that actually HAS it, and the advance comes from the
font that DREW it — taking it from the primary would place a fallback glyph correctly and
then advance by the width of a box, shifting every later glyph in the name.

Characters no font in the chain can draw are kept, drawn as whatever the primary does with
them, and REPORTED: silently dropping one shifts everything after it.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

NOTDEF_PROBE = "￿"
REFERENCE_SIZE = 128
# Cell padding, so a glyph that overhangs its advance is not clipped by its neighbour.
CELL_PAD = 8


def charset_for(vocab: dict) -> set[str]:
    """Every character the client could ever need to render for this locale."""
    chars: set[str] = set("0123456789")
    for entry in vocab.get("items", []) + vocab.get("equipment", []):
        if entry.get("name"):
            chars |= set(entry["name"])
    for template in (vocab.get("templates") or {}).values():
        if template:
            chars |= set(template)
    # Template punctuation survives NFKC folding into these forms, which is what the parser
    # and the renderer actually see.
    chars |= set("×x!！:：（）()[]、。， \u3000")
    # Whitespace is KEPT. A space renders blank but still has an advance, and a name
    # containing one would otherwise fall back to a guessed advance and shift every glyph
    # after it. Only control characters are dropped.
    return {c for c in chars if c.isprintable() or c.isspace()}


def uncovered(vocab: dict, atlas_path) -> set[str]:
    """Characters this vocabulary needs that the atlas on disk cannot draw.

    WHY THIS IS A SUBSET TEST AND NOT AN EQUALITY ONE. An atlas carrying MORE than the
    vocabulary asks for is not stale — it is what a sheet built from a wider table looks
    like, and rebuilding it would cost a player the one thing a rebuild needs and may not
    have: the game installed. Only what is MISSING can make a name unmatchable.

    And a missing character is the quiet failure, not a loud one: `AtlasRenderer` records it
    and draws the name with a hole, which scores like a misread and is refused on margin. The
    line is then simply absent from the record, and the record looks like a chest that held
    less. So this is asked before anything is read, rather than noticed afterwards.

    `charset_for` is the same function `build` composes the sheet from, so the question asked
    here and the answer written there cannot drift apart.

    Returns an empty set when the atlas cannot be read at all — a missing or corrupt sheet is
    already handled by the caller as "no atlas", and reporting it as a coverage problem would
    send it down a path that assumes there is something to compare.
    """
    import json

    try:
        index = json.loads(Path(atlas_path).read_text(encoding="utf-8"))["index"]
    except (OSError, ValueError, KeyError):
        return set()
    return {c for c in charset_for(vocab) if c not in index}


class FontSet:
    """A primary font plus the fallbacks the game itself must be using.

    `for_char` answers with the first font that can actually draw the character, so the
    atlas never stores a .notdef box for something the screen shows as a glyph.
    """

    def __init__(self, primary: Path, fallbacks: list[Path], size: int):
        from PIL import ImageFont

        # Coerced, because a caller passing strings is ordinary and both the de-duplication
        # below and the `.name` recorded in the metadata are Path behaviour: str != Path is
        # always true, so a fallback that IS the primary would be rendered twice and only
        # then fail on `.name`.
        primary = Path(primary)
        fallbacks = [Path(f) for f in fallbacks]
        self.paths = [primary] + [f for f in fallbacks if f != primary]
        self.fonts = [ImageFont.truetype(str(p), size) for p in self.paths]
        self._notdef = [self._raster(f, NOTDEF_PROBE) for f in self.fonts]
        self.fallback_used: dict[str, str] = {}
        self.unresolved: list[str] = []

    @staticmethod
    def _raster(font, ch: str) -> bytes:
        from PIL import Image, ImageDraw

        size = font.size
        img = Image.new("L", (size * 2, size * 2), 0)
        ImageDraw.Draw(img).text((size // 2, size // 2), ch, font=font, fill=255)
        return img.tobytes()

    def for_char(self, ch: str):
        """The font to draw `ch` with, and whether a fallback was needed."""
        for i, font in enumerate(self.fonts):
            # Whitespace has no ink to compare, and every font can advance it.
            if ch.isspace() or self._raster(font, ch) != self._notdef[i]:
                if i:
                    self.fallback_used[ch] = self.paths[i].name
                return font
        # Kept, drawn as whatever the primary font does with it, and REPORTED — a silently
        # dropped character would shift every glyph after it in that name.
        self.unresolved.append(ch)
        return self.fonts[0]


def build(font_path: Path, vocab: dict, out_dir: Path, locale: str,
          reference: int = REFERENCE_SIZE, fallbacks: list[Path] | None = None,
          stem: str | None = None) -> dict:
    from PIL import Image, ImageDraw

    chars = sorted(charset_for(vocab))
    fonts = FontSet(Path(font_path), list(fallbacks or []), reference)

    cell = reference + CELL_PAD * 2
    cols = int(math.ceil(math.sqrt(len(chars))))
    rows = int(math.ceil(len(chars) / cols))
    sheet = Image.new("L", (cols * cell, rows * cell), 0)
    draw = ImageDraw.Draw(sheet)

    index: dict[str, dict] = {}
    for i, ch in enumerate(chars):
        cx, cy = (i % cols) * cell, (i // cols) * cell
        font = fonts.for_char(ch)
        draw.text((cx + CELL_PAD, cy + CELL_PAD), ch, font=font, fill=255)
        # The advance comes from the font that DREW the glyph. Taking it from the primary
        # font instead would place a fallback glyph correctly and then advance by the width
        # of a box, so every later glyph in the name would sit wrong.
        index[ch] = {"x": cx, "y": cy, "advance": round(font.getlength(ch), 3)}

    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"atlas.{stem or locale}.png"
    meta = out_dir / f"atlas.{stem or locale}.json"
    # UNHIDE BEFORE WRITING. These are marked hidden at the end of this function, and Windows
    # will not open an existing hidden file for writing — so without this, the atlas could be
    # built exactly once and every rebuild after it failed with a permission error naming a
    # file the client itself had marked. See config.unhide.
    from .config import unhide

    for target in (png, meta):
        unhide(target)
    sheet.save(png, optimize=True)
    meta.write_text(
        json.dumps(
            {
                "locale": locale,
                "reference_size": reference,
                "cell": cell,
                "pad": CELL_PAD,
                # Recorded so a mismatch between atlas and vocabulary is detectable rather
                # than showing up as unexplained recognition failures.
                "glyphs": len(chars),
                # Which faces this atlas was composed from, and which characters each
                # fallback supplied. A recognition failure confined to one script is then
                # traceable to the font that drew it rather than guessed at.
                "fonts": [p.name for p in fonts.paths],
                "fallback_glyphs": fonts.fallback_used,
                "unresolved": fonts.unresolved,
                "index": index,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # Marked hidden. This is the game's licensed typeface rasterised, and it sits in the
    # folder a player opens to find their own records — it is not theirs to hand on, and it
    # is not what they came to that folder for. Hidden only: every reader still opens it,
    # and a failure to mark it is not a reason to fail the build. See config.hide.
    from .config import hide

    for made in (png, meta):
        hide(made)
    return {"png": png, "meta": meta, "glyphs": len(chars),
            "sheet": (sheet.width, sheet.height),
            "fallbacks": len(fonts.fallback_used),
            "unresolved": fonts.unresolved,
            "bytes": png.stat().st_size + meta.stat().st_size}
