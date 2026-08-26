# Changelog

All notable changes to the client are recorded here.

The format is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The version a record was
read by is stamped on the record itself, so what an older build missed stays identifiable
rather than being mistaken for a chest that held less.

Drafted with git-cliff, then **written by hand** — and that order is the point. The draft
groups the conventional commits into Added/Changed/Fixed; what a player needs is what changed
for *them* and whether their existing recordings are worth re-verifying, and that never fits
in a commit subject.

    tools/draft_changelog.sh      the commits since the last tag, grouped, to stdout

The release page is built from this file (`build_exe.py --release-notes`), so what survives
the editing is what players read.

## [Unreleased]

Nothing yet.

## [0.9.2] - 2026-08-27

The game's own item table moved to **1.35.0** (live 2026-08-20) and this build carries it.
No code changed — but the version did, and that is the point of the release.

### Added

- **Two items this client could not see before.** 稀代の英傑の印 and 夕凪の女傑の印 are new
  event points, and the recogniser matches a closed list of names: one it does not carry is
  not misread, it is *silently left out*. A chest holding one was recorded looking complete
  with a line simply gone — the same way 遺物残渣 went missing before the points table was
  added at all.
- **Four junk names from フォードレイグの迷宮**, which the dungeon list already offered. Diving
  there on 0.9.1 or earlier means its junk was not recorded, and nothing on screen or in the
  file says so.

### Changed

- **The version number, so your records can be told apart.** Nothing a record carries says
  which item table read it — `client_version` is the only handle there is. Had this shipped
  under 0.9.1, rows read against the 1.34.5 table and rows read against 1.35.0 would be one
  indistinguishable pile, and "did this build know about that item" would have no answer.
  That is the whole reason a data refresh gets a release of its own.
- **Eight 導きの霊廟の遺骸 ids left the list.** They are still in the game; they no longer pass
  the filter for what a dungeon can actually hand over. Nothing recorded so far names one.

### Unchanged, and worth saying

Item **names** did not move: zero renamed, zero retyped, across 1,574 items and 812 equipment
families. Ids stay the durable identity, and everything recorded on 0.9.1 remains directly
comparable with everything recorded on this build. Re-verifying old recordings is not needed.

**What is not fixed here.** The panel geometry shipped with the client was fitted against the
older table and no longer matches this one, so the client re-fits it once per session
(a second or so, on the first mining panel) and then remembers it. Nothing is read worse;
it is a second of work per dive until those fits are made again.

## [0.9.1] - 2026-08-18

### Fixed

- **Recording works on Windows 10 again.** Pressing Start ended a few seconds later with
  *"window capture produced no frames. Is the game minimised?"* — with the game plainly on
  screen and drawing.

  The client reads the game's **own window** rather than a picture of your screen, so that
  anything you put in front of the game does not land in the recording. When it asked for
  that, it also asked Windows to switch off the yellow *being captured* border that appears
  around a window being read. That switch exists only on Windows 11. Asking for it on
  Windows 10 does not get politely ignored: it ends the capture before a single frame
  arrives, which is why the client then said it had seen none.

  It now asks only for what the Windows it is running on actually has. Windows 10 records
  through the same path as Windows 11 and loses nothing by it — Windows 10 never draws that
  border in the first place.

  **This has been the case since 0.5.2**, so a Windows 10 machine could not record at all
  for that whole time unless it was told to read the screen instead. Nothing about how a
  frame is READ changed here: recordings already made are unaffected, on either Windows, and
  none of them need re-verifying.

## [0.9.0] - 2026-08-17

### Added

- **Pause.** Start recording now turns into *Pause*, with *Stop recording* beside it. They
  used to be one button, which meant a town trip or a phone call cost you the dive: the
  clock a chest is filed under runs from the moment you pressed Start, so stopping and
  starting again splits one farming run into two. Pausing keeps the run. Nothing is read
  while it is paused, and the record page says so — the numbers stop moving, and a number
  that has stopped with no reason beside it looks like a fault.

- **Look back at a session you have already recorded.** A picker on the right of the dungeon
  row lists your finished sessions, newest first, with when each one was and what came out
  of it. Choosing one shows that session's ledger exactly as you watched it happen. Nothing
  is sent or changed by looking, and everything it shows was already on your own computer —
  no new information is recorded for this.

