# Testing on the Steam client (PowerShell)

> Written for running the client **from a checkout**. A player who downloaded `wddrop.exe`
> needs none of it — they get the window, and half the commands below are not in their build.

Everything runs through `wddrop.py`, from `Documents\wddrop`. No build step, no PYTHONPATH,
no `--data`: state lands next to the script.

```powershell
cd $HOME\Documents\wddrop
uv run wddrop.py --help
```

## The window

Everything below can be done from one window instead, which is what a player should be
given:

```powershell
uv run --with PySide6-Essentials wddrop.py ui
```

It gates on consent, picks the dungeon from the catalogue, and starts, pauses and stops
capture while showing frames, minimap hits and chests as they land. PySide6 is not in
`wddrop.py`'s dependency list on purpose — it is ~80 MB, and capture must not stop working on
a machine where a GUI toolkit will not install.

It **has** been run against the live game on Windows, since 2026-08-10, and everything under
Known limits came out of those runs rather than from reasoning about them.

`uv run` installs the dependencies on first use. If you prefer plain Python:

```powershell
py -m pip install pillow numpy mss pydantic httpx
py wddrop.py --help
```

> Do **not** use `uv run -m wddrop_client` — in a checkout the client is a folder rather than
> an installed package, so that form fails with "No module named wddrop_client". `wddrop.py`
> exists to put it on the path for you. (The wire format is different: `wddrop-schema` is a
> pinned dependency and arrives installed, which is why nothing puts it on a path.)

---

## 1. Calibrate — only if you have to

**Three resolutions ship a verified fit** — 1920x1080, 1600x900 and the 704x1241 tall window —
and a larger screen of the same aspect is read by scaling the picture back down. If you play
at one of those, skip this section: calibrating replaces a fit checked against real recordings
with one nobody has checked.

Calibration is also **not in a released build**. It is gated on `in_development()`, so these
commands work from a checkout and are absent from the exe a player downloads — a fit made on
a player's machine is a claim nobody has verified, and one such fit was wrong for three
versions without any score reporting it.

```powershell
uv run wddrop.py calibrate
```

It prompts twice. Press ENTER, then switch back to the game — there is a 4 second countdown
before each capture, so the shot is of the game as you see it, not of the window unfocused.
Use `--delay 6` if that is tight.

```
  STEP 1/2 — stand in a dungeon with the minimap visible top-right.
  ready? [ENTER = capture / s = skip]:
      switching back to the game... 3
      looks good -> walk.png

  STEP 2/2 — open a chest and leave a 「獲得了…」 message on screen.
  ready? [ENTER = capture / s = skip]:
      looks good -> drop.png

  Which item does that message name?
  > 初始的雜物
```

Then it fits and checks itself:

```
[+] band=(870, 889) size=22px offset=(0,-1) spacing=+0.4 score=0.879
[+] self-check: {'self_check_name': '初始的雜物', 'self_check_margin': 0.0839, ...}
[*] HUD check: walk shot +1.000, drop shot +0.143
```

Two checks have to pass, and both matter:

- **self-check** — it re-reads your own drop shot against the full vocabulary and must come
  back with the item you typed. It refuses to save a profile that cannot read the frame it
  was built from.
- **HUD check** — the walk shot must look clearly different from the drop shot. If the two
  scores are close, both captures show the same state and chests would never be bracketed.
  (Per-shot validation cannot catch this: a "find the minimap" test accepts a chest
  screenshot just as happily. Only the comparison is decisive.)

If you already have screenshots, the manual form still works:

```powershell
uv run wddrop.py calibrate --drop-shot drop.png --name "初始的雜物" --walk-shot walk.png
```

They must be at native resolution, unscaled and uncropped — which is precisely the mistake
the guided flow removes.

## 2. Dry run before collecting anything

`replay` prints what *would* be recorded and writes nothing.

**First** record a short clip of two or three chest opens — at the resolution you
calibrated at — then either point `replay` straight at the video:

```powershell
uv run wddrop.py replay clip.mp4 --dungeon 2000 --fps 4
```

or split it into frames yourself and pass the directory:

```powershell
mkdir frames
ffmpeg -i clip.mp4 -vf fps=4 frames\f_%05d.png
uv run wddrop.py replay frames --dungeon 2000 --fps 4
```

