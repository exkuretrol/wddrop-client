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

from .config import ClientConfig, config_dir, data_dir
from .items import droppable
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
    """The item names, found the way the WINDOW finds them.

    `--vocab` defaults to a bare `vocab.ja.json`, which resolves against the working
    directory — so the command line worked from the folder the client was unpacked into and
    nowhere else, while the window searched the program's folder, the data folder and the
    bundle. Same file, two ways of looking for it, and only one of them told you what it had
    tried.
    """
    from .capture.ocr import MessageFormat, Vocabulary

    path = Path(args.vocab)
    if not path.exists():
        from .ui import find_data

        found = find_data(path.name, getattr(args, "locale", None) or "ja")
        if found is not None:
            path = found
            # WRITTEN BACK, because this is not the only reader. The pickaxe messages, the
            # dungeon hints and the item index are all built from `args.vocab` further down,
            # and resolving it privately here left them opening the bare name — which fails
            # with a FileNotFoundError from somewhere that says nothing about vocabularies.
            args.vocab = str(path)
    if not path.exists():
        raise SystemExit(
            f"[!] vocabulary not found: {args.vocab}\n"
            f"    Looked beside the client and in your data folder ({data_dir()}).\n"
            f"    Name one with --vocab, or build it with tools/build_vocab.py")
    return Vocabulary.load(path), MessageFormat.from_vocab(path), json.loads(path.read_text(encoding="utf-8"))


def _font_candidates(args) -> list[str]:
    r"""What to render candidates with, defaulting to the atlas this client already built.

    `--fonts` used to be required, and that made the command line a trap: the client renders
    from an ATLAS built out of the player's own copy of the game, and nothing on the command
    line said so — so the obvious thing to pass is the extracted `fonts\*.ttf`, which is a
    different typeface from the one on screen. Measured on a real 1600x900 shot, calibrating
    with BaseFont_ChineseTraditional.ttf scored 0.547 and failed its own check; the atlas
    beside it fits the same shot at 0.823.

    The window has always passed the atlas. This makes the two agree.
    """
    import glob

    if args.fonts:
        return sorted(glob.glob(args.fonts))
    from .ui import find_data

    locale = getattr(args, "locale", None) or "ja"
    found = [find_data("atlas.{locale}.json", locale),
             find_data("atlas.{locale}.scenario.json", locale)]
    atlases = [str(p) for p in found if p is not None]
    if atlases:
        return atlases
    raise SystemExit(
        "[!] no atlas found for this locale, and --fonts was not given.\n"
        "    The client builds `atlas.{0}.json` from your copy of the game on first run —\n"
        "    open the window once, or pass --fonts pointing at one.".format(locale))


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


def _band_font_candidates(args) -> list[str]:
    """The faces to FIT the message band against, which are the ones it is drawn in.

    Calibration used to sweep whatever `--fonts` named, and the GUI names the panel's atlas
    there. Fitting the band against the panel's typeface produces a profile that scores
    badly and then fails its own self-check — measured on a real shot, 0.79 against the
    band's own face and 0.92 against the panel's, with the fit landing on a different size
    and spacing entirely. See _band_source for which face is which.
    """
    named = getattr(args, "band_fonts", None)
    if named:
        return [str(named)]
    # BOTH FACES, twin first, and the SCORE decides. The band is drawn in the scenario face
    # and the panel in the base one, but "which file is which" is a property of how the
    # atlas was built on this machine, and a calibration that guesses wrong produces a
    # profile that fails its own self-check with nothing saying why. Measured on a real
    # 1920x1080 shot, fitting 「モニヨン銀貨」 in the band:
    #
    #     scenario 22px -0.3   0.806      base 21px +1.0   0.759
    #     scenario 21px +1.0   0.758      base 22px -0.3   0.742
    #
    # Sweeping both costs one more pass over a handful of sizes and removes the guess.
    candidates = _font_candidates(args)
    twins = [_scenario_beside(path) for path in candidates]
    return list(dict.fromkeys([t for t in twins if t] + candidates))


