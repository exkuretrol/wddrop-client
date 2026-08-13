"""
STANDARD MODE — read the chest result off the game's own message line.

THE UI CONTRACT (derived from screen recordings, not assumed)
-------------------------------------------------------------
After a chest opens, the game does NOT show a reward panel with rows. It prints ONE
dialogue-style message line per item, at the bottom of the screen, each advanced by the
player (▼ indicator) or by AUTO:

    獲得了初始的冥刻雜物 × 3！！
    獲得了初始的扭曲一縷重武器雜物 × 2！！
    獲得了蒼藍礦石 × 3！！

Verified end-to-end: 蒼藍礦石 resolves to the same item id that the game itself reports for
that drop, so what is read off the screen and what the game recorded agree.

THE FORMAT IS NEVER HARDCODED
-----------------------------
The line is composed from the game's own localised templates, which `tools/build_vocab.py`
extracts into each vocab file, and `MessageFormat` compiles into regexes:

    DungeonTreasure@DropItem    the wrapper   zh_tw '<color=#E2CCB2>獲得了{0}！！</color>'
    Common@NameAndQuantity      the {0} body  zh_tw '{0}×{1}'
    DungeonTreasure@DropEmpty   empty chest   zh_tw 'Msg@<color=...>但是裡面什麼都沒有……</color>'

These are STRUCTURALLY different per locale — ja puts the name first ('{0}を手に入れた!!'),
en and de terminate with a single '!' — so a regex written for zh_tw would match nothing
elsewhere and look like "that locale never opens chests" rather than like a bug.

An EMPTY chest is a real observation and the worst outcome, so it is parsed and recorded
rather than ignored; discarding empties would delete the bottom of the distribution.

A LINE MAY CARRY NO QUANTITY, FOR TWO DIFFERENT REASONS
-------------------------------------------------------
Confirmed in DungeonTreasureController.ObtainItemInfo..ctor, {0} is filled two ways:
    no boost  -> ContentModel.NameAndQuantity  ("name × qty")
    boosted   -> ContentModel.Name             (name only, NO quantity)

AND equipment drops are also rendered without a quantity — verified on a real chest:
「獲得了雪兇鳥羽冠！！」 (equipment `identification` 400101200). Equipment is always a single
piece, so there is no quantity to show.

So `quantity is None` is ambiguous on its own. `resolve_line()` below disambiguates using
the vocabulary: a name that resolves to an EQUIPMENT family is one piece (quantity 1); a
name that resolves to an ITEM was a boosted line and its quantity is genuinely unknown, so
it stays None rather than being fabricated as 1.

TWO CONSEQUENCES THAT DRIVE THE WHOLE DESIGN
--------------------------------------------
1. **Items arrive sequentially, not as a set.** One message at a time. A missed message is
   a silently missing item, so the reader must follow the sequence until the dialogue
   closes and must never assume it has seen the whole chest from one frame.

2. **The text is typewriter-animated, and reading it early is actively dangerous.** A
   mid-animation frame shows a truncated name — the recordings contain 「獲得了蒼藍」 en
   route to 「獲得了蒼藍礦石 × 3！！」. This is not a low-confidence miss that the threshold
   would catch: in this game's zh_tw item table, **191 item names have a truncated prefix
   that is itself a different, valid item name** (e.g. `HP持續提升秘笈的一節` truncates to
   the real item `HP持續提升秘笈`). An early read therefore produces a *confident* match to
   the *wrong* item, which is exactly the kind of silent corruption this study cannot
   tolerate.

   The mitigation is a hard requirement, not a tuning knob:
     * accept a line only once it is TERMINATED (matches the locale template's full
       pattern, including its trailing punctuation), and
     * accept it only once it has been STABLE across consecutive sampled frames.
   `MessageLineReader` below enforces both.

Why OCR is the default despite this: it only reads pixels the player is already shown. It
does not modify the game, inject code, or alter network traffic, so it carries materially
lower ban risk (DISCLAIMER.md §2), and needs no router-level proxy access.

Why it is tractable: the answer space is CLOSED — every name is one of ~2,325 known items
or 823 equipment families per locale (tools/build_vocab.py). Recognition is nearest-match
against a fixed vocabulary, not open-ended text recognition.

STATUS: the message parser, stability gate and vocabulary matcher below are implemented and
tested. Frame acquisition and the on-screen text region are NOT — they need per-resolution
calibration against reference recordings (docs/PLAN.md phase 4).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

# Below this score the read is recorded as unresolved WITH its raw_text rather than being
# forced onto a best guess. Forcing a match would quietly bias the sample toward whichever
# names OCR finds easy — exactly the kind of bias this study exists to measure.
#
# MEASURED, and the reason icon matching must corroborate identity: CJK names are short, so
# SequenceMatcher is coarse. On a 4-character name one wrong character scores 0.75 — e.g.
# '青銅短劍' misread as '青銅短剑' (simplified 剑) or '靑銅短劍' (variant 靑) both land at
# 0.750 and are rejected here. Lowering the threshold to catch them would also start
# accepting genuinely different 4-character names.
MIN_MATCH_CONFIDENCE = 0.82

# A line is only trusted after being identical across this many consecutive samples, which
# is what stops a typewriter frame from being read as a complete name.
STABLE_FRAMES_REQUIRED = 2

# Rich-text and routing decorations the templates carry but the rendered line does not
# expose as literal text we can match on.
TAG_RE = re.compile(r"<[^>]+>")
MSG_PREFIX_RE = re.compile(r"^Msg@")
PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")


def _clean_template(tpl: str) -> str:
    """Strip <color=...></color> markup and the internal 'Msg@' routing prefix."""
    return TAG_RE.sub("", MSG_PREFIX_RE.sub("", tpl or "")).strip()


def _norm(s: str) -> str:
    """NFKC-normalise and collapse whitespace runs, but do NOT delete whitespace.

    NFKC maps the full-width forms the game uses (！ ×) onto ASCII (! x), which is exactly
    the drift OCR introduces, so template and observed text end up in one normal form.

    Whitespace is collapsed rather than stripped: deleting it would turn the English name
    'Blue Ore' into 'BlueOre' and hand a mangled string to raw_text. Spacing differences are
    absorbed by the compiled pattern instead (every join allows optional whitespace), which tolerates both
    the templates disagreeing between locales ('{0}×{1}' vs '{0} x{1}' vs '{0} ×{1}') and
    the renderer's own kerning.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