- **Delete a reading the client was not sure of.** Some rows now carry a *Delete* button:
  an amount the game never printed, a name that could not be placed, a panel line that was
  not read, junk that does not match the dungeon you chose. Those are the ones you can settle
  by looking at your own screen, which is evidence nobody else will ever have. Hover it and
  it tells you what was wrong with the reading, and what deleting it will do.

  Records are now **held for 20 seconds before being sent** (Settings → *Hold each record
  before sending*), and while a record is held, deleting it removes it here and the study is
  never told at all. After that the study has it, and deleting asks them to remove their copy
  too — which they will do for a day. There is no countdown anywhere: when it can no longer
  be taken back the button is simply gone.

  **An empty chest never gets one.** It is a real observation and the worst possible outcome,
  and letting those be deleted one at a time would quietly raise every drop rate this study
  is trying to measure.

### Changed

- **A vein is read against eleven fewer names.** 錆びついた古銭, 貝貨, 砕けた徽章 and the
  紅焔 and 雪光 families were in the list the mining panel could match, and a vein here does
  not produce them. A name that cannot appear does not lose quietly — it wins — so taking
  them out can only help, but it does mean a mined line that was previously read as one of
  those will now read as the nearest name that is left. **No recorded swing has ever produced
  one of the eleven**, so no existing recording is affected.

- **Sale-only items are counted with the money.** On the Stats page, anything the game marks
  as sold-only now sits under *Currency* rather than *Items* — including 透明な小石 and the
  蒼雫 ores. The items list then answers "what did this dive give me" and the currency list
  answers "what did it cash out to", instead of one ranking where the ore buries everything
  else.

- **The Stats headline shows one source at a time.** Choosing *Chests* used to print
  「0 vein」 beside it, which reads as "you mined nothing" on a page that is deliberately not
  showing mining. The openings count goes too when a source is chosen — with one source it
  was the same number printed twice.

- **The pickaxe count is put away while you are looking at an earlier session.** It is the
  number in your bag right now, and editing it against a session that ended last night reads
  as editing that session.

### Fixed

- **The word "paused" no longer stays on screen after you stop.** It described a session that
  had already ended.

- A recording that fails now leaves the page in the same state a stopped one does. It was
  leaving the main button looking like a session was still running.

### For the record

- Records now say how much of a dive was spent paused, so a paused break can be told apart
  from time spent playing. The dive clock itself is unchanged and still measures wall time
  from when you started, which is what every recording made so far means by it.

- A broken pickaxe is now filed under the session it happened in. The ones already recorded
  carry no session and cannot be placed, so they will not appear when you look back at a
  session from before this version.

## [0.8.0] - 2026-08-16

### Added

- **Settings → New versions → Check now.** The client already asks once when the window
  opens, but it says so on the same line everything else in the window says things — so the
  notice can be gone before you read it, and there was no way to ask again. This asks on
  demand and tells you which of the three answers came back, including *this is the newest
  version*. It is off while the switch above it is off, because that switch stops the request
  itself and not merely the message. The terms name the button, so the client asks you to
  accept them again.

### Changed

- **A vein is read against what a vein can produce — 247 names instead of 2,384.** The mining
  panel was matched against the same list the chest message is, equipment included, and a
  name that cannot appear does not lose quietly: it wins. One swing came back as 「朧丸」, a
  katana, which the player confirmed had never been there. Ore, reinforcement material and
  modification stones are what a vein hands over; everything else has been taken out of the
  panel's reach. Nothing a vein has ever produced is affected — all seven names recorded
  across 128 swings are still in the list.
- **The statistics table lists items first and money underneath.** Currency was on top
  because it is the shorter list; what that did was put the two rows nobody opened a chest
  for above the ranking the page exists for.
- **Calibration is no longer offered.** The client ships fits for the sizes the game itself
  offers — 1920 × 1080, 1600 × 900, and the 704 × 1241 tall window — and reads a larger
  screen of the same shape by scaling the picture back down, so there is nothing left for a
  player to fit. A fit made on your own machine is one nobody has checked against a
  recording, and one such fit was wrong for three versions without anything saying so. A
  window size the client cannot read now names the sizes it can, instead of telling you to
  run a command that is not in your build.

## [0.7.1] - 2026-08-16

### Changed

- **Your id is no longer printed on the Settings page.** It sits under a bar you click to
  lift, the same one the story endings use, and it goes back under it as soon as you leave
  the page. That id is the only thing the server can identify you by — nothing else about
  you was ever stored — so anyone who has it can ask for your records to be deleted, and the
  page it was printed on is the page people photograph when reporting a problem. Click it
  when you need it; select it to copy it.
