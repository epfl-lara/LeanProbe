from __future__ import annotations

import os
import shutil

import pytest

from lean_probe.core import LeanProbe


pytestmark = pytest.mark.integration


def test_real_lean_interact_check_target_smoke(tmp_path):
    if os.environ.get("LEAN_PROBE_RUN_INTEGRATION") != "1":
        pytest.skip("set LEAN_PROBE_RUN_INTEGRATION=1 to run real LeanInteract smoke test")
    pytest.importorskip("lean_interact")
    if shutil.which("lake") is None:
        pytest.skip("lake executable not found")

    project = tmp_path / "Demo"
    module_dir = project / "Demo"
    module_dir.mkdir(parents=True)
    (project / "lakefile.lean").write_text(
        "import Lake\nopen Lake DSL\n\npackage Demo\n\nlean_lib Demo\n",
        encoding="utf-8",
    )
    target = module_dir / "Main.lean"
    target.write_text("theorem demo : True := by\n  trivial\n", encoding="utf-8")

    probe = LeanProbe(auto_build=True)
    try:
        payload = probe.check_target(target, theorem_id="demo", cwd=project, timeout_s=60)
    finally:
        probe.close()

    assert payload["success"] is True
    assert payload["ok"] is True
