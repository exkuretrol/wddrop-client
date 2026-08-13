"""The game's own typeface, read from the copy the player already has.

WHY THIS IS ORDINARY
--------------------
The recogniser compares rendered candidates against the screen, so it needs the face the
game draws with. Shipping that face would redistribute a licensed typeface; asking the player
for a substitute costs accuracy. Reading it out of their own installation costs neither.

Nothing here is decrypted, unpacked or worked around. `resources.assets` is plain Unity data
sitting beside the executable, and the fonts are ordinary Font objects inside it — the same
thing any asset viewer shows. That is the whole reason this module can exist in the open:
there is no key, no cipher, and nothing to hide.

WHICH FONT, AND WHY IT MATTERS WHICH LANGUAGE THE GAME IS IN
------------------------------------------------------------
`BaseFont` in `resources.assets` is byte-identical to `FOT-MatisseProN-DB` — the face the
game renders JAPANESE with. Measured against the vocabularies:

    ja      1,188 distinct characters, 0 missing, 0 names unmatchable
    zh_tw   1,481 distinct characters, 22 missing -> 366 of 3,478 names (10.5%) unreadable

So this is the game's real font for a Japanese client and an approximation for any other. The
Traditional Chinese face exists only inside the encrypted bundles, which is exactly what this
module refuses to touch — hence the guide asking a player to set the game to Japanese.

THE FONT IS COPIED OUT ONCE
---------------------------
Into the player's own folder, so the atlas can be rebuilt later without the game being
installed, and so nothing reads from Program Files at capture time.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("wddrop.gamefont")

GAME_DIR_NAME = "Wizardry Variants Daphne"
DATA_DIR_NAME = "WizardryVariantsDaphne_Data"
# In order. The first is what the game draws item names with; the second covers a handful of
# characters the first does not have, and is used only for those.
WANTED = ("BaseFont", "ScenarioFont")
# Below this it is one of the small numeric display faces, not a text face.
MIN_FONT_BYTES = 100_000


def _steam_libraries() -> list[Path]:
    """Every Steam library folder this machine knows about.

    The registry first, because a player who moved Steam has a path nothing else predicts;
    `libraryfolders.vdf` then, because the GAME may be on another drive from Steam itself.
    Parsed with a simple scan rather than a vdf library — the file is a flat list of quoted
    paths and one dependency for that is not worth it.
    """
    roots: list[Path] = []
    try:
        import winreg

        for key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for path in (r"Software\Valve\Steam", r"Software\WOW6432Node\Valve\Steam"):
                try:
                    with winreg.OpenKey(key, path) as handle:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(Path(winreg.QueryValueEx(handle, name)[0]))
                            except OSError:
                                continue
                except OSError:
                    continue
    except ImportError:
        pass                                    # not Windows; the guesses below still apply
    roots += [Path(r"C:/Program Files (x86)/Steam"), Path(r"C:/Program Files/Steam")]

    libraries: list[Path] = []
    for root in roots:
        apps = root / "steamapps"
        if apps.is_dir():
            libraries.append(apps)
        vdf = apps / "libraryfolders.vdf"
        if not vdf.exists():
            continue
        try:
            for line in vdf.read_text(encoding="utf-8", errors="replace").splitlines():
                if '"path"' not in line:
                    continue
                quoted = line.split('"')
                if len(quoted) >= 4:
                    other = Path(quoted[3].replace("\\\\", "/")) / "steamapps"
                    if other.is_dir():
                        libraries.append(other)
        except OSError:
            continue
    return list(dict.fromkeys(libraries))


def game_data_dir(hint: str | Path | None = None) -> Path | None:
    """The game's `_Data` folder, or None if this machine does not have the game.

    None is an ORDINARY answer. A player may record on a machine that never had the game
    installed — replaying someone else's frames, or capturing a stream — and the client has
    to keep working by asking them for a font instead.
    """
    if hint:
        found = Path(hint)
        if found.name != DATA_DIR_NAME:
            found = found / DATA_DIR_NAME
        return found if (found / "resources.assets").exists() else None
    for library in _steam_libraries():
        found = library / "common" / GAME_DIR_NAME / DATA_DIR_NAME
        if (found / "resources.assets").exists():
            return found
    return None


def extract_fonts(destination: Path, hint: str | Path | None = None) -> list[Path]:
    """Copy the game's text faces out to `destination`, in fallback order.

    Returns an empty list when the game is not here or the faces are not where they were —
    never a partial answer, because a caller that got one font of two would build an atlas
    with holes in it and not know.
    """
    data_dir = game_data_dir(hint)
    if data_dir is None:
        return []
    try:
        import UnityPy
    except ImportError:
        log.info("wddrop: UnityPy is not installed; the game's own font cannot be read")
        return []

    destination.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    try:
        env = UnityPy.load(str(data_dir / "resources.assets"))
        for obj in env.objects:
            if obj.type.name != "Font" or len(found) == len(WANTED):
                continue
            data = obj.read()
            name = str(getattr(data, "m_Name", ""))
            if name not in WANTED or name in found:
                continue
            blob = bytes(getattr(data, "m_FontData", b"") or b"")
            if len(blob) < MIN_FONT_BYTES:
                continue                        # a numeric display face, not a text one
            target = destination / f"{name}.ttf"
            target.write_bytes(blob)
            found[name] = target
    except Exception as exc:                    # noqa: BLE001 — never fatal; ask instead
        log.warning("wddrop: could not read the game's fonts: %s", exc)
        return []
    ordered = [found[name] for name in WANTED if name in found]
    if ordered:
        log.info("wddrop: read %s from the game's own files",
                 ", ".join(p.name for p in ordered))
    return ordered


def game_fonts(hint: str | Path | None = None) -> list[Path]:
    """The game's faces, copied out once and reused after that.

    Cached in the player's own folder: the atlas can then be rebuilt without the game being
    installed, and nothing reads from Program Files while a session is running.
    """
    from .config import config_dir

    cache = config_dir() / "fonts"
    existing = [cache / f"{name}.ttf" for name in WANTED]
    ready = [p for p in existing if p.exists()]
    if len(ready) == len(WANTED):
        return ready
    return extract_fonts(cache, hint) or ready


def discard_cache() -> list[str]:
    """Delete the copied faces, once an atlas has been made from them.

    The copy exists to be rasterised, and after that it is a redundant duplicate of a
    commercial typeface sitting in a player's folder. `game_fonts` re-extracts from the
    game whenever it is missing, so removing it costs a few seconds on the next rebuild and
    nothing else — and a machine that no longer has the game installed does not need one,
    because it already has the atlas.

    Never raises. A file that will not delete (open elsewhere, read-only) is not a reason to
    fail the build that just succeeded.
    """
    from .config import config_dir

    removed = []
    for name in WANTED:
        path = config_dir() / "fonts" / f"{name}.ttf"
        try:
            if path.exists():
                path.unlink()
                removed.append(path.name)
        except OSError:
            log.debug("wddrop: could not remove %s", path, exc_info=True)
    folder = config_dir() / "fonts"
    try:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass
    if removed:
        log.info("wddrop: removed the copied faces after building the atlas: %s",
                 ", ".join(removed))
    return removed


def game_font(hint: str | Path | None = None) -> Path | None:
    """Just the primary face, for a caller that wants one path."""
    fonts = game_fonts(hint)
    return fonts[0] if fonts else None