- **Asking to be forgotten now takes effect at once and can still be undone for a week.**
  Your records leave every statistic the moment you ask, and are wiped from the server seven
  days later. Those days exist for exactly one reason: a deletion nobody meant — a mistyped
  id, or someone else's — used to be permanent, because the client only ever re-sends what
  is still waiting to be sent, never what has already gone.
- The terms say both of those things now, so the client asks you to accept them again.

## [0.7.0] - 2026-08-16

### Added

- **The client asks how far through the story you are, and which promotion exam your main
  character has passed.** Both change the game you are playing — how strong the enemies are,
  which groups appear at all, what some quests pay — and neither is shown on any screen, so
  the only way to know is to ask. It is asked once, when you pick a dungeon it can matter in,
  and it is always editable in *Settings → 劇情進度*. Answering is optional.
- Those two answers now travel with each record when sharing is on. Without them, "the drops
  got worse" cannot be told apart from "these two players were not playing the same game" —
  which is the whole question this study exists to answer. The disclaimer says so, and asks
  you to accept the terms again.

### Changed

- The disclaimer reads like a document rather than a wall: headings that look like headings,
  space between paragraphs, and it can be re-read at any time from Settings.

### Fixed

- Every scrollbar in the window matches again. The guide page had one of its own, and a fix
  for that turned into every *other* page losing theirs.
- Dialogs — the story question, the disclaimer, **See it** — wear the same dark frame and
  square corners as the window instead of the system's rounded, pale one.
- Buttons on the Settings page are the width of their labels rather than the width of the
  page.

## [0.6.0] - 2026-08-15

### Added

- **Full screen now works**, and on a screen larger than the game as well. Set the game's
  own resolution first — **1920 × 1080** or **1600 × 900**, both in its own options — and the
  client reads the picture at the size it was drawn, however far your desktop stretches it.
  Measured over 15 confirmed chest lines, undoing the stretch costs about 0.016 of matching
  score against a 0.60 threshold and changed no reading. A screen whose shape differs from
  the game's (21:9, 16:10) still asks you to calibrate: the game may be letterboxing there,
  and reading the wrong rows silently is worse than saying so.
- Every button in the window says what it does when you hover over it.
- Calibration for **1920x1080** now ships. A player at that size no longer has to calibrate,
  and gets a fit that was replayed against real recordings rather than one improvised on
  their machine.
- The client now **checks what the game is set to render** and warns when that is smaller
  than what it is reading. Enlarging cannot put back ink that was never drawn: at a 1280x720
  game resolution one of those same 15 lines fell under the threshold, and an under-threshold
  line is dropped rather than guessed.
- Calibration for **1600x900** now ships as well, fitted from a real recording rather than
  from shots taken standing still — the minimap it looks for is the button bar under the map,
  not the map itself, which is what a calibration made on the spot had settled on.
- Recordings are saved under the resolution they were made at —
  `capture/1920x1080/session-…` — because everything about reading a frame is fitted per
  resolution, and a folder of mixed sizes has to be sorted before any question can be asked
  of it.
- `verify` shows **pickaxe breaks** and asks whether each one really happened. A false break
  spends a pickaxe the player still has, and it was the one reading the tool could not see.
- `verify` shows **what you confirmed** beside what the client reads now, with a
  `13 match, 1 differ` count at the end — a re-read of a confirmed session is the only
  regression figure there is. `--differing` asks only about the ones that changed.
- The window writes a **log file** at startup, honouring the trace setting. The released
  client never wrote one: it entered through the window, and the window set up a console
  logger in a build that has no console.
- In a source checkout, each reading on the record page names the frame it came from
  (`[episode-211/f_00109.png]`). Mining readings carry that frame too, which they never did.

### Changed

- **The "A pickaxe broke" button is gone.** The client reads the break message itself at
  every size it ships a calibration for, and a button that duplicates a reading is a second
  source of truth for the number every mining rate divides by.
- The guide names the sizes that work and the one that does not (**1280 × 720**, where the
  game draws the names with too little detail to read), and says which are landscape and
  which is the tall window.
- The item vocabulary now carries only what a dungeon can hand over — 2,154 names, exactly
  what the client has always narrowed to before reading anything. The download is smaller and
  nothing about recognition changes.
- The window no longer prints "unofficial" beside its own name on every screen. It says it
  once, on the Settings page, where a player goes to find out what the program is.