Passing the video directly is simpler; it needs `ffmpeg` on PATH either way.

Compare the printed items against what actually dropped. That comparison is the whole point
of this step.

## How to play while recording

Four habits, each of which exists because breaking it loses data that cannot be recovered
afterwards. The client reads your screen; it can only record what was actually drawn.

**1. Pick the right dungeon before you start.** It is the one thing the client cannot check
for you, and it is the stratum the whole analysis is cut by — a wrong dungeon does not add
noise, it files your chests under someone else's distribution. The window will not let you
start until you have chosen one. (If the junk you get is named after a different dungeon, the
client says so during the session; stop and fix it rather than carrying on.)

**2. Chests: let each line finish before you advance.** Items arrive one message at a time,
and the line animates in character by character. The client refuses to read a half-drawn
line, because **191 zh_tw item names truncate into a different valid item name** — so an
early read is not a near-miss, it is a confident wrong answer. Wait until the line is
complete, then advance. Advancing faster than the text draws loses that item silently.

**3. Veins: wait for the ▼ before you dismiss.** The arrow is the game saying the panel is
finished and waiting for you, and the client now looks for it directly — so as soon as it
appears, the swing is recorded and you can click. One vein is many swings and each is its
own observation, so this matters every time, not once per vein. (Before the client watched
for the arrow, three swings of one vein dismissed inside two frames each were lost
entirely; now the marker is enough.)

**4. Stop between chests, not during one.** A chest still open when you press Stop is
recorded and flagged as possibly incomplete, which is better than losing it — but it is data
that has to be excluded later. Finish the chest, then stop.

And one thing to watch rather than do: the counters under the Start button. If **hud** is
still 0 after a few hundred frames, the minimap is not being seen, chests will not be
bracketed, and the session is not recording what you think. Stop and recalibrate.

If something is recorded wrongly, tick **Keep the frames** and do it again — a recording can
be replayed and re-read after a fix, which is how every problem in this list was found. A
session without frames can only be described, not diagnosed.

## 3. Live capture

```powershell
uv run wddrop.py windows                                 # check the game window is found
uv run wddrop.py dungeons                                # find your dungeon id
uv run wddrop.py consent                                 # required before collecting
uv run wddrop.py run --dungeon 2000 --floor 200002
```

Sampling defaults to **20 fps**, and warns below **16**. This is the one setting worth
getting right: a drop message dismissed between two samples is never captured at all, and
unlike a misread it cannot be recovered later from the recording — the pixels were never
seen. Live capture reads only two thin strips (2.8% of the window), so the rate is cheap.

`--floor` is optional. Ctrl-C to stop. Nothing uploads — events go to a local spool file.

Each chest prints as it lands:

```
    chest #1 @42s  透明鵝卵石 x1?, 鏽跡斑斑的古錢 x1?
```

A `?` after the quantity means the game showed no number. That is normal — a single item
and an equipment drop both render without one, and it is recorded as 1 with a flag rather
than guessed. Review anything recorded so far with:

```powershell
uv run wddrop.py drops
```

### Recording a session for offline replay

To capture what the recogniser actually saw, so a run can be re-tested without the game:

```powershell
uv run wddrop.py run --dungeon 2000 --fps 4 --record capture
```

Frames are filed per session, then per episode:

```
capture\
  session-20260809-004512\
    episode-001\   f_00001.png ...     one chest / battle / conversation
    episode-002\   f_00001.png ...
```

A separate directory per run means recording twice never mixes two sessions. A separate
directory per episode means the frames behind one suspect drop can be inspected on their
own, instead of being hunted for among a few thousand PNGs.

By default only HUD-absent frames are kept (chests, battles, dialogue) plus a few either
side, so the HUD transitions a replay needs are preserved. `--record-mode all` keeps
everything and grows fast. Both stop at 4000 frames.

Frames are PNG, not video, deliberately: the recogniser works on pixels and mp4 compression
alters them, so a lossy recording would not reproduce the run it is meant to explain. Replay
using the line printed when the run finishes:

```powershell
uv run wddrop.py replay capture\session-20260809-004512 --dungeon 2000 --fps 8
```

Point it at a session folder to replay the whole run, or at a single `episode-NNN` folder to
re-test just that one.

Replay is a dry run — it prints what it would record and writes nothing. Add `--spool` to
write the results. That is what makes recordings authoritative: when a recognition bug is
fixed, the data can be **rebuilt from the frames** rather than re-collected in game.