# Characters the game / OCR may render for the "times" separator. NFKC does NOT fold
# U+00D7 MULTIPLICATION SIGN to ASCII 'x' (it is not a compatibility character), and OCR
# readily confuses the two, so the separator is matched as a class instead of a literal.
MULT_CHARS = "\u00d7\u2715\u2716xX*"
MULT_CLASS = "[" + re.escape(MULT_CHARS) + "]"


def _lit(text: str) -> str:
    """Escape template literal text, letting any space match flexible whitespace.

    NOTE: re.escape() escapes a space as '\\ ' (it is special under re.VERBOSE), so the
    replacement must target the ESCAPED form, not a bare ' '.
    """
    return re.escape(text).replace("\\ ", r"\s*")


def _lit_sep(text: str) -> str:
    """Escape a separator, but accept any multiplication-sign variant for 'x'."""
    out = []
    for ch in text:
        out.append(MULT_CLASS if ch in MULT_CHARS else _lit(ch))
    return "".join(out)


@dataclass(frozen=True)
class ParsedLine:
    """One fully-rendered message line.

    `quantity` is None when the game rendered the name WITHOUT a quantity (the boosted
    path) — deliberately not defaulted to 1, which would fabricate data.
    `is_empty` marks the empty-chest message, a real observation with no items.
    """

    name: str | None
    quantity: int | None
    raw: str
    is_empty: bool = False


