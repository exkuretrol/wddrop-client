"""Nothing published here may name how the game's own data was obtained.

This repository used to be assembled by an exporter in the private one, and every exported
byte was scanned against the list below before it could leave. There is no export any more —
the client is developed here — so the scan runs as a test, over this repository's own files.

It is weaker than what it replaces, and worth saying so: the old scan refused to publish,
this one reports after the commit exists. A commit is already public by then. It still fails
the build, and a rewritten line is a cheap fix, but the discipline it asks for is not to
write the sentence in the first place.

It will produce false positives eventually — a legitimate word that happens to match. Fix it
by rewording the sentence, not by shortening the list.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Terms that must not appear anywhere in this repository. Each is here because it names how
# the game's own data was obtained, or names a machine.
DENY = ['il2cpp',
 'masterdata',
 'nprotect',
 'gameguard',
 'frida',
 'mitmproxy',
 'assetbundle',
 '\\bdump(s|ed|ing)?\\b',
 'decrypt(ed|ion|s)?\\b',
 '/home/[a-z]+',
 '[A-Za-z]:\\\\\\\\Users\\\\\\\\[A-Za-z]+',
 '/mnt/c/Users',
 'wizardry_daphne',
 'work/(dumps|scripts|pipeline)',
 'wdpipe',
 'decompil(e|ed|es|ing|ation)',
 'hex.?rays',
 '\\bghidra\\b',
 '\\bida pro\\b',
 'reverse.?engineer(ed|ing)?']

# Sentences that are ABOUT not doing these things. The disclaimer has to be able to say the
# client does not decrypt traffic, and the code has to be able to say a mode was removed.
ALLOW_LINE = ['does not (modify|intercept|decrypt)',
 'it does not intercept, decrypt or alter',
 '[Nn]othing (here )?is decrypted',
 'json\\.dumps',
 '# noqa: DENY']

BINARY = ['.exe', '.ico', '.png', '.ttc', '.ttf', '.zip']


def _tracked() -> list[Path]:
    """Only what git tracks. Build output and virtualenvs are not published by being present,
    and the exporter this replaces learned that the expensive way: it once named 367 files
    under client/.venv, every one of them carrying a path from somebody's machine."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    return [ROOT / name for name in out.split("\0") if name]


def test_nothing_here_names_how_the_data_was_obtained():
    deny = [re.compile(p, re.I) for p in DENY]
    allow = [re.compile(p, re.I) for p in ALLOW_LINE]
    problems = []
    for path in _tracked():
        # This file holds examples of the very strings it forbids — it caught itself the
        # moment it was written, which is the check working.
        if path.resolve() == Path(__file__).resolve():
            continue
        if path.suffix.lower() in BINARY or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if any(a.search(line) for a in allow):
                continue
            for pattern in deny:
                if pattern.search(line):
                    problems.append(f"    {path.relative_to(ROOT)}:{number}  {pattern.pattern}\n"
                                    f"        {line.strip()[:100]}")
                    break
    assert not problems, "\n" + "\n".join(problems)


def test_the_scan_fails_when_it_should():
    """A guard nobody has seen fail is not one."""
    deny = [re.compile(p, re.I) for p in DENY]
    for line in ('FRAME = Path("/home/someone/work/dumps/extracted")',
                 "# built from the masterdata item table",
                 "# decompiled from the shipped build, the call reads:"):
        assert any(p.search(line) for p in deny), f"nothing caught {line!r}"
