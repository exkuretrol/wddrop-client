"""
Wire format shared by the client and the server.

UNIT OF ANALYSIS = one chest open, with its FULL content list.

The recording target is everything the game reports after a chest is opened — which in
practice is mostly `Item::Junk` and `Item::SaleOnly`, plus equipment when it drops. The
whole content list is kept together, because "what did this chest give me" is the
observation; splitting it into separate rows would lose the fact that they came from one
roll.

Confirmed against both sources, which agree exactly:
  * API  `received_contents: [{content_id, content_type, quantity, boost_id}]`
  * UI   one message line per item: 「獲得了<name> × <qty>！！」
    e.g. 「獲得了蒼藍礦石 × 3！！」 -> content_id 20000001, Item::SaleOnly

Two provenances stay distinguishable because they have different published rate tables
and different confounders:

    chest_direct   contents straight out of a treasure chest
    junk_reversal  equipment produced by reversing (逆轉) a junk item

DESIGN RULES
------------
1. The client never sends account identity. It sends a locally-generated `install_id`
   (random UUID4, persisted in the client config). The server HMACs that with a
   server-only salt to get `player_id`. Player name, code, gold, characters, party and
   friends are never collected in either mode. See DISCLAIMER.md.

2. The client never decides the calendar day. It sends UTC + its own tz offset; the
   server derives the JST day bucket (the game's own timezone). A client with a wrong
   clock can be detected and quarantined rather than silently poisoning a day bucket.

3. Empty chests count. `contents` may legitimately be an empty list; see the field docs.

4. Unmatched OCR is transmitted, never dropped. If `equipment_id` is None, `raw_text`
   carries what was actually read so the vocabulary can be repaired later and the event
   re-resolved server-side. Silently discarding unreadable drops would bias the sample
   toward whatever OCR happens to find easy — which is exactly the kind of bias this
   study is trying to measure.
"""
from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field, field_validator

SCHEMA_VERSION = 1


class CaptureMode(str, enum.Enum):
    """How the record was obtained.

    One value today. It is still a field rather than an assumption because it is what lets
    the analysis split on error profile — a second source with different error behaviour
    could not be mixed in without one, and there WAS a second before it was removed for the
    ban risk it carried."""

    OCR = "ocr"          # reads the game's own result screens off the framebuffer


class Provenance(str, enum.Enum):
    CHEST_DIRECT = "chest_direct"
    JUNK_REVERSAL = "junk_reversal"
    # Mining an ore vein with a pickaxe. A separate stream for the same question — players
    # ask whether mining yields degrade with farming time exactly as they ask it of chests —
    # and it MUST stay distinguishable: the two have different item pools, different costs
    # and different rates, so pooling them would answer neither question.
    MINING = "mining"


class LabelSource(str, enum.Enum):
    """Where the dungeon/floor labels came from.

    In OCR mode the player picks the dungeon, so labels are a claim, not a measurement.
    They are read off the screen, so they carry recognition error. Analysis must be able to
    split on this, both to measure the mislabel rate and to avoid treating a user claim as
    ground truth.
    """

    USER_DECLARED = "user_declared"
    API = "api"


class StopReason(str, enum.Enum):
    """How a session ended.

    Recorded because manual start/stop puts the session boundary under the player's
    control, and stopping may be OUTCOME-DEPENDENT — quitting right after a good drop, or
    after a bad streak, makes session-end correlate with drop quality and can manufacture
    exactly the "quality falls with farming time" pattern being tested. Without this field
    that bias is invisible; with it, the analysis can compare user-stopped sessions against
    idle/closed ones.
    """

    USER_STOP = "user_stop"
    IDLE_TIMEOUT = "idle_timeout"
    GAME_CLOSED = "game_closed"
    APP_CLOSED = "app_closed"


class ReceivedItem(BaseModel):
    """One line of a chest's contents — junk, sale-only, valuable, or equipment.

    Item identity comes from the game's own item table (`item_type` is its `type`
    field, e.g. Item::Junk / Item::SaleOnly). Quality and level apply only to equipment
    and stay None for everything else.
    """

    item_name: str = Field(description="As displayed, in the client's locale.")
    item_id: int | None = Field(
        default=None, description="The game's own item id."
    )
    item_type: str | None = Field(
        default=None, description="The game's own item type, e.g. 'Item::Junk', 'Item::SaleOnly'."
    )
    quantity: int = Field(default=1, ge=1)
    # WHETHER THAT NUMBER WAS OBSERVED OR INFERRED. The game prints no "×N" for a single
    # item, for equipment, or while a drop boost is active, so the client records 1 and
    # flags it rather than guessing — "never fabricate a quantity" is one of its hard rules.
    # Carrying the flag on the wire is what keeps that rule true past the client: without
    # it an inferred 1 and an observed 1 are the same row. Measured on the first real spool,
    # 40 of 94 content lines were inferred, and NONE of the observed lines was a 1 — so
    # server-side "quantity = 1" would have been 100% inference presented as measurement.
    quantity_unknown: bool = Field(
        default=False,
        # `qty_unknown` is what the capture client has always written into its spool, and
        # those files are the player's only copy of unsent data. Accepting both spellings
        # means a spool written before this field existed still uploads correctly, instead
        # of silently losing the flag it was carrying all along.
        validation_alias=AliasChoices("quantity_unknown", "qty_unknown"),
        description="True when the game printed no number and the 1 is an assumption.",
    )

    # -- equipment-only fields (None for junk / sale-only) --------------------------
    equipment_name: str | None = Field(
        default=None, description="Set only when this line is a piece of equipment."
    )

    # A displayed name does NOT identify a single equipment row. In the game's own table
    # 826 distinct names span 3,719 rows; rows sharing a name differ only by
    # grade_lottery_id / rarity_lottery_id and share one `identification` value. So the
    # name resolves to a FAMILY (`identification`), and the observed quality/level is what
    # pins down the variant. Storing a guessed `equipment_id` here would be fabricated
    # precision, so the family is the resolved key and the exact row stays optional.
    equipment_identification: int | None = Field(
        default=None,
        description="The equipment family key, which is stable where the display name is not.",
    )
    equipment_id: int | None = Field(
        default=None,
        description="Exact equipment row, only when the source gives it unambiguously.",
    )
    quality: int | None = Field(default=None, ge=1, le=5, description="品質 ★1-5")
    level: int | None = Field(default=None, ge=1, le=5, description="等級 1-5")

    # Optional but high-value: the published rate table is conditioned on WHICH junk was
    # reversed. Without it we can still compare time buckets against each other; with it
    # every observation gets an absolute expected distribution to be tested against.
    # Only meaningful when provenance == JUNK_REVERSAL.
    source_junk_name: str | None = None
    source_junk_id: int | None = None

    match_confidence: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Render-and-compare match score for this line.",
    )
    raw_text: str | None = Field(
        default=None,
        description=(
            "The raw message line as read (e.g. '獲得了蒼藍礦石 × 3！！'), retained when the "
            "match failed or was low-confidence so the vocabulary can be repaired and the "
            "event re-resolved server-side."
        ),
    )


