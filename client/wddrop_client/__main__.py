"""
wddrop client.

    wddrop consent                      show the disclaimer and record acceptance
    wddrop dungeons                     list dungeon ids for --dungeon
    wddrop calibrate                    guided: takes the two screenshots for you
    wddrop calibrate --drop-shot X.png --name "初始的冥刻雜物" [--walk-shot Y.png]
    wddrop windows                      list windows, to find the game's title
    wddrop probe                        one live frame: what each detector sees
    wddrop run --dungeon 2000 [--floor 200002] [--source window]
    wddrop replay <dir|video> --dungeon 2000     re-derive drops (add --spool to write)
    wddrop drops                        show what has been recorded so far
    wddrop verify <recording>           confirm what each chest really contained
    wddrop accuracy                     measured accuracy against confirmed chests
    wddrop upload                       drain the spool to the server
    wddrop whoami                       print install_id (needed to request erasure)

Packaged for players as a single Windows executable.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger("wddrop.cli")

from wddrop_schema.models import CaptureMode

from .config import ClientConfig, config_dir
from .consent import ConsentRequired, disclaimer_hash, disclaimer_text
from .uploader import upload_spool

PROFILE_NAME = "profile.json"

# Live sampling rate. Below MIN_FPS, messages dismissed by a fast player fall entirely
# between samples and are lost with no trace -- the one failure no later fix can undo, since
# the pixels were never seen. Both numbers come from field testing on a real client.
LIVE_DEFAULT_FPS = 20.0
MIN_RECOMMENDED_FPS = 16.0


def _data_path(args, name: str) -> Path:
    root = Path(args.data) if getattr(args, "data", None) else config_dir()
    return root / name


def _load_vocab(args):
    from .capture.ocr import MessageFormat, Vocabulary

    path = Path(args.vocab)
    if not path.exists():
        raise SystemExit(f"[!] vocabulary not found: {path}\n    build it with tools/build_vocab.py")
    return Vocabulary.load(path), MessageFormat.from_vocab(path), json.loads(path.read_text(encoding="utf-8"))


def _font_candidates(args) -> list[str]:
    import glob

    if args.fonts:
        return sorted(glob.glob(args.fonts))
    raise SystemExit("[!] --fonts is required (glob for the game's extracted *.ttf)")


def _render_source_override(args) -> str | None:
    """The font or atlas named by `--fonts`, when running rather than calibrating.

    Calibration sweeps a glob because it is choosing; everything downstream must render with
    exactly ONE source, so a glob matching several here is an error rather than a silent
    pick — the whole point of the override is to know which renderer produced a result.
    """
    if not getattr(args, "fonts", None):
        return None
    matches = _font_candidates(args)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"[!] --fonts matched nothing: {args.fonts}")
    raise SystemExit(
        f"[!] --fonts matched {len(matches)} files; name exactly one to render with:\n"
        + "\n".join(f"    {m}" for m in matches[:8]))


# -- commands --------------------------------------------------------------------
def cmd_consent(cfg: ClientConfig, args) -> int:
    print(disclaimer_text())
    print("=" * 72)
    if input("同意並開始收集資料嗎？ / Accept and enable collection? [yes/no]: ").strip().lower() not in {"y", "yes", "是"}:
        print("未同意，不會收集任何資料。/ Not accepted — nothing will be collected.")
        return 1
    cfg.consent.accepted_hash = disclaimer_hash()
    # Asked here as well as in the window, so `asked_sharing` is not left False by a player
    # who consented on the command line and never opened Settings.
    cfg.asked_sharing = True
    cfg.save()
    print("已記錄同意狀態。/ Consent recorded.")
    return 0


def cmd_dungeons(cfg: ClientConfig, args) -> int:
    path = Path(args.catalog)
    if not path.exists():
        raise SystemExit(f"[!] catalog not found: {path}\n    build it with tools/build_catalog.py")
    cat = json.loads(path.read_text(encoding="utf-8"))
    for d in cat["dungeons"]:
        print(f"{d['id']:>8}  {d['name']}   ({len(d['floors'])} floors)")
        if args.floors:
            for f in d["floors"]:
                print(f"           {f['id']:>8}  {f['name']}")
    return 0


def _grab_window(delay: float):
    """One frame of the game window, after a countdown.

    The countdown exists because answering a prompt moves focus to the console, and the shot
    must be of the GAME as the player sees it. Without it every capture would be taken with
    the window unfocused.
    """
    import time

    from .capture.source import ScreenSource

    for remaining in range(int(delay), 0, -1):
        print(f"\r    switching back to the game... {remaining} ", end="", flush=True)
        time.sleep(1)
    print("\r    capturing.            ")
    source = ScreenSource(fps=1, follow_window=True)
    return next(iter(source.frames())).image


def _guided_shot(what: str, check, delay: float, path: Path):
    """Prompt, capture, validate, and offer a retry until the shot is usable.

    Validating here rather than at fit time is the point: "your profile did not work" is a
    poor thing to discover after the fact, when the game state that produced it is gone.
    """
    while True:
        print(f"\n  {what}")
        answer = input("  ready? [ENTER = capture / s = skip]: ").strip().lower()
        if answer == "s":
            return None
        image = _grab_window(delay)
        problem = check(image)
        if problem is None:
            image.save(path)
            print(f"    looks good -> {path}")
            return image
        print(f"    [!] {problem}")
        if input("    try again? [Y/n]: ").strip().lower() in ("n", "no"):
            return None


def _walk_shot_problem(image):
    """Only a sanity check.

    A per-shot test of "is the minimap here" cannot work: detect_hud_region always returns
    its best candidate, so it accepts a chest screenshot just as happily. Verified — walk and
    chest shots both passed. The real test compares the TWO shots, and lives in
    _check_hud_separation below.
    """
    from .calibration import detect_hud_region

    try:
        detect_hud_region(image.convert("L"))
    except Exception:
        return "could not find any panel — is the game on screen?"
    return None


def _check_hud_separation(profile, walk_image, drop_image) -> None:
    """The captured pair must actually distinguish walking from a chest.

    This is the check that means something. The HUD template is cut from the walk shot, so it
    scores ~1.0 there by construction; what matters is that it scores LOW on the drop shot.
    If both look alike, the two captures show the same state -- two walk shots, or two chest
    shots -- and episode bracketing would never fire. Better to say so now than to let a
    session record nothing and leave the cause to be guessed at.
    """
    from .calibration import HUD_STRAIGHTNESS_MIN, hud_straightness
    from .capture.hud import HudDetector

    # Does the region even look like a PANEL? The separation check below compares two shots
    # and a patch of dungeon wall passes it easily — the two shots are of different places,
    # so anything in them differs. That is how a photograph of rock was stored as the HUD
    # template at 1920x1080: it separated the shots perfectly and matched 13 frames in 2341.
    if profile.hud_region:
        straight = hud_straightness(walk_image, tuple(profile.hud_region))
        if straight < HUD_STRAIGHTNESS_MIN:
            print(f"[!] the region found for the minimap does not look like a panel edge "
                  f"(straightness {straight:.2f}, expected {HUD_STRAIGHTNESS_MIN:.2f}+).")
            print("    Check hud_template.png: it should show part of the minimap's frame, "
                  "not scenery.")
            print("    If it shows a wall, take the walk shot with the minimap OPEN.")

    det = HudDetector.from_profile(profile)
    walk_score = det.read(walk_image.convert("L")).score
    drop_score = det.read(drop_image.convert("L")).score
    print(f"[*] HUD check: walk shot {walk_score:+.3f}, drop shot {drop_score:+.3f}")
    if walk_score - drop_score < 0.3:
        print("[!] these two shots look alike to the HUD detector. The walk shot should show "
              "the minimap and the drop shot should not — otherwise chests will never be "
              "bracketed. Re-run calibrate and check each capture.")


def _drop_shot_problem(image):
    from .calibration import find_text_bands

    if not find_text_bands(image.convert("L")):
        return "no message line found — is a 「獲得了…」 drop message on screen?"
    return None


def cmd_calibrate(cfg: ClientConfig, args) -> int:
    from PIL import Image

    from .calibration import fit_hud, fit_message_profile

    vocab, fmt, raw = _load_vocab(args)
    names = [e.name for e in vocab.entries]
    prefix = _prefix_from(fmt)

    if not args.drop_shot:
        # Guided capture: the client takes the screenshots itself, which also removes the
        # commonest setup mistake -- a scaled or cropped screenshot, whose geometry no longer
        # matches the live window.
        from .capture.window import find_window

        win = find_window()
        print(f"[*] game window: {win.width}x{win.height} ({win.process or 'unknown process'})")
        walk_path = _data_path(args, "walk.png")
        drop_path = _data_path(args, "drop.png")
        walk = _guided_shot(
            "STEP 1/2 — stand in a dungeon with the minimap visible top-right.",
            _walk_shot_problem, args.delay, walk_path)
        drop = _guided_shot(
            "STEP 2/2 — open a chest and leave a 「獲得了…」 message on screen.",
            _drop_shot_problem, args.delay, drop_path)
        if drop is None:
            raise SystemExit("[!] no drop message captured; cannot calibrate.")
        if not args.name:
            print("\n  Which item does that message name?")
            print(f"  (open {drop_path} if you need to read it)")
            args.name = input("  > ").strip()
        if not args.name:
            raise SystemExit("[!] the item name is needed as calibration's answer key.")
        args.drop_shot, args.walk_shot = str(drop_path), (str(walk_path) if walk else None)

    shot = Image.open(args.drop_shot)
    print(f"[*] fitting from {args.drop_shot} ({shot.size[0]}x{shot.size[1]}), name={args.name!r}")
    profile = fit_message_profile(
        shot, args.name, prefix, _font_candidates(args), names, locale=args.locale
    )
    print(f"[+] band={profile.message_band} font={Path(profile.font_path).name} "
          f"size={profile.font_size}px offset={profile.offset} "
          f"spacing={profile.letter_spacing:+.1f} score={profile.calibration_score:.3f}")
    print(f"[+] self-check: {profile.notes}")

    if args.walk_shot:
        tpl = _data_path(args, "hud_template.png")
        walk_image = Image.open(args.walk_shot)
        profile = fit_hud(profile, walk_image, template_path=tpl)
        print(f"[+] HUD template captured -> {tpl}")
        _check_hud_separation(profile, walk_image, shot)
    else:
        print("[!] no --walk-shot: HUD detection disabled, so dive/episode bracketing will not work")

    from .calibration import ProfileStore

    root = _data_path(args, PROFILE_NAME).parent
    store = ProfileStore.load(root)
    store.put(profile)
    store.save(root)
    # The single-profile file is still written, so anything reading it keeps working.
    profile.save(_data_path(args, PROFILE_NAME))
    key = ProfileStore.key_for(profile.frame_size)
    print(f"[+] profile saved for {key} -> {root / ProfileStore.FILENAME}")
    if len(store) > 1:
        print(f"    calibrated resolutions: {', '.join(store.keys())}")
    return 0


def _prefix_from(fmt) -> str:
    """Template text before {0}: 獲得了 for zh_tw, empty for name-first locales."""
    import re

    tpl = fmt.raw.get("drop_item") or "{0}"
    clean = re.sub(r"<[^>]+>", "", re.sub(r"^Msg@", "", tpl))
    return clean.split("{0}")[0]


def _peek_size(spec: str):
    """Frame size of a recording, so the matching calibration can be chosen."""
    from PIL import Image

    path = Path(str(spec).rstrip("/\\"))
    if path.is_dir():
        frames = sorted(path.glob("**/*.png"))
        if frames:
            with Image.open(frames[0]) as im:
                return im.size
    elif path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}:
        with Image.open(path) as im:
            return im.size
    return None


def _fps_for(args) -> float:
    """Use the rate the recording was CAPTURED at, unless one was given explicitly.

    Replaying at a different rate silently rescales every timestamp, because
    `elapsed_seconds` is frame index / fps -- and elapsed time is the variable the study
    turns on. Recordings made before manifests existed fall back to the default, with a
    warning, since their true rate is unknowable.
    """
    manifest_fps = None
    src = getattr(args, "source", None)
    if src:
        mf = Path(str(src).rstrip("/\\")) / "session.json"
        if mf.exists():
            try:
                manifest_fps = json.loads(mf.read_text(encoding="utf-8")).get("fps")
            except Exception:
                manifest_fps = None

    if getattr(args, "fps", None) is not None:
        if manifest_fps and abs(float(manifest_fps) - float(args.fps)) > 1e-6:
            # Overriding is allowed, but it rescales every timestamp: elapsed_seconds is
            # frame index / fps. Silently accepting the wrong rate would corrupt the study's
            # independent variable.
            print(f"[!] --fps {args.fps} overrides this recording's actual rate of "
                  f"{manifest_fps} fps. Elapsed times will be scaled by "
                  f"{manifest_fps / args.fps:.2f}x. Drop --fps to use the recorded rate.")
        return args.fps
    if manifest_fps:
        print(f"[*] using the recording's own rate: {manifest_fps} fps")
        return float(manifest_fps)
    print(f"[!] this recording has no session.json; assuming {LIVE_DEFAULT_FPS:g} fps. "
          "Timestamps will be wrong if it was captured at a different rate.")
    return LIVE_DEFAULT_FPS


def _session_record_dir(base):
    """A fresh subdirectory per session.

    Frames are numbered from 1 each run, so recording twice into the same directory
    OVERWRITES and interleaves the two sessions — which silently produced a recording that
    was two runs mixed together, and a replay that disagreed with the live run it was
    supposed to reproduce. One directory per session makes that impossible.
    """
    if not base:
        return None
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Path(base) / f"session-{stamp}"


def _select_profile(args, size=None):
    """Pick the calibration for this resolution.

    Choosing by size rather than by "the last one saved" is what lets windowed and fullscreen
    coexist -- previously switching between them meant recalibrating and discarding the other
    fit, and running with the wrong one fails on a resolution mismatch.
    """
    from .calibration import Profile, ProfileStore

    root = _data_path(args, PROFILE_NAME).parent
    store = ProfileStore.load(root)
    shipped = ProfileStore.shipped()
    if size is not None:
        # The player's own fit first: they calibrated against their machine, and a shipped
        # one is a stand-in for a step they have not had to take.
        chosen = store.get(size) or shipped.get(size)
        if chosen is not None:
            if store.get(size) is None:
                log.info("wddrop: using the calibration shipped for %s",
                         ProfileStore.key_for(size))
            return chosen
        raise SystemExit(
            f"[!] no calibration for {ProfileStore.key_for(size)}.\n"
            f"    Calibrated here: {', '.join(store.keys()) or 'none'}\n"
            f"    Shipped: {', '.join(shipped.keys()) or 'none'}\n"
            f"    Run `wddrop calibrate` while the game is at this resolution; existing "
            f"calibrations are kept."
        )
    if not len(store):
        if len(shipped) == 1:
            return shipped.only()
        raise SystemExit(f"[!] no calibration found in {root}\n    Run `wddrop calibrate` first.")
    single = store.only()
    if single is not None:
        return single
    return Profile.load(_data_path(args, PROFILE_NAME))


def _live_size(args) -> tuple[int, int] | None:
    """The size live capture will actually produce, decided BEFORE the profile is chosen.

    Replay peeks its first frame (`_peek_size`) and picks the matching calibration. Live
    capture had no equivalent: it loaded profile.json — whichever resolution was calibrated
    LAST — and the mismatch only surfaced once the first frame arrived, as a warning, with
    the right calibration sitting in profiles.json all along. Reported from a real run:
    the window was found at 1920x1080, both resolutions were calibrated, and the 704x1241
    profile was used anyway.

    Returns None when the size cannot be known ahead of time, which restores the old
    behaviour rather than refusing to start.
    """
    spec = getattr(args, "source", "") or ""
    try:
        if spec == "window" or spec.startswith("window:"):
            from .capture.window import find_window

            return find_window(spec.split(":", 1)[1] if ":" in spec else None).size
        if spec == "screen" or spec.startswith("screen:"):
            import mss

            index = int(spec.split(":", 1)[1]) if ":" in spec else 1
            with mss.mss() as sct:
                monitor = sct.monitors[index]
                return monitor["width"], monitor["height"]
    except (Exception, SystemExit):
        # Including SystemExit: `find_window` uses it to report "no game window", and that
        # is the source's error to raise properly a moment later, not this helper's.
        return None
    return None


def _build_runner(cfg: ClientConfig, args, size=None):
    from .calibration import Profile
    from .capture.episodes import EpisodeTracker
    from .capture.glyph import RenderRecognizer, centred_shifts, make_renderer
    from .capture.hud import HudDetector
    from .runner import CaptureRunner

    profile = _select_profile(args, size)
    vocab, fmt, raw = _load_vocab(args)
    prefix = _prefix_from(fmt)
    names = [e.name for e in vocab.entries]

    # `--fonts` overrides what the profile was calibrated against, which is how a font and
    # an atlas can be compared on the same recording. It was previously accepted here and
    # silently ignored, so a replay meant to test one renderer quietly used the other.
    font = _render_source_override(args) or profile.resolve_font(
        near=_data_path(args, PROFILE_NAME).parent)
    renderer = make_renderer(font, profile.font_size, tuple(profile.window),
                             getattr(profile, "letter_spacing", 0.0))
    recognizer = RenderRecognizer(
        renderer, prefix, names, shifts=centred_shifts(profile.offset, 1),
    )
    from .calibration import data_version
    from .capture.ocr import MessageFormat
    from .capture.pickaxe import PickaxeWatch
    from .labels import DungeonHints

    hints = DungeonHints.load(args.vocab)
    # Mining announces in a centred panel with its own wording, 「得到了{0}。」, so it gets its
    # own format and its own index over the same item names.
    mining_fmt = mining_prefix = None
    get_item = (raw.get("templates") or {}).get("get_item")
    if get_item:
        mining_fmt = MessageFormat(get_item, (raw["templates"] or {}).get("name_and_quantity"))
        mining_prefix = _prefix_from(mining_fmt)
    # The pickaxe messages are whole sentences, not "<prefix><item name>", so they get their
    # own tiny index with an EMPTY prefix rather than being bolted onto the item vocabulary.
    watch = PickaxeWatch.from_vocab(args.vocab)
    pickaxe_recognizer = RenderRecognizer(
        renderer, "", watch.candidates, shifts=centred_shifts(profile.offset, 1),
    ) if len(watch) else None
    hud = HudDetector.from_profile(profile)
    open_prompt = (raw.get("templates") or {}).get("open_prompt") or args.open_prompt
    # stable_frames=1: the RUNNER already refuses to recognise a band that has not held
    # still, so requiring the reader to see the same text twice as well would double-count
    # the same guarantee — and would discard lines recovered as they vanished, which are
    # seen exactly once by definition.
    tracker = EpisodeTracker(fmt, open_prompt, lambda obs: None, stable_frames=1)
    from .review import ReviewQueue

    queue = ReviewQueue.load(_data_path(args, "review.json"))
    runner = CaptureRunner(
        profile, recognizer, hud, tracker, message_format=fmt,
        renderer=renderer, prefix=prefix, review_queue=queue,
        record_dir=_session_record_dir(getattr(args, "record", None)),
        record_mode=getattr(args, "record_mode", "episodes"),
        dungeon_hints=hints,
        pickaxe_watch=watch,
        pickaxe_recognizer=pickaxe_recognizer,
        mining_names=names if mining_fmt else None,
        mining_render_source=font,
        mining_format=mining_fmt,
        mining_prefix=mining_prefix or "",
        pickaxes=getattr(args, "pickaxes", None),
        # What the panel fit is a claim ABOUT: this atlas rendering this vocabulary. A fit
        # carried across a data update would read plausible wrong names rather than fail.
        # `font` is the render source actually in use — the atlas, or a ttf. `--fonts` is
        # not consulted: it is required only by calibrate, and asking for it here made
        # replay and run exit outright.
        data_version=data_version(args.vocab, font),
        profile_path=_data_path(args, PROFILE_NAME),
    )
    # Before capture starts, so the cost lands while the window still says "Preparing…"
    # rather than on the player's first swing.
    runner.warm_mining_index(getattr(args, "dungeon", None))
    return runner, profile


def _describe(event: dict, hints=None) -> str:
    """One-line summary of a captured chest, for live feedback."""
    dive = event.get("dive") or {}
    parts = []
    for c in event["contents"]:
        label = f"{c['item_name']} x{c['quantity']}" + ("?" if c.get("qty_unknown") else "")
        if c.get("source_frame"):
            label += f" [{c['source_frame']}]"
        parts.append(label)
    items = ", ".join(parts) or "(empty chest)"
    if event.get("provenance") == "mining":
        unread = (event.get("qc") or {}).get("panel_lines_unread")
        line = (f"mined @{dive.get('elapsed_seconds')}s  {items}"
                + (f"  [!] {unread} line(s) on the panel could not be read" if unread else ""))
    else:
        line = f"chest #{dive.get('chest_index_in_dive')} @{dive.get('elapsed_seconds')}s  {items}"
    # A wrong dungeon is worse than a missing one — it moves the observation into another
    # dungeon's distribution — and it can only be fixed while the session is still running.
    if hints is not None:
        conflict = hints.describe_conflict(dive.get("dungeon_id"), event.get("qc") or {})
        if conflict:
            line += f"\n    {conflict}"
    return line


def _capture_strips(profile, record: bool, mining: bool = True):
    """Only the regions actually read, unless frames are being recorded.

    Recording needs whole frames to be replayable; live capture does not, and grabbing a few
    strips instead of the whole window is what makes a high sample rate affordable.

    THE STRIPS ARE THE READABLE WORLD. Everything outside them is composited as black, so a
    region left out here is not "read less accurately" — it does not exist. Mining reports in
    a centred panel, and leaving that band out meant mining could never be detected in a live
    session at all, while working perfectly on a recording (which has whole frames). It cost
    a real session to find, and it is the reason this function takes `mining` rather than
    quietly assuming the message band is the only place the game speaks.
    """
    if record:
        return None
    top, bottom = tuple(profile.message_band)
    w, h = tuple(profile.frame_size)
    strips = [(0, max(0, top - 4), w, min(h, bottom + 4) - max(0, top - 4))]
    if profile.hud_region:
        l, t, r, b = profile.hud_region
        strips.append((int(l * w), int(t * h), int((r - l) * w) + 1, int((b - t) * h) + 1))
    if mining:
        from .capture.panel import SEARCH_BOTTOM, SEARCH_TOP

        panel_top = int(h * SEARCH_TOP)
        strips.append((0, panel_top, w, int(h * SEARCH_BOTTOM) - panel_top))
    return strips


def cmd_run(cfg: ClientConfig, args) -> int:
    from .capture.source import open_source
    from .config import spool_path
    from .consent import require

    require(cfg.consent)
    # Validate the source BEFORE building the render index: that takes several seconds over
    # a few thousand candidates, and spending it only to report a bad path is pure friction.
    from .calibration import Profile

    # By the size the capture will actually produce, not by whichever calibration was saved
    # last — the two disagree the moment a player calibrates both windowed and fullscreen.
    live_size = _live_size(args)
    _profile = _select_profile(args, live_size)
    args.fps = args.fps or LIVE_DEFAULT_FPS
    if args.fps < MIN_RECOMMENDED_FPS:
        print(f"[!] --fps {args.fps:g} is below the recommended minimum of "
              f"{MIN_RECOMMENDED_FPS:g}. A drop message dismissed between samples is never "
              f"captured, and no replay or fix can recover it.")
    source = open_source(
        args.source, fps=args.fps,
        strips=_capture_strips(_profile, bool(getattr(args, "record", None))),
        expect_size=tuple(_profile.frame_size),
    )
    runner, profile = _build_runner(cfg, args, live_size)

    # Print each chest as it lands. Recording in silence gives the player no way to tell a
    # working session from a broken one until it is over — which is exactly how a live run
    # spent 182 frames looking like it had done nothing.
    spool = runner.on_event
    captured: list[dict] = []

    def announce(event: dict) -> None:
        captured.append(event)
        print(f"    {_describe(event, runner.dungeon_hints)}", flush=True)
        spool(event)

    runner.on_event = announce

    def on_pickaxe(kind: str, name: str, watch) -> None:
        from .capture.pickaxe import BROKE

        if kind == BROKE:
            print(f"    [pickaxe] {name} broke — {watch.total_broken} this session", flush=True)
        else:
            print("    [pickaxe] you have none left — restock in town before mining again",
                  flush=True)

    runner.on_pickaxe = on_pickaxe

    def on_mining(event: dict, left) -> None:
        items = ", ".join(
            f"{c['item_name']} x{c['quantity']}" + ("?" if c.get("qty_unknown") else "")
            for c in event["contents"])
        tail = "" if left is None else f"   ({left} pickaxe(s) left)"
        print(f"    mined  {items}{tail}", flush=True)

    runner.on_mining = on_mining
    print(f"[*] dungeon={args.dungeon} floor={args.floor} source={args.source}  (ctrl-c to stop)")
    try:
        stats = runner.run(source, dungeon_id=args.dungeon, floor_id=args.floor)
    except KeyboardInterrupt:
        stats = runner.stats
        print()
    # The source running out during a LIVE capture means the game window went away; only
    # the caller knows that, so the runner leaves it unset rather than guessing.
    reason = runner.stop_reason or "game_closed"
    from .config import records_path
    from .runner import record_stop_reason
    from .uploader import record_close

    record_stop_reason(runner.dive_id, reason)
    record_stop_reason(runner.dive_id, reason, records_path())
    # For whatever has already been uploaded, which the stamps above cannot reach.
    record_close(runner.dive_id, reason)
    print(f"[+] {stats}")
    print(f"[+] session ended: {reason}")
    if runner.pickaxes is not None and runner.pickaxes.summary():
        print(f"[+] {runner.pickaxes.summary()}")
    print(f"[+] {len(captured)} chest(s) this session -> {spool_path()}")
    if runner.record_dir is not None:
        print(f"[+] {stats.get('recorded', 0)} frame(s) recorded -> {runner.record_dir}")
        print(f"    Replay them with:  uv run wddrop.py replay {runner.record_dir} "
              f"--dungeon {args.dungeon} --fps {args.fps} --vocab {args.vocab}")
    if not captured:
        print("    No chests recorded. If that is wrong, run `wddrop probe` while a drop "
              "message is on screen.")
    return 0


def cmd_replay(cfg: ClientConfig, args) -> int:
    """Re-derive drops from recorded frames.

    Dry by default: prints what WOULD be recorded and writes nothing, so a replay can never
    quietly duplicate or corrupt the spool. `--spool` opts in to writing, which is what makes
    a recording an authoritative source — when a recognition bug is fixed, the data can be
    rebuilt from the frames instead of being re-collected in game.
    """
    from .capture.source import open_source
    from .config import spool_path

    fps = _fps_for(args)
    source = open_source(args.source, fps=fps)
    runner, profile = _build_runner(cfg, args, _peek_size(args.source))
    captured: list[dict] = []
    spool = runner.on_event
    if args.spool:
        runner.on_event = lambda e: (captured.append(e), spool(e))
    else:
        runner.on_event = captured.append
    stats = runner.run(source, dungeon_id=args.dungeon, floor_id=args.floor)
    print(f"[+] {stats}")
    for e in captured:
        print(f"    {_describe(e, runner.dungeon_hints)}")
    if not captured:
        print("    (no chests detected)")
    if args.spool:
        print(f"[+] {len(captured)} chest(s) written -> {spool_path()}")
    return 0


def cmd_probe(cfg: ClientConfig, args) -> int:
    """Capture one live frame and report what each detector sees.

    Exists because "0 chests" has several possible causes that look identical from the
    outside — wrong region, wrong resolution, HUD collapsed, nothing on screen. This saves
    the frame and the two crops so the actual cause is visible rather than guessed at.
    """
    import numpy as np
    from PIL import Image

    from .calibration import Profile
    from .capture.glyph import INK_LEVEL, anchor_window, ink_bbox
    from .capture.hud import HudDetector, crop_region
    from .capture.source import open_source

    profile = Profile.load(_data_path(args, PROFILE_NAME))
    frame = next(open_source(args.source, fps=1).frames()).image.convert("L")
    out = _data_path(args, "probe")
    out.mkdir(parents=True, exist_ok=True)
    frame.save(out / "frame.png")

    print(f"frame        {frame.size[0]}x{frame.size[1]}   profile {tuple(profile.frame_size)}")
    if tuple(frame.size) != tuple(profile.frame_size):
        print("  [!] RESOLUTION MISMATCH — nothing below will be meaningful")

    from .calibration import decode_template

    template = decode_template(profile)
    if profile.hud_region and template is not None:
        region = tuple(profile.hud_region)
        crop_region(frame, region).save(out / "hud_now.png")
        template.save(out / "hud_template.png")
        det = HudDetector.from_profile(profile)
        r = det.read(frame)
        print(f"HUD          score={r.score:+.3f}  threshold={det.threshold:.2f}  "
              f"-> {'PRESENT' if r.present else 'ABSENT'}")
        print(f"  region     {tuple(round(v, 4) for v in region)}  "
              f"= px {tuple(int(v * s) for v, s in zip(region, frame.size * 2))}")
        print(f"  compare    {out / 'hud_now.png'}  vs  {out / 'hud_template.png'}")
    else:
        print("HUD          NOT CALIBRATED — re-run `calibrate` with --walk-shot")

    top, bottom = tuple(profile.message_band)
    band = np.asarray(frame, dtype=np.uint8)[top:bottom, :]
    Image.fromarray(band).save(out / "band_now.png")
    ink = int((band > INK_LEVEL).sum())
    box = ink_bbox(band)
    print(f"message band y {top}-{bottom}   ink={ink}px   bbox={box}")
    window = anchor_window(frame, (top, bottom), tuple(profile.window))
    if window is None:
        print("  nothing to read (band is blank right now)")
    else:
        _, fmt, _ = _load_vocab(args)
        from .capture.glyph import RenderRecognizer, centred_shifts, make_renderer
        vocab, _, _ = _load_vocab(args)
        rec = RenderRecognizer(
            make_renderer(profile.resolve_font(near=_data_path(args, PROFILE_NAME).parent),
                          profile.font_size, tuple(profile.window),
                          getattr(profile, "letter_spacing", 0.0)),
            _prefix_from(fmt), [e.name for e in vocab.entries],
            shifts=centred_shifts(tuple(profile.offset), 1),
        )
        m = rec.recognize(window, observed_ink_width=(box[2] - box[0]) if box else None)
        print(f"  recognised {m.name!r}  score={m.score:.3f} margin={m.margin:.4f} "
              f"accepted={m.accepted} runner={m.runner_up!r}")
    print(f"\nsaved crops -> {out}")
    return 0


def cmd_windows(cfg: ClientConfig, args) -> int:
    """List visible windows, so the game's title can be found for --source window:<title>."""
    from .capture.window import list_windows

    for w in list_windows():
        print(f"  {w.width:>5}x{w.height:<5} at ({w.left:>5},{w.top:>5})  "
              f"{(w.process or '?'):<28} {w.title}")
    return 0


