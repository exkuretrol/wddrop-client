"""The game window's OWN pixels, from Windows.Graphics.Capture.

WHY NOT A SCREENSHOT
--------------------
Screen capture reads a rectangle of the desktop, so it reads whatever is drawn there — and
what is drawn there is not always the game. Everything that sits on top lands in the frame
instead: a browser, a chat window, the client's own window if the player brings it forward
to check a count. The reading then fails on pixels that were never the game's, and nothing
downstream can tell the difference between that and a chest that paid nothing.

Windows.Graphics.Capture asks the compositor for a WINDOW's frames rather than for a region
of screen. What comes back is the window as it drew itself, whatever is in front of it, and
it follows the window without anyone having to track where it moved to.

WHAT IT IS NOT
--------------
Not a way to read a window that is minimised — there is nothing being drawn to capture —
and not a way to read one the player has closed. Both end the session, which is correct: it
ended.

WHY NOT PrintWindow
-------------------
The obvious cheap answer, and it does not work here. PrintWindow asks a window to redraw
itself through GDI, and this game draws through a GPU swapchain; the result is a black
rectangle. Measured before writing any of this.

THE CROP IS NOT COSMETIC
------------------------
A captured window frame is the window's whole visual — its border and title bar included —
while every region in a profile is measured against the CLIENT area, which is what a
screenshot of the game gave. So the client area is cut out of each frame, at the offset
between where the compositor puts the window's frame and where Windows says the client area
starts. Getting that wrong shifts every band by the height of a title bar, which reads as
"nothing is recognised any more" rather than as an error.
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time
import types
from typing import Iterator

log = logging.getLogger("wddrop.wgc")

# Frames arrive when the window draws, which is at its own frame rate rather than ours. Only
# the newest is ever wanted: a sampler that fell behind and then read a queue would be
# reading the past, and the whole point of the sample rate is to see what is on screen NOW.
LATEST_ONLY = 1
# How long to wait for the first frame before giving up on this path. A window that is
# drawing produces one within a frame or two; nothing at all in this long means the
# compositor is not going to send any, and the caller should fall back rather than hang.
FIRST_FRAME_TIMEOUT = 4.0


class _NoOpenCV(types.ModuleType):
    """Stands in for OpenCV, which `windows_capture` imports and this client does not use.

    The package's convenience wrapper does `import cv2` at module scope to implement one
    helper that saves a frame as an image file. Nothing here calls it — frames go to PIL —
    but the import runs anyway, and it drags 42 MB into a 68 MB client.

    So the import is satisfied and left empty. If a future version of the package ever
    reaches for something in it, this says exactly what happened instead of failing as an
    AttributeError from inside somebody else's module, and capture falls back to the screen.
    """

    def __getattr__(self, name):
        # Dunders answer as ordinarily absent. Python and its introspection machinery ask
        # modules for things like `__wrapped__` and `__path__` all the time and expect an
        # AttributeError when they are not there — raising anything else out of those turns a
        # routine lookup into a failure. Measured: the import died on `cv2.__wrapped__`
        # before the package had asked for anything of its own.
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise ImportError(
            f"windows_capture wanted cv2.{name}, which this client deliberately does not "
            f"bundle. Window capture is unavailable; reading the screen instead.")


def _native():
    """The compiled capture class, without OpenCV coming with it."""
    if "cv2" not in sys.modules:
        sys.modules["cv2"] = _NoOpenCV("cv2")
    from windows_capture.windows_capture import NativeWindowsCapture

    return NativeWindowsCapture


def available() -> bool:
    """Whether this machine can capture a window's own frames.

    False is an ordinary answer — another platform, an older Windows, or the package not
    installed — and the caller falls back to reading the screen.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        _native()
    except Exception as exc:                               # noqa: BLE001
        log.info("wddrop: window capture unavailable (%s); reading the screen instead", exc)
        return False
    return True


def client_offset(handle: int) -> tuple[int, int]:
    """Where the client area starts inside a captured window frame.

    The compositor hands over the window's extended frame bounds — the visual, including the
    shadow-free border it draws — and the client area sits somewhere inside that. Both are in
    screen coordinates, so the offset is one subtraction; it is NOT a constant, because it
    depends on the border and title bar the theme happens to draw.
    """
    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    bounds = RECT()
    hit = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(handle), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
        ctypes.byref(bounds), ctypes.sizeof(bounds))
    origin = wintypes.POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(wintypes.HWND(handle), ctypes.byref(origin))
    if hit != 0:
        # DWM would not say. The window rect is the next best origin, and being a few pixels
        # out is still better than being a whole title bar out.
        rect = RECT()
        if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(handle), ctypes.byref(rect)):
            return 0, 0
        bounds = rect
    return origin.x - bounds.left, origin.y - bounds.top