def _band_source(args, panel_font: str) -> str:
    """The face the MESSAGE BAND is drawn in, which is not the one the mining panel uses.

    The game picks a typeface per UI element: every text object carries a serialized font
    name and `LocalizeFontManager.GetFont(language, name)` resolves it. The two surfaces this
    client reads are on opposite sides of that choice — measured against real frames, each
    face given its own best alignment:

        message band  「…を手に入れた!!」   ScenarioFont 0.83-0.91   BaseFont 0.69-0.84
        mining panel  「… を入手した」      BaseFont     0.59-0.76   ScenarioFont 0.51-0.64

    The client built one atlas, from BaseFont, and used it for both. The panel was right by
    accident; the band was reading every line against the wrong face. Worst line of five:
    0.695 -> 0.834 against a 0.60 gate, and 「100バイン紙幣」 stopped being refused outright.

    The calibration is unaffected — 25px at +1.1 spacing is the best fit for BOTH faces, so
    this swaps the atlas and nothing else. See the vault: Reference/UI Font System.

    Resolution order, and why:

    1. `band_fonts` on the args — the GUI names both atlases outright, because it already
       overrides `--fonts` to force the LOCALE's atlas over whatever locale the profile was
       fitted in, and that override must not also decide the band's face.
    2. `--fonts` alone pins BOTH, because comparing two renderers on one recording is exactly
       what that flag is for and silently rendering the band with a different file would
       break the one guarantee it makes.
    3. Otherwise: the scenario atlas beside the profile's, when it has been built.

    Falls back to the panel's face whenever the scenario atlas is missing, so a player who
    has not rebuilt their atlas is no worse off than before this existed.
    """
    explicit = getattr(args, "band_fonts", None)
    if explicit:
        return str(explicit)
    if _render_source_override(args):
        return panel_font
    return _scenario_beside(panel_font) or panel_font


def _scenario_beside(atlas: str) -> str | None:
    """The `.scenario` twin of an atlas path, if it is there."""
    path = Path(atlas)
    if path.suffix.lower() != ".json" or path.stem.endswith(".scenario"):
        return None
    twin = path.with_name(f"{path.stem}.scenario.json")
    return str(twin) if twin.exists() else None


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


# How many frames the walking shot actually takes, and how far apart. The extra ones are the
# only evidence that separates the minimap's chrome from its interior — see
# calibration.choose_hud_region. The gap is long enough for a walking player to have moved
# somewhere else and short enough that nobody notices they are being asked to keep walking.
WALK_BURST, WALK_GAP = 3, 0.8


def _grab_burst(delay: float, count: int = WALK_BURST, gap: float = WALK_GAP):
    """`count` frames of the game window, `gap` seconds apart, after one countdown."""
    import time

    frames = [_grab_window(delay)]
    for _ in range(count - 1):
        time.sleep(gap)
        frames.append(_grab_window(0))
    return frames


def _burst_paths(path: Path, count: int) -> list[Path]:
    """walk.png, walk.2.png, walk.3.png — the first keeps its name so nothing else moves."""
    return [path] + [path.with_name(f"{path.stem}.{i}{path.suffix}") for i in range(2, count + 1)]


def _guided_shot(what: str, check, delay: float, path: Path, burst: int = 1):
    """Prompt, capture, validate, and offer a retry until the shot is usable.

    Validating here rather than at fit time is the point: "your profile did not work" is a
    poor thing to discover after the fact, when the game state that produced it is gone.

    `burst` takes several frames a moment apart and returns them all. Only the first is
    validated — the others exist to be COMPARED with it, and a comparison is what says which
    part of the minimap panel is furniture and which part redraws.
    """
    while True:
        print(f"\n  {what}")
        answer = input("  ready? [ENTER = capture / s = skip]: ").strip().lower()
        if answer == "s":
            return None
        images = _grab_burst(delay, burst) if burst > 1 else [_grab_window(delay)]
        problem = check(images[0])
        if problem is None:
            for image, where in zip(images, _burst_paths(path, len(images))):
                image.save(where)
            print(f"    looks good -> {path}")
            return images if burst > 1 else images[0]
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
    """Print what `calibration.hud_findings` says about the pair, and the scores either way.

    The findings themselves live in `calibration` because the WINDOW needs them too, and for
    three versions it did not have them: a fit made there could carry a template cut from a
    wall and say nothing, which is the failure that records a whole dive as one chest.
    """
    from .calibration import hud_findings
    from .capture.hud import HudDetector

    detector = HudDetector.from_profile(profile)
    print(f"[*] HUD check: walk shot {detector.read(walk_image.convert('L')).score:+.3f}, "
          f"drop shot {detector.read(drop_image.convert('L')).score:+.3f}")
    for finding in hud_findings(profile, walk_image, drop_image):
        print(f"[!] {finding}")


def _drop_shot_problem(image):
    from .calibration import find_text_bands

    if not find_text_bands(image.convert("L")):
        return "no message line found — is a 「獲得了…」 drop message on screen?"
    return None