def cmd_verify(cfg: ClientConfig, args) -> int:
    """Walk recorded chests and record what they ACTUALLY contained.

    'y' confirms the reading; anything else lets the true item list be typed in.
    Confirmations are keyed by session + chest index, so they survive re-replaying the same
    recording after a recogniser change -- which is when they matter most.
    """
    from .capture.source import open_source
    from .verify import (CONFIRMED, CORRECTED, ChestTruth, TruthStore, parse_item,
                         parse_transcript, split_items)

    store = TruthStore.load(_data_path(args, "verified.json"))
    session = Path(args.source.rstrip("/\\")).name
    transcript = (parse_transcript(Path(args.transcript).read_text(encoding="utf-8"))
                  if getattr(args, "transcript", None) else None)
    source = open_source(args.source, fps=_fps_for(args))
    runner, _ = _build_runner(cfg, args, _peek_size(args.source))
    captured: list[dict] = []
    runner.on_event = captured.append
    runner.run(source, dungeon_id=args.dungeon, floor_id=args.floor)

    # Stated truth is checked against the closed vocabulary. A name that is not an item name
    # cannot be what the chest held, so it is either a typo or — the case that actually
    # happened — a list that was split on the wrong character, and either way it would be
    # recorded as ground truth and count a correct reading as wrong.
    known = {e.name for e in _load_vocab(args)[0].entries}

    def check(items: list[str]) -> None:
        unknown = [i for i in items if parse_item(i)[0] not in known]
        if unknown:
            print(f"    [!] not item names in this locale's vocabulary: {', '.join(unknown)}")

    print(f"\n{len(captured)} chest(s) read from {session}\n")
    for event in captured:
        event["session_label"] = session
        key = TruthStore.key_for(event)
        existing = store.get(key)
        from .verify import format_item

        read_items = [
            format_item(c["item_name"], None if c.get("qty_unknown") else c["quantity"])
            for c in event["contents"]
        ]
        print(f"  {key}  (@{(event.get('dive') or {}).get('elapsed_seconds')}s)")
        if not event["contents"]:
            print("    read: (nothing)")
        for c in event["contents"]:
            qty = f"x{c['quantity']}" + ("?" if c.get("qty_unknown") else "")
            # The frame is printed so a disagreement can be checked against the picture
            # immediately, rather than being remembered and hunted for afterwards.
            src = c.get("source_frame", "?")
            print(f"    - {c['item_name']:<24} {qty:<5} {src}")
        if existing and not args.again:
            print(f"    already {existing.verdict}; use --again to revisit\n")
            continue

        if transcript is not None:
            if key not in transcript:
                # Silence here would read as "the transcript agrees". It does not; it says
                # nothing about this chest, and the difference matters to a recall figure.
                print("    not in the transcript; left unverified\n")
                continue
            true_items = transcript[key]
            check(true_items)
            verdict = CONFIRMED if true_items == read_items else CORRECTED
            store.put(ChestTruth(key=key, session=session, verdict=verdict,
                                 read_items=read_items, true_items=true_items,
                                 verified_by=args.verified_by))
            store.save(_data_path(args, "verified.json"))
            print(f"    {verdict} ({args.verified_by})\n")
            continue

        answer = input("    correct? [y / n = type the real items / s = skip]: ").strip().lower()
        if answer == "s":
            print()
            continue
        if answer in ("y", "yes", ""):
            store.put(ChestTruth(key=key, session=session, verdict=CONFIRMED,
                                 read_items=read_items, true_items=list(read_items),
                                 verified_by=args.verified_by))
        else:
            # Separated by ';' rather than ',' because item names contain commas
            # (10,000拜恩紙幣) — see verify.ITEM_SEPARATOR.
            print("    Enter the true items, separated by ';', as \"name xN\"")
            print("    (omit xN if the game showed no number, or type (nothing)):")
            typed = input("    > ").strip()
            true_items = split_items(typed)
            check(true_items)
            store.put(ChestTruth(key=key, session=session, verdict=CORRECTED,
                                 read_items=read_items, true_items=true_items,
                                 verified_by=args.verified_by))
        store.save(_data_path(args, "verified.json"))
        print()

    print(json.dumps(store.accuracy(), indent=1))
    return 0