```powershell
Remove-Item spool.jsonl
uv run wddrop.py replay capture\session-... --dungeon 7015 --fps 8 --spool
```


---

## 4. Confirm what each chest really held

The client cannot report a drop rate without knowing its own error rate, and every problem
so far was found by you noticing a wrong chest. This records that judgement instead:

```powershell
uv run wddrop.py verify capture\session-... --dungeon 7015 --fps 12
```

Each chest is listed with the frame every item came from:

```
  session-20260809-024153#1  (@2s)
    - 溶喰的扭曲根晶        x1?   episode-001/f_00019.png
    - 100拜恩紙幣          x2    episode-001/f_00022.png
    correct? [y / n = type the real items / s = skip]:
```

`y` confirms; `n` then type the true items as `name xN`, **separated by `;`** (omit `xN` if
the game showed no number, or type `(nothing)` for an empty chest); `s` skips. Open the
listed frame if you are unsure — that is what it is there for. Answers are keyed by session
+ chest index, so they survive re-replaying the same recording after a fix.

> Separated by `;`, not by a comma, because item names contain commas — `10,000拜恩紙幣` is
> one item. Every name you type is checked against the vocabulary and anything unrecognised
> is flagged, so a typo cannot quietly become ground truth.

If you would rather write the session down than answer prompts, put the answers in a file
and pass it — same format, one chest per line:

```
session-20260809-034520#1: 莫尼翁銀幣 x2; 北穿幽靈城的四鱗雜物
session-20260809-034520#2: (nothing)
```

```powershell
uv run wddrop.py verify capture\session-... --dungeon 7015 --transcript mysession.txt
```

Add `--verified-by frames` if you are reading a *recording* rather than remembering what you
saw. Those two are reported apart, because a recording can only show what the capture
caught — it cannot tell you about a message that was never sampled.

Do **not** pass `--fps` here: replay adopts the rate the recording was captured at, and
overriding it rescales every elapsed time.

```powershell
uv run wddrop.py accuracy
```

reports measured accuracy and lists the specific failures, in three separate categories:

| category | meaning | why it is separate |
|---|---|---|
| **missed** | on screen, never recorded | understates a drop rate |
| **spurious** | recorded, was not there | invents data |
| **wrong quantity** | right item, wrong count | a name-only check scores it as perfect — which is how a real x1 recorded as x9 went unnoticed |

---

## What to send back

- `profile.json` (the HUD template is embedded in it now, so it is self-contained)
- the output of `uv run wddrop.py drops`
- a `--record` directory, if you took one — that lets the recogniser be re-tested offline
- `review.json` if it exists (readings the recogniser refused to guess at)
- `verified.json` — the confirmations, which is what makes accuracy measurable

## Known limits on this build

- **A profile belongs to one resolution.** Every region in it is absolute pixels. A
  resolution change is detected and refused; a window *move* is handled (capture follows the
  window), but a **resize** invalidates the profile. This only bites if you calibrated: the
  three shipped fits cover the sizes the game itself offers.
- **Quantity may print as `x1?`.** That means the game showed no number — normal for a single
  item, for equipment, and while a drop boost is active. It is recorded as 1 with a flag
  rather than guessed, and the server can resolve it later from the boost calendar.
- **One-character rivals are refused, not guessed.** Measured: 初始的扭曲一縷**重**武器雜物 vs
  初始的扭曲一縷**中**武器雜物 score 0.882 vs 0.859 — under the margin gate, so the reading goes
  to `review.json` instead of being recorded wrongly.
- **The atlas is not shipped, and the locale is not a choice.** Since 0.5.0 the client builds
  the atlas on your machine from the copy of the game you already have, and the game language
  is Japanese — `--vocab` and `--catalog` default to the `ja` files. The flags below are only
  for pointing at something else deliberately.
- **A chest still open when you press Ctrl-C is recorded with `truncated: true`.** Its item
  list may be short, so it can be excluded at analysis time — but it is not thrown away, as
  a missing chest and a chest that never happened look identical afterwards.
- **Sampling defaults to 20 fps, minimum recommended 16.** A message dismissed between two
  samples is never captured, and no later fix can recover it. A line that vanished before
  settling is still read once, but only if at least one frame caught it.
