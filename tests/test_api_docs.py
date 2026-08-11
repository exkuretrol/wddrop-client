"""
The API's own documentation.

FastAPI generates `/docs` and `/openapi.json` from the code, so a copy in the repository can
only ever be a second source of truth. It is kept anyway, because a spec that can be read
WITHOUT running the server is what makes an endpoint changing shape show up in a diff — and
that is worth nothing if it is allowed to go stale, which is what `--check` is for.

No database here, deliberately: these are about the shape of the API, and gating them behind
a live Postgres would mean they only ran where nobody was looking.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "packages" / "schema"))

import pytest  # noqa: E402

pytest.importorskip("fastapi", reason="fastapi not installed")

def test_the_generated_api_docs_match_the_code():
    """`/docs` and `/openapi.json` are generated from the code, so a copy in the repository
    can only be a second source of truth. It is here for review — an endpoint changing shape
    should show up in a diff — which is worth nothing if it is allowed to go stale."""
    root = ROOT
    if not (root / "tools" / "api_docs.py").exists():
        pytest.skip("tools/ not present")
    done = subprocess.run([sys.executable, "tools/api_docs.py", "--check"],
                          cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_endpoint_says_what_it_is_for():
    """An operation with no summary and no description is a line in the spec that tells a
    reader nothing — and this spec exists to be read by someone writing another client."""
    from wddrop_server.main import app

    api = app.openapi()
    bare = [f"{method.upper()} {path}"
            for path, methods in api["paths"].items()
            for method, operation in methods.items()
            if not (operation.get("summary") and operation.get("description"))]
    assert not bare, f"undocumented: {bare}"