def cmd_accuracy(cfg: ClientConfig, args) -> int:
    """How often the client is right, measured against confirmed chests."""
    from .verify import TruthStore

    store = TruthStore.load(_data_path(args, "verified.json"))
    if not len(store):
        print("nothing verified yet -- run `wddrop verify <recording>` first")
        return 0
    print(json.dumps(store.accuracy(), indent=1))
    print()
    for t in store.all():
        if t.missed or t.spurious or t.wrong_quantity:
            print(f"  {t.key} ({t.session})")
            if t.missed:
                print(f"      MISSED   : {', '.join(t.missed)}")
            if t.spurious:
                print(f"      SPURIOUS : {', '.join(t.spurious)}")
            if t.wrong_quantity:
                print(f"      QUANTITY : {'; '.join(t.wrong_quantity)}")
    return 0


def cmd_drops(cfg: ClientConfig, args) -> int:
    """Print the local spool — everything recorded and not yet uploaded."""
    from .config import spool_path

    path = spool_path()
    if not path.exists():
        print(f"nothing recorded yet ({path})")
        return 0
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for ln in lines:
        try:
            print(f"  {_describe(json.loads(ln))}")
        except Exception as exc:
            print(f"  [!] unreadable spool line: {exc}")
    print(f"\n{len(lines)} chest(s) in {path}")
    return 0


