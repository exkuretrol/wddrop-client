"""The build's dependency list must not drift from what the client declares.

`build_exe.py` keeps `RUNTIME_IMPORTS` by hand so it needs no toml reader, and its comment
said "kept in step by test_build.py" — which did not exist. It drifted the moment the wire
format stopped being a folder on `--paths` and became an installed dependency: nothing added
it to the build list, so the build environment never installed it, PyInstaller's analysis of
`wddrop_client` stopped at that import, and `pydantic` — which nothing in the client imports
directly, only through the schema — was left out of the bundle with it.

The exe still BUILT. The bundle's own self-check is what failed, in CI, after four minutes.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # build_exe is a script at the root, not a package

import build_exe  # noqa: E402


def _name(requirement: str) -> str:
    """The PEP 503 normalised name out of a requirement string, markers and URL discarded."""
    head = re.split(r"[\[<>=!~;@ ]", requirement.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", head).lower()


def _declared() -> set[str]:
    """Everything the client needs AT RUNTIME: the required set plus the optional modes.

    `dev` is left out — pytest is not in the bundle. PySide6 is not in pyproject at all, on
    purpose (see wddrop.py), so this is a one-way check: what is declared must be built, not
    the reverse.
    """
    data = tomllib.loads((ROOT / "client" / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    wanted = list(project.get("dependencies", []))
    for extra, requirements in project.get("optional-dependencies", {}).items():
        if extra != "dev":
            wanted += requirements
    return {_name(r) for r in wanted}


def test_every_runtime_dependency_reaches_the_build():
    """The failure this file exists for: a dependency the client declares that the build
    environment never installs is not a build error — it is a bundle that starts and cannot
    record."""
    listed = {_name(install) for _module, install, _why in build_exe.RUNTIME_IMPORTS}
    missing = sorted(_declared() - listed)
    assert not missing, (
        "declared in client/pyproject.toml but not in build_exe.RUNTIME_IMPORTS:\n  "
        + "\n  ".join(missing))


def test_a_dependency_no_index_can_resolve_carries_its_url():
    """`wddrop-schema` is not on PyPI. Listed by bare name it would install as whatever an
    index happens to hold under that name, or fail the build — and the first of those is
    worse than the second."""
    for _module, install, _why in build_exe.RUNTIME_IMPORTS:
        if _name(install) == "wddrop-schema":
            assert " @ http" in install, f"{install!r} names no source pip can fetch"
            break
    else:
        raise AssertionError("the wire format is not in the build list at all")


# -- the licence has to be IN the distribution, not only named by it ------------------

def test_the_licence_files_the_package_ships_match_the_ones_at_the_root():
    """Two copies, and a guard so they cannot drift.

    GitHub detects a licence only at the repository root; hatchling can only reach files
    under its own project root, which is `client/`. So both places need them — and
    `license-files` fails SILENTLY when a path is not there: the wheel built fine, said
    `License-Expression: Apache-2.0`, and carried no licence text at all. Apache-2.0 §4(a)
    wants recipients to actually get a copy, so the quiet version is the wrong one.

    A symlink would avoid the copy and was rejected: the release builds on Windows, where a
    checked-out symlink can materialise as a text file containing `../LICENSE`, and the wheel
    would then ship that as its licence without anything failing.
    """
    for name in ("LICENSE", "NOTICE"):
        root = (ROOT / name).read_bytes()
        packaged = (ROOT / "client" / name).read_bytes()
        assert root == packaged, (
            f"client/{name} has drifted from {name} at the root — copy the root one over it")


# -- what the exe says about itself ---------------------------------------------------

def _evaluate_version_resource(path):
    """Load it the way PyInstaller does — `eval` with the versioninfo names in scope.

    PyInstaller's own loader is Windows-gated (it imports `win32api`), so it cannot be used
    to check this off Windows. Stand-in classes prove the SHAPE, which is the part a build
    would choke on; whether the PE resource is written correctly is the Windows job's to say.
    """
    body = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                     if not l.startswith("#"))
    strings = {}

    class _Node:
        def __init__(self, *args, **kwargs):
            pass

    class _StringStruct:
        def __init__(self, name, value):
            strings[name] = value

    scope = {name: type(name, (_Node,), {}) for name in
             ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable",
              "VarFileInfo", "VarStruct")}
    scope["StringStruct"] = _StringStruct
    eval(body, scope)
    return strings


def test_the_exe_says_who_made_it_and_which_version_it_is(tmp_path):
    """0.9.2 was flagged `Trojan:Win32/Wacatac.C!ml` — a generic ML verdict, not a signature
    match. An unsigned one-file bundle with no prevalence starts out suspicious, and one that
    also declines to name its publisher, product or version is a blank to everything that
    looks at it, the Properties dialog a wary player opens included.

    This is not the fix — signing is, and a developer submission clears a specific detection.
    It removes one weight and costs nothing, so the thing worth guarding is that it is
    actually there and actually says the version this build IS.
    """
    from build_exe import _client_version, version_resource

    strings = _evaluate_version_resource(
        version_resource(tmp_path / "v.txt", _client_version()))

    assert strings["FileVersion"] == _client_version()
    assert strings["ProductVersion"] == _client_version()
    for field in ("CompanyName", "FileDescription", "ProductName", "LegalCopyright",
                  "OriginalFilename", "InternalName"):
        assert strings.get(field), f"{field} is empty, which is what this exists to prevent"


def test_the_version_quad_is_derived_from_the_version_and_not_typed_twice(tmp_path):
    """A fourth copy of the number would agree with the other three until it did not, and
    the one place it would surface is a Properties dialog nobody reads."""
    from build_exe import version_resource

    text = version_resource(tmp_path / "v.txt", "1.2.3").read_text(encoding="utf-8")
    assert "filevers=(1, 2, 3, 0)" in text
    assert "prodvers=(1, 2, 3, 0)" in text
    assert _evaluate_version_resource(tmp_path / "v.txt")["FileVersion"] == "1.2.3"


def test_upx_is_decided_here_rather_than_by_the_runner_s_PATH():
    """PyInstaller uses UPX if it happens to find it, so whether the exe shipped packed was a
    property of the build image rather than a decision — and a packed binary is one more
    weight against an unsigned bundle already being called a trojan."""
    source = (ROOT / "build_exe.py").read_text(encoding="utf-8")
    assert '"--noupx"' in source