class WindowSource:
    """Live capture of one window, by handle.

    Written against the native module rather than the `windows_capture` convenience wrapper
    for one reason worth stating: that wrapper imports OpenCV at module scope to implement a
    `save_as_image` helper, and OpenCV is 42 MB of a 68 MB client that would never call it.
    The native class is what the wrapper is a thin layer over — the same callbacks, the same
    arguments.
    """

    def __init__(self, handle: int, fps: float = 20.0,
                 size: tuple[int, int] | None = None,
                 strips: list[tuple[int, int, int, int]] | None = None):
        self.handle = handle
        self.fps = fps
        # The CLIENT size capture was calibrated for. Frames are cut to exactly this, so a
        # window that is resized mid-session keeps producing frames of the shape everything
        # downstream expects — and the frame-size check says so once, as before.
        self.size = size
        # Accepted and ignored. The screen path grabs only the strips it reads because moving
        # 1920x1080 of pixels through the CPU is what capped its rate; here the compositor
        # hands over the whole window either way, and cutting it up again would cost more
        # than it saved.
        self.strips = strips
        self._latest: queue.Queue = queue.Queue(maxsize=LATEST_ONLY)
        self._stop = threading.Event()
        self._closed = threading.Event()
        self._error: BaseException | None = None

    # -- the capture thread ------------------------------------------------------------
    def _on_frame(self, native_frame, buf_len: int, width: int, height: int,
                  stop_list: list, timespan: int) -> None:
        """Called by the compositor's thread, for every frame the window draws.

        The buffer belongs to the mapped frame and is valid only until this returns, so what
        goes on the queue is a COPY. Everything expensive downstream — the crop, the greyscale
        conversion, the matching — happens on the consumer's thread, because blocking here
        blocks the compositor.
        """
        import numpy as np

        if self._stop.is_set():
            stop_list[0] = True
            return
        try:
            pitch = int(native_frame.bytes_per_row)
            raw = np.frombuffer(native_frame.buffer_view(), dtype=np.uint8, count=buf_len)
            frame = raw.reshape(height, pitch)[:, : width * 4].reshape(height, width, 4).copy()
        except Exception as exc:                           # noqa: BLE001
            self._error = exc
            stop_list[0] = True
            return
        # Newest wins. A full queue means the sampler has not taken the previous frame yet,
        # and that frame is now the stale one.
        try:
            self._latest.get_nowait()
        except queue.Empty:
            pass
        try:
            self._latest.put_nowait(frame)
        except queue.Full:
            pass

    def _on_closed(self) -> None:
        self._closed.set()

    def _start(self):
        capture = _native()(
            self._on_frame,          # on_frame_arrived
            self._on_closed,         # on_closed
            False,                   # cursor_capture — the cursor is not the game
            False,                   # draw_border — no yellow rectangle around the game
            None,                    # secondary_window
            None,                    # minimum_update_interval
            None,                    # dirty_region
            None,                    # monitor_index — a window, not a screen
            None,                    # window_name — by handle, which cannot collide
            self.handle,
        )
        return capture.start_free_threaded()

    # -- the frame source --------------------------------------------------------------
    def frames(self) -> Iterator:
        from PIL import Image

        control = self._start()
        left, top = client_offset(self.handle)
        interval = 1.0 / self.fps
        start = due = time.monotonic()
        first, waiting = True, False
        try:
            while True:
                if self._error is not None:
                    raise RuntimeError(f"window capture failed: {self._error}")
                try:
                    buffer = self._latest.get(
                        timeout=FIRST_FRAME_TIMEOUT if first else max(interval * 4, 1.0))
                except queue.Empty:
                    if self._closed.is_set():
                        log.info("wddrop: the game window closed")
                        return
                    if first:
                        # Nothing at all, ever. Either the window is minimised or this path
                        # does not work here; both are reasons to say so rather than to sit
                        # in a loop producing nothing.
                        raise RuntimeError(
                            "window capture produced no frames. Is the game minimised?")
                    # A GAP is not a failure. A minimised window draws nothing, and a player
                    # who minimises the game for a moment must not lose the session for it —
                    # this used to raise, which ended the dive and everything not yet
                    # emitted with it. Waiting costs nothing: the runner's own idle timeout
                    # still ends a session that never comes back.
                    if not waiting:
                        waiting = True
                        log.info("wddrop: the game window is not drawing (minimised?) — "
                                 "waiting for it to come back")
                    continue
                if waiting:
                    waiting = False
                    log.info("wddrop: the game window is drawing again")
                first = False
                width, height = self.size or (buffer.shape[1] - left, buffer.shape[0] - top)
                cut = buffer[top:top + height, left:left + width, :3][:, :, ::-1]
                image = Image.fromarray(cut, "RGB")
                yield _Frame(t=time.monotonic() - start, image=image)
                if self._closed.is_set():
                    return
                # Paced against a running deadline rather than the elapsed time modulo the
                # interval: frames are already waiting when the loop comes round, so the
                # modulo form delivered 24fps for a requested 20 — a fifth more CPU spent
                # beside a game, for samples nobody asked for.
                due += interval
                time.sleep(max(0.0, due - time.monotonic()))
        finally:
            self._stop.set()
            try:
                control.stop()
            except Exception:                              # noqa: BLE001 — already stopping
                pass


def _Frame(t: float, image):                               # noqa: N802 — a constructor
    from .source import Frame

    return Frame(t=t, image=image)