class MessageFormat:
    """Compiles a locale's drop-message patterns from the GAME'S OWN templates.

    Never hardcode the zh_tw wording: the templates are structurally different per locale.

        zh_tw  '<color=#E2CCB2>獲得了{0}！！</color>'   name in the middle, '！！'
        ja     '<color=#E2CCB2>{0}を手に入れた!!</color>'  name FIRST
        en     '<color=#E2CCB2>You received {0}!</color>'  single '!'
        ko     '<color=#E2CCB2>{0}을(를) 손에 넣었다!!</color>'
        de     '<color=#E2CCB2>{0} erhalten!</color>'       single '!'

    A regex written for one of these silently matches nothing on the others, which would
    look like "that locale just never opens chests" rather than like a bug.

    Two forms are compiled, because the game composes {0} differently depending on whether
    a drop-rate boost is active (confirmed in DungeonTreasureController.ObtainItemInfo..ctor):

        hasObtainItemUp == false  ->  {0} = ContentModel.NameAndQuantity   ("name x qty")
        hasObtainItemUp == true   ->  {0} = ContentModel.Name              (name only)

    So `quantity` is None on a boosted line rather than being wrongly assumed to be 1.
    """

    def __init__(self, drop_item: str, name_and_quantity: str, drop_empty: str | None = None):
        self.raw = {
            "drop_item": drop_item,
            "name_and_quantity": name_and_quantity,
            "drop_empty": drop_empty,
        }
        outer = _norm(_clean_template(drop_item))
        inner = _norm(_clean_template(name_and_quantity))

        if "{0}" not in outer:
            raise ValueError(f"drop_item template has no {{0}}: {drop_item!r}")

        # Inner: '{0}x{1}' after normalisation -> name group + literal separator + qty group.
        inner_pattern = self._compile_inner(inner)
        # Name-only form for the boosted path.
        name_only = r"(?P<name>.+?)"

        self.with_quantity = re.compile("^" + self._substitute(outer, inner_pattern) + "$")
        self.name_only = re.compile("^" + self._substitute(outer, name_only) + "$")
        self.empty = (
            re.compile("^" + _lit(_norm(_clean_template(drop_empty))) + "$")
            if drop_empty else None
        )

    @staticmethod
    def _compile_inner(inner: str) -> str:
        """'{0}x{1}' -> '(?P<name>.+?)x(?P<qty>\\d+)', keeping the locale's own separator."""
        m = PLACEHOLDER_RE.split(inner)
        # split yields [pre, '0', mid, '1', post] for '{0}<sep>{1}'
        if len(m) != 5 or m[1] != "0" or m[3] != "1":
            raise ValueError(f"unexpected name_and_quantity template: {inner!r}")
        pre, sep, post = _lit(m[0]), _lit_sep(m[2]), _lit(m[4])
        # \s* at every join so 'name×3', 'name × 3' and 'name x3' all parse identically.
        return rf"{pre}\s*(?P<name>.+?)\s*{sep}\s*(?P<qty>\d+)\s*{post}"

    @staticmethod
    def _substitute(outer: str, inner_pattern: str) -> str:
        """Escape the outer template's literal text, then drop the inner pattern into {0}."""
        parts = outer.split("{0}")
        if len(parts) != 2:
            raise ValueError(f"drop_item template must contain exactly one {{0}}: {outer!r}")
        return _lit(parts[0]) + r"\s*" + inner_pattern + r"\s*" + _lit(parts[1])

    @classmethod
    def from_vocab(cls, vocab_json: Path) -> "MessageFormat":
        data = json.loads(Path(vocab_json).read_text(encoding="utf-8"))
        t = data.get("templates") or {}
        return cls(t.get("drop_item"), t.get("name_and_quantity"), t.get("drop_empty"))

    # -- parsing -------------------------------------------------------------------
    def parse(self, text: str) -> "ParsedLine | None":
        """Parse one message line. Returns None if it is not a COMPLETE drop line.

        Returning None for an unterminated line is the point: the template's trailing
        punctuation is the game's own end-of-line marker, and the only proof available that
        the typewriter animation has finished.
        """
        s = _norm(text)
        if not s:
            return None
        if self.empty is not None and self.empty.match(s):
            return ParsedLine(name=None, quantity=None, raw=text, is_empty=True)
        m = self.with_quantity.match(s)
        if m:
            return ParsedLine(name=m.group("name"), quantity=int(m.group("qty")), raw=text)
        m = self.name_only.match(s)
        if m:
            # Boosted drop: the game rendered the name without a quantity, so quantity is
            # genuinely unknown. Do NOT default it to 1 — that would fabricate data.
            return ParsedLine(name=m.group("name"), quantity=None, raw=text)
        return None