def cmd_calibrate(cfg: ClientConfig, args) -> int:
    from PIL import Image

    from .calibration import fit_hud, fit_message_profile

    vocab, fmt, raw = _load_vocab(args)
    # The same answer space the runner uses: calibration reads a real chest drop, so a name
    # that cannot come out of a chest cannot be the one in the shot either.
    names = droppable(vocab.entries)
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
        # KEEP WALKING, and it is not a nicety: the frames are compared with each other to
        # find the part of the minimap panel that does not change. Standing still makes every
        # band look stable, including the map interior, which is the one that must not be
        # matched. Calibration says so and falls back to the old rule if it happens anyway.
        walk = _guided_shot(
            "STEP 1/2 — walk around in a dungeon, minimap visible top-right, and KEEP WALKING "
            "for a few seconds after the countdown.",
            _walk_shot_problem, args.delay, walk_path, burst=WALK_BURST)
        # Quoted from the locale's OWN template rather than written out here: the wording
        # this asked for was Chinese whatever language the client was reading in, which is
        # the one instruction a player cannot follow by guessing.
        example = _clean_template(fmt).replace("{0}", "…")
        drop = _guided_shot(
            f"STEP 2/2 — open a chest and leave a 「{example}」 message on screen.",
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
        # THE BAND'S OWN FACE, not the panel's. `_band_font_candidates` was written for
        # exactly this and was never wired in here: calibration fitted the message band
        # against whatever `--fonts` named — the panel's atlas — scored badly, failed its
        # own self-check, and saved nothing. On a 1920x1080 shot that is the difference
        # between 0.806 and a fit the recogniser then refuses to read the frame with.
        shot, args.name, prefix, _band_font_candidates(args), names, locale=args.locale,
        suffix=_suffix_from(fmt), separator=_separator_from(raw),
    )
    print(f"[+] band={profile.message_band} font={Path(profile.font_path).name} "
          f"size={profile.font_size}px offset={profile.offset} "
          f"spacing={profile.letter_spacing:+.1f} score={profile.calibration_score:.3f}")
    print(f"[+] self-check: {profile.notes}")
    if (profile.notes or {}).get("name_ends_at") is None:
        # The game WRAPS a long message rather than clipping it, so the wording after the
        # name can be on a row this shot's band does not cover. The fit still passed — it is
        # checked against the name either way — but it was fitted against ink no candidate
        # covers, and letter spacing is the part of it that suffers.
        print("[!] that message wrapped onto a second line, so the fit had less to go on.")
        print("    It passed its own check. If readings look poor later, calibrate again on")
        print("    a chest whose whole sentence fits on one row.")

    if args.walk_shot:
        tpl = _data_path(args, "hud_template.png")
        # EVERY frame of the burst that is still on disk, not just the one named. A rerun with
        # `--walk-shot walk.png` finds walk.2.png and walk.3.png beside it and fits the way
        # the guided path does; a lone screenshot falls back to the density search.
        walking = [Image.open(p) for p in _burst_paths(Path(args.walk_shot), WALK_BURST)
                   if p.exists()]
        walk_image = walking[0]
        profile = fit_hud(profile, walking, template_path=tpl, absent=shot)
        print(f"[+] HUD template captured from {len(walking)} frame(s) -> {tpl}")
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


def _clean_template(fmt) -> str:
    import re

    tpl = fmt.raw.get("drop_item") or "{0}"
    return re.sub(r"<[^>]+>", "", re.sub(r"^Msg@", "", tpl))


def _prefix_from(fmt) -> str:
    """Template text before {0}: 獲得了 for zh_tw, empty for name-first locales."""
    return _clean_template(fmt).split("{0}")[0]


def _suffix_from(fmt) -> str:
    """Template text after {0}: 「！！」 for zh_tw, 「を手に入れた!!」 for ja.

    Not cosmetic — it is what the reader masks out and what the quantity reader anchors on,
    and it is the whole difference between the two locales' geometry.
    """
    clean = _clean_template(fmt)
    return clean.split("{0}")[1] if "{0}" in clean else ""


def _separator_from(raw: dict) -> str:
    """The 「×」 between a name and its quantity, as this locale's own template writes it."""
    import re

    tpl = (raw.get("templates") or {}).get("name_and_quantity") or "{0}×{1}"
    parts = re.split(r"\{\d\}", tpl)
    return parts[1] if len(parts) > 2 else "×"


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


def _session_record_dir(base, size=None):
    """`<capture>/<width>x<height>/session-<stamp>` — a fresh directory per session, under
    the resolution it was recorded at.

    Frames are numbered from 1 each run, so recording twice into the same directory
    OVERWRITES and interleaves the two sessions — which silently produced a recording that
    was two runs mixed together, and a replay that disagreed with the live run it was
    supposed to reproduce. One directory per session makes that impossible.

    THE RESOLUTION IS PART OF THE PATH because it is the first thing anyone asks of a
    recording. Everything about reading a frame is fitted per resolution — the band, the
    panel's box, the letter spacing, the HUD template — so a folder of sessions at mixed
    sizes is a folder that has to be opened one session.json at a time to be sorted, and
    every question about a fault ("does this happen at 704 as well?") starts with that
    sorting. Named with `ProfileStore.key_for`, so the folder and the calibration it belongs
    to are spelled the same way.
    """
    if not base:
        return None
    from datetime import datetime, timezone

    from .calibration import ProfileStore

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    root = Path(base)
    if size:
        root = root / ProfileStore.key_for(size)
    return root / f"session-{stamp}"


