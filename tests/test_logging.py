"""The log file, and the two things it must never get wrong.

A log is the only evidence that survives a session nobody was watching. It is also a file
players paste into chat windows when asking for help, which is what makes its contents a
privacy question rather than a convenience one.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))


@pytest.fixture(autouse=True)
def _own_folder(tmp_path, monkeypatch):
    """Every test here writes a real file, and none of them may touch the real one."""
    monkeypatch.setenv("WDDROP_HOME", str(tmp_path))
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_wddrop", False):
            root.removeHandler(handler)
            handler.close()


def test_it_writes_where_the_players_own_files_are(tmp_path):
    """Inside the one folder whose whole promise is "delete this and it is all gone". A log
    left somewhere else outlives the erasure it was supposed to be covered by."""
    from wddrop_client import logs
    from wddrop_client.config import config_dir

    path = logs.configure(trace=False)
    assert path is not None
    assert config_dir() in path.parents
    logging.getLogger("wddrop.test").info("hello")
    assert "hello" in path.read_text(encoding="utf-8")


def test_normal_keeps_the_reasoning_out_and_trace_puts_it_in():
    """The whole point of the switch. At INFO the file is what a player would recognise; the
    recogniser's own workings are far too much to write on every session."""
    from wddrop_client import logs

    path = logs.configure(trace=False)
    logging.getLogger("wddrop.test").debug("why the line was refused")
    logging.getLogger("wddrop.test").info("a chest was recorded")
    quiet = path.read_text(encoding="utf-8")
    assert "why the line was refused" not in quiet
    assert "a chest was recorded" in quiet

    logs.configure(trace=True)
    logging.getLogger("wddrop.test").debug("why the line was refused")
    assert "why the line was refused" in path.read_text(encoding="utf-8")


def test_the_install_id_never_reaches_the_log():
    """It is the erasure handle, and the one identifier the service promises never to store.
    A log file is exactly what gets pasted into a chat window when someone asks for help.

    This is the same mistake as putting it in a query string, where it went into every
    access log — see docs/HANDOFF.md. Caught here rather than in review, because the way it
    arrives is somebody logging a config object that happens to contain it.
    """
    from wddrop_client import logs
    from wddrop_client.config import ClientConfig

    cfg = ClientConfig.load()
    path = logs.configure(trace=True)
    # Everything the client says about itself at startup, and then a whole session's worth
    # of the noisiest thing it does.
    logging.getLogger("wddrop.runner").info("wddrop: session %s started", "dive-1")
    logging.getLogger("wddrop.runner").debug("wddrop: uploading to %s", cfg.server_url)
    written = path.read_text(encoding="utf-8")
    assert cfg.install_id not in written
    assert cfg.install_id, "the test would pass trivially against an empty id"


def test_a_japanese_item_name_survives_being_written():
    """Every name in this study is CJK. A file opened at the Windows default encoding
    (cp932, cp950) raises inside the handler on the first one it cannot encode — and logging
    swallows that, so the lines that vanish are exactly the ones naming what was read."""
    from wddrop_client import logs

    path = logs.configure(trace=False)
    logging.getLogger("wddrop.test").info("wddrop: read %s", "ウロボロス鉱石")
    assert "ウロボロス鉱石" in path.read_text(encoding="utf-8")


def test_configuring_twice_does_not_write_everything_twice():
    """The window reconfigures whenever the setting changes, and a doubled handler turns
    one line into two — which reads as the loop having run twice."""
    from wddrop_client import logs

    logs.configure(trace=False)
    path = logs.configure(trace=True)
    logging.getLogger("wddrop.test").info("once")
    assert path.read_text(encoding="utf-8").count("once") == 1


def test_a_log_that_cannot_be_opened_does_not_stop_capture(monkeypatch):
    """A read-only folder, a full disk, an antivirus holding the file. None of those is a
    reason to refuse to record — capture is the thing that cannot be redone afterwards."""
    from wddrop_client import logs

    def refuse(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(logs.logging.handlers, "RotatingFileHandler", refuse)
    assert logs.configure(trace=True) is None
    logging.getLogger("wddrop.test").info("still running")


def test_the_flag_and_the_setting_both_turn_it_on(monkeypatch, tmp_path):
    """`--trace` for this run, the checkbox for every run, and neither turns the other off:
    a player who ticked the box and then used the command line meant it to stay on."""
    from wddrop_client import __main__ as cli
    from wddrop_client.config import ClientConfig

    seen = {}
    monkeypatch.setattr(cli, "ClientConfig", ClientConfig)
    import wddrop_client.logs as logs_module

    monkeypatch.setattr(logs_module, "configure",
                        lambda trace=False, console=True: seen.update(trace=trace))
    monkeypatch.setattr(cli, "cmd_whoami", lambda cfg, args: 0)

    cli.main(["whoami"])
    assert seen["trace"] is False

    cli.main(["--trace", "whoami"])
    assert seen["trace"] is True

    # After the subcommand as well — it is what people type.
    cli.main(["whoami", "--trace"])
    assert seen["trace"] is True

    cfg = ClientConfig.load()
    cfg.trace = True
    cfg.save()
    cli.main(["whoami"])
    assert seen["trace"] is True, "the setting was ignored without the flag"


def test_the_window_configures_the_LOG_FILE_at_startup_not_a_console():
    """The exe never wrote a log line in its life, and this is why.

    `build_exe`'s entry script calls `wddrop_client.ui.main` — not `__main__.main`, which is
    where the file logging lives. `ui.main` called `logging.basicConfig`, which writes to a
    console, and the build is `--windowed`: there is no console, and `sys.stderr` is None. So
    every INFO and DEBUG line the client produced went nowhere at all.

    The setting was half-wired for the same reason: `logs.configure` ran only when the trace
    checkbox was TOGGLED, so a player who had turned trace on in an earlier session opened
    the window with it ticked and still got nothing. That is exactly how it was reported —
    "I checked the trace mode and it didn't record".

    Checked on the source rather than by running Qt: the property is which call is in there,
    and a headless Qt run would test the harness more than the client.
    """
    import ast
    import inspect
    import textwrap

    from wddrop_client import ui

    tree = ast.parse(textwrap.dedent(inspect.getsource(ui.main)))
    fn = tree.body[0]
    # The DOCSTRING is not the code, and it names both calls to explain the bug.
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]
    src = ast.unparse(fn)
    assert "logs.configure" in src, "the window's entry point does not set up file logging"
    assert "basicConfig" not in src, "a console handler in a build that has no console"
    assert "cfg.trace" in src or "getattr(cfg" in src, \
        "the trace SETTING is not applied at startup, only when the box is toggled"


def test_the_bundle_really_does_enter_through_the_window():
    """The link the test above depends on. If the entry script ever calls `__main__.main`
    instead, the reasoning changes and this should be looked at again."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    import build_exe

    target = Path(__import__("tempfile").mkdtemp()) / "entry.py"
    body = build_exe.entry_script(target).read_text(encoding="utf-8")
    assert "ui.main()" in body
    assert "wddrop_client.__main__" not in body