class MessageLineReader:
    """Consumes sampled OCR reads of the message region and emits each COMPLETED line once.

    Guards both failure modes:
      * unterminated (typewriter still running) -> not emitted
      * repeated frames of the same line        -> emitted exactly once
    """

    def __init__(self, fmt: "MessageFormat", stable_frames: int = STABLE_FRAMES_REQUIRED):
        self.fmt = fmt
        self.stable_frames = stable_frames
        self._candidate: str | None = None
        self._count = 0
        self._last_emitted: str | None = None

    def feed(self, text: str) -> ParsedLine | None:
        s = (text or "").strip()
        if s != self._candidate:
            self._candidate, self._count = s, 1
        else:
            self._count += 1

        if self._count < self.stable_frames:
            return None
        parsed = self.fmt.parse(s)
        if parsed is None or s == self._last_emitted:
            return None
        self._last_emitted = s
        return parsed

    def reset(self, keep_last_line: bool = False) -> None:
        """Call when the dialogue closes, so an identical item from the NEXT chest is not
        suppressed as a duplicate.

        `keep_last_line` is for the case where the episode ended but the MESSAGE IS STILL ON
        SCREEN — the idle fallback, which fires 8s after the last line whether or not the
        player has dismissed it. Forgetting the line there means the very next frame reads
        the same still-displayed text as a brand new one, and the chest is recorded again,
        and again, every 8 seconds for as long as the player leaves it up. Measured: one
        chest became four in 40 seconds.
        """
        self._candidate = None
        self._count = 0
        if not keep_last_line:
            self._last_emitted = None


@dataclass(frozen=True)
class VocabEntry:
    name: str
    item_id: int | None = None
    item_type: str | None = None
    identification: int | None = None
    ids: tuple[int, ...] = ()


class Vocabulary:
    """Closed-set matcher over one locale's items AND equipment families."""

    def __init__(self, entries: list[VocabEntry], variant_map: dict[str, str] | None = None):
        self.entries = entries
        # Simplified->traditional character map derived from the game's own item names (see
        # tools/build_vocab.py). Applied to BOTH sides, so matching is symmetric and works
        # whichever script the OCR happens to emit.
        self.variant_map = variant_map or {}
        self._exact = {self._norm(e.name): e for e in entries}

    def _canon(self, s: str) -> str:
        if not self.variant_map:
            return s
        return "".join(self.variant_map.get(c, c) for c in s)

    @classmethod
    def load(cls, vocab_json: Path) -> "Vocabulary":
        data = json.loads(Path(vocab_json).read_text(encoding="utf-8"))
        variant_map = data.get("variant_map") or {}
        entries = [
            VocabEntry(name=i["name"], item_id=i.get("id"), item_type=i.get("type"))
            for i in data.get("items", [])
            if i.get("name")
        ]
        entries += [
            VocabEntry(
                name=f["name"],
                identification=f.get("identification"),
                ids=tuple(f.get("ids", [])),
            )
            for f in data.get("equipment", [])
            if f.get("name")
        ]
        return cls(entries, variant_map=variant_map)

    def _norm(self, s: str) -> str:
        # NFKC folds the full/half-width variants OCR mixes up; whitespace is dropped
        # because CJK renderers introduce inconsistent spacing; the variant map folds
        # simplified/traditional character pairs, which would otherwise cost ~0.25 of the
        # similarity score each and sink an otherwise perfect read.
        base = unicodedata.normalize("NFKC", s or "").replace(" ", "").strip().lower()
        return self._canon(base)

    def match(self, text: str) -> tuple[VocabEntry | None, float]:
        """Return (entry, confidence). 1.0 means an exact normalised hit."""
        key = self._norm(text)
        if not key:
            return None, 0.0
        hit = self._exact.get(key)
        if hit is not None:
            return hit, 1.0

        best, best_score = None, 0.0
        for entry in self.entries:
            score = SequenceMatcher(None, key, self._norm(entry.name)).ratio()
            if score > best_score:
                best, best_score = entry, score
        if best_score < MIN_MATCH_CONFIDENCE:
            return None, best_score
        return best, best_score