def _select_profile(args, size=None):
    """Pick the calibration for this resolution.

    Choosing by size rather than by "the last one saved" is what lets windowed and fullscreen
    coexist -- previously switching between them meant recalibrating and discarding the other
    fit, and running with the wrong one fails on a resolution mismatch.
    """
    from .calibration import Profile, ProfileStore

    root = _data_path(args, PROFILE_NAME).parent
    store = ProfileStore.load(root)
    # For the language THIS run reads in: a fit made for another one names an atlas that is
    # not here, and would fail at the font rather than at the choice.
    shipped = ProfileStore.shipped(getattr(args, "locale", None))
    if size is not None:
        # The player's own fit first: they calibrated against their machine, and a shipped
        # one is a stand-in for a step they have not had to take.
        # THE SHIPPED FIT WINS where there is one. It is the one that has been checked
        # against recordings; a fit made on a player's machine is a claim nobody has
        # verified, and calibration is no longer offered to players precisely because of
        # that. Their own fit is still used for a size the shipped set does not cover, which
        # is the case it exists for.
        #
        # The old order — theirs first — meant a stale profile silently outranked a better
        # one shipped later, with nothing to say so. Measured on the player this was written
        # for: their 24px/+2.0 fit stayed in use after 25px/+1.1 shipped, and mining recorded
        # NOTHING for two more sessions because the panel's spacing sweep is relative to the
        # band's. Upgrading the client could not fix it; only deleting the file could.
        mine, baked = store.get(size), shipped.get(size)
        chosen = baked or mine
        if chosen is not None:
            if baked is not None and mine is not None:
                # WHAT THE SHIPPED FIT DOES NOT SAY, the local one may still answer. The
                # shipped fit replaces the player's for everything it states — that is the
                # point of it winning — but the mining panel's geometry is LEARNED at run
                # time, on their machine, from a panel nobody could photograph for a fit. If
                # it were dropped with the rest, every session would re-fit the panel and
                # then save the answer into an entry that is outranked, so the same seconds
                # would be spent again on the next run and forever after.
                #
                # Gaps only: a value the shipped fit carries is never overwritten.
                for field in ("panel_font_size", "panel_letter_spacing", "panel_font_path",
                              "panel_data_version"):
                    learned = getattr(mine, field, None)
                    if learned is not None and getattr(baked, field, None) is None:
                        setattr(baked, field, learned)
                log.info("wddrop: using the shipped calibration for %s, not the one fitted "
                         "here (%dpx %+.1f vs %dpx %+.1f)", ProfileStore.key_for(size),
                         baked.font_size, baked.letter_spacing,
                         mine.font_size, mine.letter_spacing)
            elif baked is not None:
                log.info("wddrop: using the calibration shipped for %s",
                         ProfileStore.key_for(size))
            return chosen
        # THE FRAME MAY BE AN ENLARGEMENT OF ONE OF THEM. The game can only ask Unity for
        # borderless fullscreen, so a 1920x1080 game fills a 2560x1440 or 4K desktop by being
        # scaled up — and that is the same picture, not a new resolution to calibrate. See
        # calibration.scaled_from for why it cannot be turned off, and the measured cost.
        from .calibration import scaled_from

        match = scaled_from(size, set(store.sizes()) | set(shipped.sizes()))
        if match is not None:
            source_size, scale = match
            picked = shipped.get(source_size) or store.get(source_size)
            if picked is not None:
                log.info("wddrop: capturing %s, reading it at %s (scaled by %.3fx). The game "
                         "can only do BORDERLESS fullscreen, so the window is the desktop's "
                         "size while the render is not.",
                         ProfileStore.key_for(size), ProfileStore.key_for(source_size), scale)
                _warn_if_the_game_renders_smaller(source_size)
                return picked
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