class DiveContext(BaseModel):
    """The independent variable lives here.

    `elapsed_seconds` is measured from entering the floor/dungeon to the moment of the
    acquisition. `chest_index_in_dive` is recorded because it is the main RIVAL
    explanation: if quality falls with elapsed time, it may simply be falling with the
    number of chests already opened. Only recording time would make the two
    indistinguishable.
    """

    dive_id: UUID
    started_at: datetime
    elapsed_seconds: int = Field(ge=0)

    # Required in practice: the client gates its Start button on a dungeon being chosen.
    dungeon_id: int | None = None
    # OPTIONAL and often null by design. Players will not keep a floor dropdown current, and
    # a stale label is worse than an honest null — so the client makes it optional and
    # sticky rather than mandatory. Note drop tables are keyed per floor, so a null floor
    # means the dungeon stratum pools several distributions; a source that gets the
    # true floor for free) is what calibrates whether that pooling is safe.
    floor_id: int | None = None

    label_source: LabelSource = LabelSource.USER_DECLARED
    # Known only when the session ends, so the uploader backfills it across the dive.
    stop_reason: StopReason | None = None

    chest_index_in_dive: int | None = Field(default=None, ge=0)


class CaptureInfo(BaseModel):
    mode: CaptureMode
    client_version: str
    game_version: str | None = None
    locale: str = Field(default="zh_tw", description="Client display locale, e.g. zh_tw/ja/en.")
    # Free-form QC signals (screen resolution, detector version, template set hash...).
    qc: dict[str, str | int | float | bool] = Field(default_factory=dict)


class DropEvent(BaseModel):
    """One submission. `event_id` is the idempotency key — resubmitting is safe."""

    schema_version: int = SCHEMA_VERSION
    event_id: UUID
    install_id: UUID
    occurred_at: datetime = Field(description="UTC, client clock.")
    tz_offset_minutes: int = Field(
        ge=-24 * 60, le=24 * 60,
        description="Client local offset from UTC, for clock-sanity QC only.",
    )
    provenance: Provenance
    contents: list[ReceivedItem] = Field(
        default_factory=list,
        description=(
            "Every line the chest reported — junk, sale-only, valuable, equipment. "
            "MAY BE EMPTY: the game has its own empty-chest message "
            "(DungeonTreasure@DropEmpty, 「但是裡面什麼都沒有……」). An empty chest is a real "
            "observation and the WORST outcome, so it must be recordable — dropping such "
            "events would delete the bottom of the distribution and inflate every measured "
            "drop rate."
        ),
    )
    # The session ended while this chest was still open, so its content list may be short.
    # Recorded rather than dropped, because a chest that was never recorded and a chest that
    # never happened are indistinguishable afterwards — but the analysis must be able to
    # exclude it on evidence, which it cannot do if the flag stops at the client.
    truncated: bool = False
    dive: DiveContext | None = None
    capture: CaptureInfo

    @field_validator("occurred_at")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware (send UTC)")
        return v


class IngestBatch(BaseModel):
    """Clients batch events so a dive can be uploaded in one request."""

    events: list[DropEvent] = Field(min_length=1, max_length=500)


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    errors: list[str] = Field(default_factory=list)


class DiveClose(BaseModel):
    """How a dive ended, sent after the events it produced.

    `stop_reason` is known only when the session ends. While a dive's events are still in
    the spool the client stamps them in place, but per-record sending — the default — has
    already uploaded them by then, and they are stored with a null. This backfills those
    rows, so the outcome-dependent-stopping check StopReason exists for is possible on the
    mode most players will actually use.

    `install_id`, not `player_id`: the client cannot compute the pseudonym, and it scopes
    the update to the caller's own rows so knowing a dive_id is not enough to write to
    someone else's. It travels in the BODY for the same reason erasure does.
    """

    install_id: UUID
    dive_id: UUID
    stop_reason: StopReason


class DiveCloseResult(BaseModel):
    updated: int