def resolve_line(parsed: ParsedLine, vocab: "Vocabulary") -> dict:
    """Turn a parsed line into ReceivedItem kwargs, resolved against the vocabulary.

    NOT ON THE LIVE PATH, AND IT IS WORTH KNOWING WHY THAT MATTERS. The capture runner
    resolves through `ItemIndex.identify()` instead; nothing but the tests calls this. Two
    things followed from believing otherwise: the wire schema documented a repair path
    ("unmatched OCR is transmitted with its raw_text") that only this function implements,
    and `item_type`/`match_confidence` were assumed to be arriving at the server when only
    this function set them.

    It also encodes one rule the live path does NOT have: a quantity-less line that resolves
    to EQUIPMENT is a known 1, while the same shape resolving to an item means the number was
    hidden by a boost and is genuinely unknown. `_emit` marks both unknown.

    The keys it returns are its own; `raw_text` and `item_type` left the wire on 2026-08-13
    and would be ignored by `ReceivedItem` if this were ever wired in as it stands.

    Handles the quantity ambiguity described in the module docstring, and never invents an
    identity: a low-confidence match is returned unresolved WITH its raw text, so the event
    is still transmitted and can be re-resolved server-side once the vocabulary is fixed.
    (Real case: a single-character misread of 雪兇鳥羽冠 as 雪兜鳥羽冠 scores 0.800 — below
    threshold, so it is correctly refused rather than attributed to another item.)
    """
    entry, confidence = vocab.match(parsed.name or "")
    item: dict = {
        "item_name": parsed.name or "",
        "quantity": parsed.quantity if parsed.quantity is not None else 1,
        "match_confidence": confidence,
        "raw_text": parsed.raw,
    }
    if entry is None:
        # Unresolved: keep the reading, claim nothing about what it was.
        return item

    if entry.identification is not None:
        # Equipment: exactly one piece, regardless of the missing quantity.
        item["equipment_name"] = entry.name
        item["equipment_identification"] = entry.identification
        item["quantity"] = 1
        item["item_type"] = "Equipment::Equipment"
    else:
        item["item_id"] = entry.item_id
        item["item_type"] = entry.item_type
        if parsed.quantity is None:
            # Boosted item line: the game printed no quantity, so we do not know it.
            item["quantity"] = 1
            item["qty_unknown"] = True
    item["item_name"] = entry.name
    return item


class OcrCapture:
    """CaptureBackend implementation. Frame acquisition not yet built."""

    def __init__(self, vocab: Vocabulary, fmt: MessageFormat):
        self.vocab = vocab
        self.fmt = fmt
        self.reader = MessageLineReader(fmt)

    def start(self, sink) -> None:
        raise NotImplementedError(
            "Frame acquisition and message-region calibration are not implemented yet. "
            "The parsing/stability/matching layer IS implemented and tested — what remains "
            "is grabbing the game window and locating the message band per resolution. "
            "See docs/PLAN.md phase 4."
        )

    def stop(self) -> None:
        pass


# TODO (phase 4) — remaining work, in order:
#   1. grab the game window (WVDWS already pins its size, removing most variance)
#   2. locate the message band; sample it at >= 4 fps (the typewriter completes in ~1s, so
#      slower sampling risks missing a whole line between advances)
#   3. OCR the band -> MessageLineReader.feed() -> Vocabulary.match()
#   4. detect dialogue open/close to bound one chest's content list and call reader.reset()
#   5. distinguish chest_direct from junk_reversal by the originating screen
#   6. read 品質 ★ / 等級 for equipment lines (position still unknown — needs a recording
#      containing an equipment drop; see docs/PLAN.md §5)