def _warn_if_the_game_renders_smaller(reading_at) -> None:
    """The one thing resampling cannot fix, said out loud.

    The layout is right whatever the game renders at — the render resolution cancels out of
    `units * height / 1920` when the compositor stretches it to the display. The INK does
    not: a 1280x720 render enlarged to 1440p and resampled back scored min 0.5626 over the
    same 15 confirmed lines that a 1920x1080 render scored 0.8473 on, and 0.5626 is under
    the 0.60 gate — i.e. one reading lost, silently, because that is what an under-gate line
    is meant to be.
    """
    try:
        from .capture.window import rendered_resolution

        rendered = rendered_resolution()
    except Exception:                                  # noqa: BLE001 — a hint, never a blocker
        return
    if not rendered:
        return
    if rendered[1] < reading_at[1]:
        print(f"[!] the game is rendering {rendered[0]}x{rendered[1]} and its picture is "
              f"being read at {reading_at[0]}x{reading_at[1]}.")
        print("    Enlarging cannot put back ink that was never drawn, and a line that "
              "falls under the")
        print(f"    recogniser's gate is DROPPED rather than guessed. Set the game's own "
              f"resolution to at least {reading_at[0]}x{reading_at[1]}.")
    else:
        log.info("wddrop: the game renders %dx%d, read at %dx%d", *rendered, *reading_at)


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
    from .items import ItemIndex
    from .runner import CaptureRunner

    profile = _select_profile(args, size)
    vocab, fmt, raw = _load_vocab(args)
    prefix = _prefix_from(fmt)
    # WHAT A DUNGEON CAN HAND YOU, not the whole vocabulary: 2,154 names of 3,268. The rest
    # is weight — rendered once, held in memory, and correlated against every line — and an
    # answer space a third larger is also a third more chances for a wrong name to win.
    #
    # Safe because the two places the client reads are two specific game strings:
    # `DungeonTreasure@DropItem` in the band and `Common@GetItem` in the mining panel. A
    # quest reward is `Scenario@ObtainItemGet` and reaches neither. See
    # items.NOT_FROM_A_DUNGEON, and note that every item ever recorded — 75 records over
    # nine sessions, chests and veins — survives this filter.
    names = droppable(vocab.entries)

    # `--fonts` overrides what the profile was calibrated against, which is how a font and
    # an atlas can be compared on the same recording. It was previously accepted here and
    # silently ignored, so a replay meant to test one renderer quietly used the other.
    font = _render_source_override(args) or profile.resolve_font(
        near=_data_path(args, PROFILE_NAME).parent)
    # The band and the panel are drawn in DIFFERENT faces — see _band_source. `font` stays
    # the panel's (and the profile's) source; the band gets its own.
    band_font = _band_source(args, font)
    renderer = make_renderer(band_font, profile.font_size, tuple(profile.window),
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
        record_dir=_session_record_dir(getattr(args, "record", None), profile.frame_size),
        record_mode=getattr(args, "record_mode", "episodes"),
        dungeon_hints=hints,
        pickaxe_watch=watch,
        item_index=ItemIndex.from_vocab(raw),
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
        # THE VOCABULARY, not the atlas. The stamp exists so a panel fit made against one
        # set of game data is not reused against another — but the atlas is built on the
        # player's own machine from their own copy of the font, so its bytes differ from
        # everyone else's while describing the same game. Including it meant a fit baked into
        # profiles.shipped.json could never match on any machine but the one that made it:
        # the geometry was carried, discarded on load, and searched for again every session.
        # Measured — shipped stamp c64f60c4..., the same data on the player's machine
        # 93733e8f... The vocabulary is the thing that actually says which game data this is.
        data_version=data_version(args.vocab),
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
    """The regions live capture grabs — defined in `preview`, so the picture the window can
    draw of them and the pixels this actually asks for are the same list.

    A preview that keeps its own idea of the regions is a picture that can disagree with the
    program while looking authoritative, which is worse than not having one.
    """
    from .preview import strips

    return strips(profile, record, mining)


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
        # WHAT THE WINDOW IS, not what the calibration wants it to be: in borderless
        # fullscreen those differ, and matching a window by the calibrated size would then
        # rule out the only window there is.
        expect_size=live_size or tuple(_profile.frame_size),
        profile_size=tuple(_profile.frame_size),
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
    from .capture.source import _to_profile, open_source
    from .config import spool_path

    fps = _fps_for(args)
    source = open_source(args.source, fps=fps)
    runner, profile = _build_runner(cfg, args, _peek_size(args.source))
    # A recording made on a screen larger than the calibration — see calibration.scaled_from.
    # Wrapped here rather than at open_source so a bad path is still reported before the
    # render index is built.
    source = _to_profile(source, tuple(profile.frame_size))
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

    from .preview import annotate, as_capture_sees

    profile = Profile.load(_data_path(args, PROFILE_NAME))
    frame = next(open_source(args.source, fps=1).frames()).image.convert("L")
    out = _data_path(args, "probe")
    out.mkdir(parents=True, exist_ok=True)
    frame.save(out / "frame.png")
    # THE TWO PICTURES, because the crops below answer neither question on their own: they
    # show what each region CONTAINS, and say nothing about where it sits or whether capture
    # grabs it at all. A HUD template that is a photograph of a rock face looks like a
    # perfectly good crop; it is only obviously wrong once the box is drawn on the frame.
    annotate(frame, profile).save(out / "regions.png")
    as_capture_sees(frame, profile).save(out / "as_captured.png")

    print(f"frame        {frame.size[0]}x{frame.size[1]}   profile {tuple(profile.frame_size)}")
    if tuple(frame.size) != tuple(profile.frame_size):
        print("  [!] RESOLUTION MISMATCH — nothing below will be meaningful")
    print(f"  look at     {out / 'regions.png'}      where it looks")
    print(f"              {out / 'as_captured.png'}  what it gets, live")

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
            _prefix_from(fmt), droppable(vocab.entries),
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


def _against_what_was_confirmed(existing, read_items) -> tuple[bool, list[str]]:
    """What you already said it was, beside what the client says now.

    Only the second of those was printed. So re-running `verify` over a session that had
    been confirmed answered the wrong question: it showed the NEW reading, said "already
    confirmed", and never compared the two — while a confirmed session is the only ground
    truth this project has, and re-reading it is how a change to the reader gets checked.

    Returns (they agree, lines to print). The stored verdict and who gave it come along
    because "confirmed by me last week" and "corrected from a transcript" are not the same
    evidence.
    """
    same = list(existing.true_items) == list(read_items)
    lines = [f"    you confirmed: {'; '.join(existing.true_items) or '(nothing)'}"
             f"   [{existing.verdict}, {existing.verified_by or 'unknown'}]"]
    if same:
        lines.append("    the client reads the same thing now")
    else:
        lines.append(f"    ** DIFFERS ** it now reads: "
                     f"{'; '.join(read_items) or '(nothing)'}")
    return same, lines


def cmd_verify(cfg: ClientConfig, args) -> int:
    """Walk recorded chests and record what they ACTUALLY contained.

    'y' confirms the reading; anything else lets the true item list be typed in.
    Confirmations are keyed by session + chest index, so they survive re-replaying the same
    recording after a recogniser change -- which is when they matter most.
    """
    from .capture.source import _to_profile, open_source
    from .verify import (CONFIRMED, CORRECTED, ChestTruth, TruthStore, parse_item,
                         parse_transcript, split_items)

    store = TruthStore.load(_data_path(args, "verified.json"))
    session = Path(args.source.rstrip("/\\")).name
    transcript = (parse_transcript(Path(args.transcript).read_text(encoding="utf-8"))
                  if getattr(args, "transcript", None) else None)
    source = open_source(args.source, fps=_fps_for(args))
    runner, verify_profile = _build_runner(cfg, args, _peek_size(args.source))
    source = _to_profile(source, tuple(verify_profile.frame_size))
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

    # A PICKAXE BREAK IS A READING, and the one with the most to lose from being wrong: a
    # false break spends a pickaxe the player still has. It is not on the event stream — the
    # wire has no provenance for it — so it is merged in here, in time order, and confirmed
    # like anything else. Without this the tool that exists to check readings could not see
    # the readings hardest to trust.
    readings = sorted(captured + list(getattr(runner, "pickaxe_events", [])),
                      key=lambda e: (e.get("dive") or {}).get("elapsed_seconds") or 0)
    # How this run compares with what was confirmed BEFORE it — the only regression figure
    # there is, since the store's own accuracy is what the reader scored on the day it was
    # verified rather than what it scores now.
    agreed = differed = 0
    chests = sum(1 for e in readings if e.get("provenance") != "pickaxe_break")
    breaks = len(readings) - chests
    print(f"\n{chests} reading(s) and {breaks} pickaxe break(s) from {session}\n")
    for event in readings:
        event["session_label"] = session
        key = TruthStore.key_for(event)
        existing = store.get(key)
        from .verify import format_item

        read_items = [
            format_item(c["item_name"], None if c.get("qty_unknown") else c["quantity"])
            for c in event["contents"]
        ]
        kind = ("pickaxe broke" if event.get("provenance") == "pickaxe_break"
                else "vein" if event.get("provenance") == "mining" else "chest")
        print(f"  {key}  {kind}  (@{(event.get('dive') or {}).get('elapsed_seconds')}s)")
        if not event["contents"]:
            print("    read: (nothing)")
        for c in event["contents"]:
            qty = f"x{c['quantity']}" + ("?" if c.get("qty_unknown") else "")
            # The frame is printed so a disagreement can be checked against the picture
            # immediately, rather than being remembered and hunted for afterwards.
            src = c.get("source_frame", "?")
            print(f"    - {c['item_name']:<24} {qty:<5} {src}")
        if existing:
            same, lines = _against_what_was_confirmed(existing, read_items)
            agreed += same
            differed += not same
            for line in lines:
                print(line)
            # THE REPLAY CANNOT BE NARROWED — every frame has to be read again to produce
            # these readings at all — but the QUESTIONS can, and a session is 14 of them.
            # `--again` asks about all of them; `--differing` asks only where the client now
            # says something other than what was confirmed, which is the case that actually
            # needs a person: either the reader improved and the old answer was wrong, or it
            # regressed and the old answer still stands. Nobody should have to re-answer
            # thirteen agreements to reach the one disagreement.
            ask = args.again or (getattr(args, "differing", False) and not same)
            if not ask:
                print("    use --again to change what you confirmed"
                      + ("" if same else ", or --differing to be asked only about these")
                      + "\n")
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

        prompt = ("    did this pickaxe really break? [y / n = it did not / s = skip]: "
                  if event.get("provenance") == "pickaxe_break"
                  else "    correct? [y / n = type the real items / s = skip]: ")
        answer = input(prompt).strip().lower()
        if answer == "s":
            print()
            continue
        if answer in ("y", "yes", ""):
            store.put(ChestTruth(key=key, session=session, verdict=CONFIRMED,
                                 read_items=read_items, true_items=list(read_items),
                                 verified_by=args.verified_by))
        elif event.get("provenance") == "pickaxe_break":
            # NOT ASKED TO TYPE ANYTHING. There is one claim here and it is either true or
            # false, so "n" states the whole correction: nothing broke.
            store.put(ChestTruth(key=key, session=session, verdict=CORRECTED,
                                 read_items=read_items, true_items=[],
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

    if agreed or differed:
        print(f"against what was already confirmed: {agreed} match, {differed} differ"
              + ("" if not differed else "   <- look at these") + "\n")
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


def cmd_atlas(cfg: ClientConfig, args) -> int:
    """Render the atlas from a font on THIS machine.

    The recogniser compares rendered candidates against the screen, so it needs the typeface
    the game draws with — and that typeface is licensed, so the client does not carry it.
    Building it here means nothing font-derived is ever distributed: the glyphs are made on
    the machine that already has the font.

    Which font is the player's choice, and it decides how well this works. The game's own
    face reads everything; another face reads the names but misses some, and leaves more
    quantities unread — see the report this prints, and `--verify` on a real frame.
    """
    from .atlas import build

    vocab_path = _data_path(args, f"vocab.{cfg.locale}.json")
    if not Path(vocab_path).exists():
        raise SystemExit(f"[!] no vocabulary at {vocab_path}\n"
                         f"    The atlas is built from the names it has to draw.")
    font, fallbacks = _atlas_fonts(args)
    # THE PLAYER'S folder, not the program's. `data_dir()` is where the atlases that SHIP
    # live, and writing there is wrong for one built here: the program folder is read-only
    # under Program Files, and inside a one-file exe it is a temporary directory that is
    # deleted on exit. This one is theirs, made from their font, and rebuildable — which is
    # exactly what their own folder is for. `find_data` searches it either way.
    out = Path(args.out) if args.out else config_dir()
    vocab = json.loads(Path(vocab_path).read_text(encoding="utf-8"))
    result = build(Path(font), vocab, out, cfg.locale, fallbacks=fallbacks)
    print(f"[+] {result['glyphs']} glyphs from {Path(font).name} -> {result['meta'].name}"
          f" + {result['png'].name}  ({result['bytes'] / 1_048_576:.1f} MB)")
    # The SECOND face. The mining panel and the drop message band are drawn in different
    # typefaces (see _band_source), so one atlas cannot serve both — and the one this
    # command has always built is the panel's.
    scenario = next((p for p in [Path(font), *fallbacks]
                     if "ScenarioFont" in p.name), None)
    if scenario is not None and not getattr(args, "font", None):
        rest = [p for p in [Path(font), *fallbacks] if p != scenario]
        second = build(scenario, vocab, out, cfg.locale, fallbacks=rest,
                       stem=f"{cfg.locale}.scenario")
        print(f"[+] {second['glyphs']} glyphs from {scenario.name} -> {second['meta'].name}"
              f" + {second['png'].name}  (the message band reads against this one)")
    if result["fallbacks"]:
        print(f"    {result['fallbacks']} character(s) came from a fallback font")
    if result["unresolved"]:
        # Named, not counted. A character no font could draw is a name that can never match,
        # and knowing WHICH tells the player whether it matters to them.
        shown = "".join(result["unresolved"][:20])
        print(f"[!] {len(result['unresolved'])} character(s) no font here can draw: {shown}"
              f"\n    Names containing them will not be recognised.")
    return 0


def _atlas_fonts(args) -> tuple[str, list[Path]]:
    """The face to build from, and the ones to fall back to.

    The game's own, by default, read out of the player's installation — that is the whole
    point, and it is what makes the atlas match the screen. Anything given with `--font`
    wins, because a player whose game is elsewhere, or who wants to try another face, should
    not have to argue with a search.

    Not finding the game is an ORDINARY outcome, not an error: a player may be replaying
    someone else's frames on a machine that never had it installed.
    """
    fallbacks = [Path(f) for f in getattr(args, "fallback", [])]
    if getattr(args, "font", None):
        return args.font, fallbacks

    from .gamefont import game_fonts

    found = game_fonts(getattr(args, "game", None))
    if not found:
        raise SystemExit(
            "[!] the game's fonts could not be read, and no --font was given.\n"
            "    Point at the install with --game <folder>, or give any font with\n"
            "    --font <path to a .ttf/.otf/.ttc> — the game's own face reads everything,\n"
            "    another face reads most of it.")
    print(f"[=] the game's own faces: {', '.join(p.name for p in found)}")
    return str(found[0]), found[1:] + fallbacks


def cmd_whoami(cfg: ClientConfig, args) -> int:
    print(f"install_id = {cfg.install_id}")
    print("提出此識別碼即可要求刪除你的所有資料。/ Quote this id to request erasure.")
    return 0


# -- entry point -----------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wddrop")
    ap.add_argument("--data", help="state directory (default: per-user config dir)")
    # Accepted BEFORE the subcommand and after it, because both are what people type. The
    # per-subcommand copies use SUPPRESS so that not passing one there does not overwrite a
    # `--trace` given ahead of it — argparse would otherwise fill in its own default and
    # silently turn it back off.
    ap.add_argument("--trace", action="store_true",
                    help="write a detailed log (also a setting in the window)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("consent")
    sub.add_parser("ui")
    sub.add_parser("whoami")
    sub.add_parser("windows")
    sub.add_parser("drops")
    sub.add_parser("accuracy")
    q = sub.add_parser("probe")
    q.add_argument("--source", default="window")
    q.add_argument("--vocab", default="vocab.ja.json")
    q.add_argument("--locale", default="ja")

    # ja, like everything else the client reads: the build stopped shipping a Chinese
    # catalogue with 0.5.0, so this default named a file that is no longer there.
    p = sub.add_parser("dungeons"); p.add_argument("--catalog", default="catalog.ja.json")
    p.add_argument("--floors", action="store_true")

    for name in ("calibrate", "run", "replay", "verify"):
        q = sub.add_parser(name)
        # Japanese by default, as the window is and as the guide asks for: the game's own
        # font is readable out of the player's install for that language and no other.
        q.add_argument("--vocab", default="vocab.ja.json")
        q.add_argument("--locale", default="ja")
        if name == "calibrate":
            q.add_argument("--drop-shot", help="screenshot showing a drop message "
                                               "(omit to capture it live, guided)")
            q.add_argument("--name", help="the item name in that screenshot")
            q.add_argument("--walk-shot", help="screenshot while walking (for the HUD template)")
            q.add_argument("--delay", type=float, default=4.0,
                           help="seconds to switch back to the game before each capture")
            # NOT REQUIRED ANY MORE. The client renders from the atlas it built out of the
            # player's own copy of the game, and making this mandatory meant every command
            # line had to name a typeface — so the obvious thing to pass was the extracted
            # `fonts\*.ttf`, which is NOT what the game draws with. See _font_candidates for
            # what that measured: 0.547 and a failed self-check, against 0.823 for the atlas.
            q.add_argument("--fonts", default=None,
                           help="render with this font or atlas instead of the atlas this "
                                "client built (a glob, or one atlas.<locale>.json)")
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
            # Defaulted to None, not to a language. The vocabulary carries this string per
            # locale and `_build_runner` prefers it; the flag is only an override. A Chinese
            # default meant a Japanese session searched every line for a string that could
            # not appear in it.
            q.add_argument("--open-prompt", default=None)
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
                                   help="ask about every reading again, including the "
                                        "ones already confirmed")
                    q.add_argument("--differing", action="store_true",
                                   help="ask again only where the client now reads "
                                        "something OTHER than what you confirmed")
                    q.add_argument("--transcript", metavar="FILE",
                                   help="confirm from a written record instead of the "
                                        "prompt: lines of 'session#N: item xA, item'")
                    q.add_argument("--verified-by", default="player", metavar="WHO",
                                   help="who is confirming (default: player). A later "
                                        "reading of the frames is not a player and is "
                                        "reported apart — e.g. --verified-by frames")

    sub.add_parser("upload")

    a = sub.add_parser("atlas", help="build the glyph atlas from a font on this machine")
    a.add_argument("--font", help="the .ttf/.otf/.ttc to render from")
    a.add_argument("--fallback", action="append", default=[],
                   help="another font to take characters the first one lacks from")
    a.add_argument("--out", help="where to write it (default: your data folder)")
    a.add_argument("--game", help="the game's folder, if it is not where Steam usually puts it")

    for parser in sub.choices.values():
        parser.add_argument("--trace", action="store_true", default=argparse.SUPPRESS,
                            help="write a detailed log (also a setting in the window)")

    args = ap.parse_args(argv)
    cfg = ClientConfig.load()
    # The flag turns it on for this run; the setting turns it on for every run. Neither can
    # turn the other off — a player who ticked the box in the window and then ran from the
    # command line meant it to stay on.
    from . import logs

    logs.configure(trace=getattr(args, "trace", False) or cfg.trace)
    handlers = {
        "consent": cmd_consent, "dungeons": cmd_dungeons, "calibrate": cmd_calibrate,
        "run": cmd_run, "replay": cmd_replay, "upload": cmd_upload, "whoami": cmd_whoami,
        "windows": cmd_windows, "probe": cmd_probe, "drops": cmd_drops,
        "verify": cmd_verify, "accuracy": cmd_accuracy, "ui": cmd_ui,
        "atlas": cmd_atlas,
    }
    try:
        return handlers[args.cmd](cfg, args)
    except ConsentRequired as exc:
        print(f"[!] {exc}\n    先執行 `wddrop consent`. / Run `wddrop consent` first.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
