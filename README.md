# wddrop

Records what chests and ore veins give you in *Wizardry Variants Daphne*, so drop rates can
be worked out from what players actually see rather than from impressions.

It reads your own screen. It does not modify the game, inject anything into it, read its
memory, touch network traffic, or press any key for you — see [DISCLAIMER.md](DISCLAIMER.md)
before you use it.

## Getting it

Download `wddrop.exe` from [Releases](../../releases) and run it. Nothing to install.

Everything it keeps lives in one folder — `%LOCALAPPDATA%\wddrop` — and the window will open
it for you. Deleting that folder removes everything held on your computer.

## Using it

1. Play in the **tall window (704 × 1241)**. It is the only size that reads reliably today,
   and the client already has a calibration for it. Other sizes, full screen included, are
   not recommended yet: they sample the screen more slowly and some item names are still
   misread.
2. Pick the dungeon you are in. It is the one thing the window cannot check for you.
3. Press **Start recording**, and play normally.

Let each drop line finish before advancing — a half-read line is a confident wrong answer,
not a near miss.

## Sharing

Recording and sharing are separate. Everything is recorded on your own computer either way;
sharing only decides whether a copy is also sent, and it is off until you turn it on. The
window asks once, and Settings can change the answer at any time.

Your records are pooled with other players' to work out per-dungeon drop rates. The identifier
sent with them is a random one this client made up; it cannot be traced back to a game
account, and you can have everything removed by quoting it.

## Building it yourself

    uv run --with PySide6-Essentials wddrop.py ui          # run from source
    uv run --with pyinstaller --with PySide6-Essentials build_exe.py   # one-file exe

The client needs its data files beside it — the item vocabulary, the glyph atlas and the
dungeon catalogue. They are not in this repository; see `build_exe.py` for what it expects.

## Unofficial

Fan-made, and not affiliated with or endorsed by the developers or operators of the game.
