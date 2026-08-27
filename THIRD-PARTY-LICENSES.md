# Third-party software in `wddrop.exe`

The client's own code is Apache-2.0 (`LICENSE`, `NOTICE`). The published exe is a
PyInstaller bundle, so it also carries the packages below, each under its own licence.

**This file identifies them. It is not by itself the whole obligation** — MIT and BSD
require their notice text to travel with the binary, and Qt requires more than that (see
below). The texts are collected into `licenses/` at build time from each package's own
metadata rather than copied here by hand, because a list maintained in two places drifts
and the version that shipped is the only one that matters.

Versions are the ones resolved at the 0.9.2 build. `build_exe.RUNTIME_IMPORTS` is the list
this is derived from; if something is added there it belongs here too.

| package | version | licence | why it is in the bundle |
|---|---|---|---|
| PySide6-Essentials (Qt) | 6.11.x | **LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only** | the window itself |
| pillow | 12.3.0 | MIT-CMU | recognition — image handling |
| numpy | 2.5.2 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | recognition — the correlation |
| mss | 10.2.0 | MIT | screen capture |
| windows-capture | 2.0.1 | MIT | reading the game *window* rather than the screen |
| httpx | 0.28.1 | BSD-3-Clause | uploading |
| pydantic | 2.13.4 | MIT | the record format |
| UnityPy | 1.25.3 | MIT (© 2019-2026 K0lb3) | reading the game's own font, to build the atlas |
| wddrop-schema | 0.1.0 | Apache-2.0 | the wire format, shared with the server |

## Qt / PySide6 — LGPL-3.0

Qt is used under the **LGPL-3.0**, unmodified, and is dynamically linked by PySide6. Two
things follow, and they are obligations on the binary rather than on this project's own
licence:

1. **The licence text and this notice travel with the exe.**
2. **You may replace the Qt libraries with your own build.** A single-file PyInstaller exe
   makes that awkward, so it is offered directly instead:

> **Written offer.** For three years from the date this release was published, the complete
> corresponding source of the LGPL-covered components in this binary, and whatever is needed
> to relink the application against a modified version of them, is available on request.
> Open an issue at <https://github.com/exkuretrol/wddrop-client/issues>, naming the release
> version. The same source is published by the Qt Company at <https://download.qt.io/> and
> by PySide6 on PyPI.

**Known gap:** the workflow installs Qt with `--with PySide6-Essentials`, which is not
pinned, so the repository does not record the exact Qt version any given exe shipped with.
An offer of "the source corresponding to *this* binary" wants that number. The build should
stamp the resolved version into this file.

## Everything else

MIT, BSD-3-Clause and MIT-CMU are attribution licences: their copyright and permission
notices must accompany the binary, which is what `licenses/` is for. None of them restricts
what this project does with the result.

numpy's expression covers vendored components with their own terms (`0BSD`, `Zlib`,
`CC0-1.0`); its own distribution carries the full set, which is why they are collected from
the package rather than summarised here.
