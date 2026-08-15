"""
Frame sources — where pixels come from.

The whole capture pipeline is written against this interface so the SAME code path can be
driven by a live screen, a recorded video, or a directory of PNGs. That is not just tidiness:
it means the pipeline can be validated end-to-end against existing recordings on a machine
that has neither the game nor Windows, and switching to live capture on the player's box is
a one-line change rather than a different program.

It also gives the accuracy harness something to replay: a reading and the player's own ground
truth can be compared over the same frames, deterministically, as many times as needed.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

log = logging.getLogger("wddrop.source")


@dataclass(frozen=True)
class Frame:
    """One captured frame. `t` is seconds since capture start, monotonic."""

    t: float
    image: object                 # PIL.Image, greyscale-convertible
    # Where this frame came from, when it came from disk. Carried through to each recorded
    # item so a suspect reading can be checked against the exact picture that produced it,
    # instead of being hunted for among thousands of PNGs.
    source: str | None = None


class FrameSource(Protocol):
    def frames(self) -> Iterator[Frame]: ...


def resample(image, to_size):
    """Bring a frame back to the size its calibration was made at.

    BOX when the scale is a whole number, which is exactly the 4K case: 3840x2160 -> 1920x1080
    averages each 2x2 block, which is what the compositor's enlargement did in reverse.
    LANCZOS otherwise, for 2560x1440 and anything else that lands between pixels.
    """
    from PIL import Image

    width, height = image.size
    if (width, height) == tuple(to_size):
        return image
    scale = width / to_size[0]
    exact = abs(scale - round(scale)) < 1e-6
    return image.resize(tuple(to_size), Image.BOX if exact else Image.LANCZOS)


class ScaledSource:
    """Frames that arrive larger than the calibration, resampled back down to it.

    For a source that hands over whole frames — a recording made on a 4K screen, a still, a
    clip. Live capture does NOT go through this: it grabs strips rather than whole frames,
    so it scales each strip at the grab instead (see ScreenSource.scale), which is both
    cheaper and exactly equivalent.
    """

    def __init__(self, inner: "FrameSource", to_size: tuple[int, int]):
        self.inner = inner
        self.to_size = (int(to_size[0]), int(to_size[1]))

    def frames(self) -> Iterator[Frame]:
        from dataclasses import replace

        for frame in self.inner.frames():
            yield replace(frame, image=resample(frame.image, self.to_size))


class FramesDirSource:
    """Replay a directory of PNGs, in filename order, at a nominal frame rate.

    Filenames are sorted lexicographically, so zero-padded numbering is required — the
    natural output of `ffmpeg -vf fps=N out_%03d.png`.
    """

    def __init__(self, directory: str | Path, fps: float = 4.0, pattern: str = "**/*.png"):
        self.directory = Path(directory)
        self.fps = fps
        # Recursive by default: recordings are filed per episode in subdirectories, so
        # pointing replay at a session folder must pick up every episode in order rather
        # than silently finding nothing.
        self.pattern = pattern

    def frames(self) -> Iterator[Frame]:
        from PIL import Image

        paths = sorted(self.directory.glob(self.pattern))
        if not paths:
            raise FileNotFoundError(f"no {self.pattern} frames in {self.directory}")
        for i, p in enumerate(paths):
            try:
                image = Image.open(p)
                image.load()
            except Exception as exc:
                # Ctrl-C during a write leaves the last frame truncated. Skipping it costs
                # one frame; letting it raise threw away the entire replay of a session that
                # was otherwise complete.
                log.warning("wddrop: skipping unreadable frame %s (%s)", p.name, exc)
                continue
            yield Frame(t=i / self.fps, image=image, source=str(p))


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


class ImageSource:
    """A single still image, repeated `count` times.

    Pointing at one screenshot is the obvious thing to do when diagnosing, and treating it as
    a video (ffmpeg finding nothing to extract) is a confusing way to fail. Repeats are
    supported because the episode machine needs a line to be STABLE across frames before it
    trusts it, so a single frame would never produce a reading.
    """

    def __init__(self, path: str | Path, fps: float = 4.0, count: int = 1):
        self.path = Path(path)
        self.fps = fps
        self.count = count

    def frames(self) -> Iterator[Frame]:
        from PIL import Image

        img = Image.open(self.path)
        for i in range(self.count):
            yield Frame(t=i / self.fps, image=img, source=str(self.path))


class VideoSource:
    """Replay a video by extracting frames with ffmpeg.

    Extraction is to a temp directory rather than piped, so a failed run can be inspected
    and a long clip is not re-decoded on every pass.
    """

    def __init__(self, path: str | Path, fps: float = 4.0, workdir: str | Path | None = None):
        self.path = Path(path)
        self.fps = fps
        self.workdir = Path(workdir) if workdir else None

    def frames(self) -> Iterator[Frame]:
        out = self.workdir or Path(tempfile.mkdtemp(prefix="wddrop-frames-"))
        out.mkdir(parents=True, exist_ok=True)
        if not any(out.glob("*.png")):
            cmd = ["ffmpeg", "-v", "error", "-i", str(self.path),
                   "-vf", f"fps={self.fps}", str(out / "f_%05d.png")]
            log.info("wddrop: extracting frames -> %s", out)
            subprocess.run(cmd, check=True)
        yield from FramesDirSource(out, fps=self.fps).frames()


class ScreenSource:
    """Live capture of a window, or of a whole monitor.

    Window capture is the default for a reason: a 2560x1440 monitor showing a 1920x1080
    windowed game produces full-monitor frames that can never match a profile calibrated from
    a window screenshot, because every profile region is absolute pixels. Following the window
    also means the capture stays correct if the window is MOVED — only a resize invalidates
    the profile.
    """

    def __init__(self, monitor: int = 1, fps: float = 4.0,
                 region: tuple[int, int, int, int] | None = None,
                 window_title: str | None = None, follow_window: bool = False,
                 strips: list[tuple[int, int, int, int]] | None = None,
                 expect_size: tuple[int, int] | None = None,
                 profile_size: tuple[int, int] | None = None):
        self.monitor = monitor
        self.fps = fps
        self.region = region      # (left, top, width, height) in screen coordinates
        self.window_title = window_title
        self.follow_window = follow_window
        # Sub-regions to grab instead of the whole window, in window-relative coordinates.
        # Only the message band and the HUD chrome are ever read -- together well under 2% of
        # the pixels -- so grabbing the full 1920x1080 spends almost all of its time moving
        # bytes nobody looks at. That cost is what caps the sample rate, and the sample rate
        # is what decides whether a quickly-dismissed line is seen at all.
        #
        # The strips are composited back onto a full-size black canvas at their true
        # positions, so every absolute coordinate downstream stays valid.
        self.strips = strips
        # The calibrated size, used to disambiguate windows. Weaker than the process name but
        # far stronger than a title substring.
        self.expect_size = expect_size
        # WHEN THE WINDOW IS AN ENLARGEMENT OF THE CALIBRATION — a borderless-fullscreen game
        # rendering 1920x1080 onto a 2560x1440 or 4K desktop; see calibration.scaled_from.
        # Every region below is in the PROFILE's pixels, so the scale is applied at the grab:
        # each strip is asked for at its enlarged position and resampled back on arrival. The
        # canvas stays profile-sized, so nothing downstream can tell the difference — and the
        # cheap part stays cheap, since a strip is still the only thing read off the screen.
        self.profile_size = tuple(profile_size) if profile_size else None

    def _follow(self, handle: int, box: dict) -> dict:
        """Where the window is NOW, keeping the size capture started at.

        THE REGION IS RE-READ EVERY FRAME. It used to be read once, before the loop, and a
        window is a thing a player drags — after which every grab reads a rectangle of the
        desktop where the game no longer is, and the session records nothing while looking
        like it is working. Two syscalls; nothing measurable at 20fps.

        The SIZE is deliberately not followed. Every region in a profile is absolute pixels,
        so a resized window is a window this calibration no longer describes; carrying on at
        the original size keeps every downstream shape valid and lets the frame-size check
        say so once, instead of producing frames that quietly change shape mid-session.
        """
        from .window import client_region

        now = client_region(handle)
        if now is None:
            return box                                 # window gone: keep the last known box
        left, top, width, height = now
        if (width, height) != (box["width"], box["height"]) and not self._warned_resize:
            self._warned_resize = True
            log.warning("wddrop: the game window is now %dx%d but capture started at %dx%d — "
                        "still reading the original size, which the calibration is for.",
                        width, height, box["width"], box["height"])
        if (left, top) == (box["left"], box["top"]):
            return box
        return {"left": left, "top": top, "width": box["width"], "height": box["height"]}

    def frames(self) -> Iterator[Frame]:
        import time

        import mss
        from PIL import Image

        self._warned_resize = False

        interval = 1.0 / self.fps
        handle = None
        if self.follow_window:
            from .window import find_window

            found = find_window(self.window_title, expect_size=self.expect_size)
            self.region = found.as_region()
            handle = found.handle

        with mss.mss() as sct:
            mon = sct.monitors[self.monitor]
            box = mon
            if self.region:
                # Screen coordinates already, so they are NOT offset by the monitor origin.
                left, top, width, height = self.region
                box = {"left": left, "top": top, "width": width, "height": height}
            start = time.monotonic()
            while True:
                if handle is not None:
                    box = self._follow(handle, box)
                canvas = self.profile_size or (box["width"], box["height"])
                kx = box["width"] / canvas[0]
                ky = box["height"] / canvas[1]
                if self.strips:
                    img = Image.new("L", canvas, 0)
                    for (sl, st, sw, sh) in self.strips:
                        # Rounded OUTWARD, so a strip never comes back a pixel short of the
                        # region it stands for: the band's own edges are what the reader
                        # anchors on.
                        gl, gt = int(sl * kx), int(st * ky)
                        gw = max(1, int(round((sl + sw) * kx)) - gl)
                        gh = max(1, int(round((st + sh) * ky)) - gt)
                        sub = sct.grab({"left": box["left"] + gl, "top": box["top"] + gt,
                                        "width": gw, "height": gh})
                        cut = Image.frombytes("RGB", sub.size, sub.bgra, "raw", "BGRX").convert("L")
                        if (gw, gh) != (sw, sh):
                            cut = resample(cut, (sw, sh))
                        img.paste(cut, (sl, st))
                else:
                    shot = sct.grab(box)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    if img.size != tuple(canvas):
                        img = resample(img, canvas)
                yield Frame(t=time.monotonic() - start, image=img)
                # Sleep the remainder of the interval so the loop paces itself rather than
                # spinning; capture cost varies with resolution.
                time.sleep(max(0.0, interval - (time.monotonic() - start) % interval))


def _window_source(title: str | None, fps: float,
                   strips: list[tuple[int, int, int, int]] | None,
                   expect_size: tuple[int, int] | None):
    """The window's OWN frames, if this machine can give them. None to read the screen.

    Preferred whenever it is available, because a screenshot is of whatever is drawn where
    the game is — which is the game only while nothing is in front of it. Measured with a
    sheet over the top half of the game window: the screen path came back 49.96% covered,
    this one 0.00%.

    Falling back is not a failure state. An older Windows, a machine without the package, a
    window that is minimised: the screen path still works there, and it is what every
    recording so far was made with.
    """
    from .wgc import WindowSource, available

    if not available():
        return None
    try:
        from .window import find_window

        found = find_window(title, expect_size=expect_size)
        log.info("wddrop: capturing the window's own frames (occlusion cannot reach them)")
        return WindowSource(found.handle, fps=fps, size=found.size, strips=strips)
    except SystemExit:
        raise                                          # "no window matched" is the real answer
    except Exception as exc:                           # noqa: BLE001
        log.warning("wddrop: could not capture the window itself (%s); reading the screen", exc)
        return None


def _to_profile(source: "FrameSource", profile_size) -> "FrameSource":
    """Resample whole frames down to the calibrated size, when they arrive larger.

    A no-op both when nothing was asked for and when the frames already match — the check is
    a tuple compare per frame, and the alternative is every caller having to know whether its
    particular source scales itself.
    """
    return ScaledSource(source, profile_size) if profile_size else source


def open_source(spec: str, fps: float = 4.0,
                strips: list[tuple[int, int, int, int]] | None = None,
                expect_size: tuple[int, int] | None = None,
                profile_size: tuple[int, int] | None = None) -> FrameSource:
    """Build a source from a CLI-friendly string.

        window            follow the game window (default for live capture)
        window:Wizardry   follow a window whose title contains "Wizardry"
        screen            whole primary monitor
        screen:2          whole monitor 2
        /path/clip.mp4    replay a video
        /path/frames/     replay a directory of PNGs
        /path/shot.png    a single still (for `probe` / diagnosis)
    """
    if spec == "window" or spec.startswith("window:"):
        title = spec.split(":", 1)[1] if ":" in spec else None
        window = _window_source(title, fps, strips, expect_size)
        if window is not None:
            # The compositor hands this path the whole window either way, so it is resampled
            # whole. The screen path below scales each strip at the grab instead — same
            # result, but it never moves the pixels it does not read.
            return _to_profile(window, profile_size)
        return ScreenSource(fps=fps, window_title=title, follow_window=True, strips=strips,
                            expect_size=expect_size, profile_size=profile_size)
    if spec == "screen" or spec.startswith("screen:"):
        monitor = int(spec.split(":", 1)[1]) if ":" in spec else 1
        return ScreenSource(monitor=monitor, fps=fps, strips=strips, profile_size=profile_size)

    # PowerShell tab-completion leaves a trailing separator on directories, and `frames\`
    # is the form the docs use; strip it so the path resolves either way.
    path = Path(spec.rstrip("\\/") or spec)

    if path.is_dir():
        frames = sorted(path.glob("**/*.png"))
        if not frames:
            raise SystemExit(
                f"[!] {path} exists but contains no .png frames.\n"
                f"    Split a clip into it first:\n"
                f"        ffmpeg -i clip.mp4 -vf fps={fps:g} {path}\\f_%05d.png"
            )
        return _to_profile(FramesDirSource(path, fps=fps), profile_size)
    if path.is_file():
        if path.suffix.lower() in IMAGE_SUFFIXES:
            return _to_profile(ImageSource(path, fps=fps), profile_size)
        return _to_profile(VideoSource(path, fps=fps), profile_size)

    raise SystemExit(
        f"[!] frame source not found: {spec!r}\n"
        f"    Expected one of:\n"
        f"        window            follow the game window (recommended)\n"
        f"        window:Wizardry   follow a window matching a title substring\n"
        f"        screen            whole primary monitor\n"
        f"        screen:2          whole monitor 2\n"
        f"        path\\to\\clip.mp4   a recorded video\n"
        f"        path\\to\\frames\\   a directory of PNG frames\n"
        f"    To create a frames directory from a clip:\n"
        f"        ffmpeg -i clip.mp4 -vf fps={fps:g} frames\\f_%05d.png"
    )