def cmd_upload(cfg: ClientConfig, args) -> int:
    mode = CaptureMode.OCR
    print(json.dumps(upload_spool(cfg, mode), indent=1))
    return 0


def cmd_ui(cfg: ClientConfig, args) -> int:
    """Open the window.

    PySide6 is imported HERE rather than at module scope so the CLI keeps working on a
    machine without it — the capture path must never depend on a GUI toolkit being
    installable.
    """
    try:
        from .ui import main as ui_main
    except ImportError as exc:
        raise SystemExit(
            f"[!] the window needs PySide6, which is not installed ({exc}).\n"
            f"    Install it with:  uv run --with PySide6-Essentials wddrop.py ui\n"
            f"    Everything the window does is also available as a command; see --help."
        ) from exc
    return ui_main([])


def cmd_whoami(cfg: ClientConfig, args) -> int:
    print(f"install_id = {cfg.install_id}")
    print("提出此識別碼即可要求刪除你的所有資料。/ Quote this id to request erasure.")
    return 0


# -- entry point -----------------------------------------------------------------
def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="wddrop")
    ap.add_argument("--data", help="state directory (default: per-user config dir)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("consent")
    sub.add_parser("ui")
    sub.add_parser("whoami")
    sub.add_parser("windows")
    sub.add_parser("drops")
    sub.add_parser("accuracy")
    q = sub.add_parser("probe")
    q.add_argument("--source", default="window")
    q.add_argument("--vocab", default="vocab.zh_tw.json")
    q.add_argument("--locale", default="zh_tw")

    p = sub.add_parser("dungeons"); p.add_argument("--catalog", default="catalog.zh_tw.json")
    p.add_argument("--floors", action="store_true")

    for name in ("calibrate", "run", "replay", "verify"):
        q = sub.add_parser(name)
        q.add_argument("--vocab", default="vocab.zh_tw.json")
        q.add_argument("--locale", default="zh_tw")
        if name == "calibrate":
            q.add_argument("--drop-shot", help="screenshot showing a drop message "
                                               "(omit to capture it live, guided)")
            q.add_argument("--name", help="the item name in that screenshot")
            q.add_argument("--walk-shot", help="screenshot while walking (for the HUD template)")
            q.add_argument("--delay", type=float, default=4.0,
                           help="seconds to switch back to the game before each capture")
            q.add_argument("--fonts", required=True,
                           help="glob for the game's fonts, or an atlas.<locale>.json")
        else:
            q.add_argument("--dungeon", type=int, required=True)
            q.add_argument("--floor", type=int, default=None)
            # 8 rather than 4: a player advancing dialogue quickly can show a line for well
            # under 250ms, and a line that is never sampled cannot be recovered by any
            # amount of cleverness downstream. The per-frame cost is now an ink count and a
            # hash, so sampling faster is cheap; only genuinely new lines pay for matching.
            # 20 by default, from field testing. A line dismissed faster than one sample
            # interval is never seen at all, and nothing downstream can recover it -- a real
            # chest lost its first item to exactly this at 8fps. Live capture grabs only the
            # two strips it reads (2.8% of the pixels), so the rate is cheap.
            q.add_argument("--fps", type=float, default=None,
                           help=f"sampling rate (live default {LIVE_DEFAULT_FPS}); for "
                                "replay/verify it defaults to the recording's own rate")
            q.add_argument("--open-prompt", default="打開")
            q.add_argument("--pickaxes", type=int, default=None, metavar="N",
                           help="how many pickaxes you are diving with. Counted down as you "
                                "mine, so you know when to restock; never uploaded.")
            q.add_argument("--fonts", default=None,
                           help="render with this font or atlas instead of the calibrated "
                                "one (exactly one file)")
            if name == "run":
                q.add_argument("--source", default="window",
                               help="window | window:<title> | screen | screen:N | path")
                q.add_argument("--record", metavar="DIR",
                               help="save frames for offline replay (PNG, lossless)")
                q.add_argument("--record-mode", choices=("episodes", "all"),
                               default="episodes",
                               help="episodes = HUD-absent frames only (default)")
            else:
                q.add_argument("source")
                if name == "replay":
                    q.add_argument("--spool", action="store_true",
                                   help="write the results to the spool (default: dry run)")
                else:
                    q.add_argument("--again", action="store_true",
                                   help="revisit chests that were already confirmed")
                    q.add_argument("--transcript", metavar="FILE",
                                   help="confirm from a written record instead of the "
                                        "prompt: lines of 'session#N: item xA, item'")
                    q.add_argument("--verified-by", default="player", metavar="WHO",
                                   help="who is confirming (default: player). A later "
                                        "reading of the frames is not a player and is "
                                        "reported apart — e.g. --verified-by frames")

    sub.add_parser("upload")

    args = ap.parse_args(argv)
    cfg = ClientConfig.load()
    handlers = {
        "consent": cmd_consent, "dungeons": cmd_dungeons, "calibrate": cmd_calibrate,
        "run": cmd_run, "replay": cmd_replay, "upload": cmd_upload, "whoami": cmd_whoami,
        "windows": cmd_windows, "probe": cmd_probe, "drops": cmd_drops,
        "verify": cmd_verify, "accuracy": cmd_accuracy, "ui": cmd_ui,
    }
    try:
        return handlers[args.cmd](cfg, args)
    except ConsentRequired as exc:
        print(f"[!] {exc}\n    先執行 `wddrop consent`. / Run `wddrop consent` first.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