- The pickaxe count stays editable while recording. Restocking happens mid-dive, and the
  correction now reaches the running reader instead of being overwritten by the next break.
- The frame counter on the record page is a source-checkout thing again. The released exe
  carries the development marker on purpose, which had been showing it to everyone.
- Calibration takes a short **burst** of walking shots rather than one, and asks you to keep
  walking. One picture cannot tell the minimap's furniture from its map, and the map is the
  half that must never be matched.

### Fixed

- **Opening a chest could freeze capture for up to nine seconds**, and the chest's own drop
  lines were never sampled because nothing was. The chest's 「だれが開ける？」 prompt looks
  exactly like a mining panel, and fitting one costs an index build per geometry tried.
- **Mining at 1920x1080**: ore lines went missing, and pickaxe breaks were not detected at
  all. Panel rows are anchored differently at different resolutions, and the search now
  covers that rather than assuming it.
- **A chest read one item short** when its message wrapped onto a second row, and the
  client's own letter spacing was fitted on a name too short to measure it.
- **A chest could be recorded as empty** while the line was plainly there on the frame, at a
  resolution the player had calibrated themselves. Calibration checks its fit by reading back
  the name you gave it, and two geometries can read that name equally well while only one of
  them reads a name written in digits — 「10,000バイン紙幣」 scored 0.5982 under one and
  0.8536 under the other. Calibration now looks past its own name when the check is close.
- **Quantities**: `×10` came back as unknown, a two-digit number could lose its first digit,
  and `×13` could be read confidently as `×18` — a screenshot widens a stroke by about a
  pixel, which is the whole difference between one digit and another.
- **The last chest of a session** was stamped `00:00` when you stopped the recording while
  it was still being read — before every chest that preceded it.
- `verify` treated every mining swing in a session as the same entry, so confirming the
  first marked all the rest confirmed without ever showing them.
- A calibration that ships now replaces a stale local one for that size without discarding
  the mining panel's geometry, which is learned on the player's machine and cannot ship.

## [0.5.3] - 2026-08-13

### Added

- A rotating log at `%LOCALAPPDATA%\wddrop\logs`, INFO by default and DEBUG when the trace
  setting is on, so a missed drop can be explained after the fact.
- One request to GitHub when the window opens, asking whether a newer build exists. It
  carries nothing about the player or their game, and *Settings → New versions* stops it
  being made at all.
- **See it** — draws the regions the client reads and the strips it actually receives.
- The stats page separates currency from items and shows every duration as MM:SS.

### Changed

- The answer space is the 2,154 names a chest can contain rather than all 3,268 — mission
  passes, skill books, NPC gear and event stock cannot come out of a chest.
- Calibration at 1920x1080 finds the minimap and fits the band against its own typeface.

### Fixed

- Letter spacing is a full-width correction, so `10,000バイン紙幣` reads. It was being
  applied to half-width glyphs as well, which pushed every ASCII name out of alignment.

## [0.5.2] - 2026-08-13

### Fixed

- The mining panel is rendered at its **own** letter spacing rather than the message band's.
  Spacing is added per character, so the error accumulates along the line and the damage is a
  function of name length: on one real panel 「ウロボロス鉱石」 scored 0.5595 against a 0.60
  gate while 「透明な小石」 beside it read 0.706, and the yield was recorded with that line
  simply missing. Fitted rather than inherited, the same two lines read 0.847 and 0.895.
  Replayed over seven recorded sessions: every panel line read, and both pickaxe breaks still
  counted.

---

Versions before 0.5.2 were built and tested but never published, so they are not listed here.

[0.9.2]: https://github.com/exkuretrol/wddrop-client/releases/tag/v0.9.2
[0.9.1]: https://github.com/exkuretrol/wddrop-client/releases/tag/v0.9.1
[0.9.0]: https://github.com/exkuretrol/wddrop-client/releases/tag/v0.9.0
[0.8.0]: https://github.com/exkuretrol/wddrop-client/releases/tag/v0.8.0
[0.7.1]: https://github.com/exkuretrol/wddrop-client/releases/tag/v0.7.1
[0.7.0]: https://github.com/exkuretrol/wddrop-client/releases/tag/v0.7.0
[0.6.0]: https://github.com/exkuretrol/wddrop-client/releases/tag/v0.6.0
[0.5.3]: https://github.com/exkuretrol/wddrop-client/releases/tag/v0.5.3
[0.5.2]: https://github.com/exkuretrol/wddrop-client/releases/tag/v0.5.2
