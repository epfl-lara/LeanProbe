from __future__ import annotations

import json
from types import SimpleNamespace

from lean_probe import cli, core


def _install_fake_lean_interact(monkeypatch):
    class _Command:
        def __init__(self, *, cmd: str, all_tactics: bool = False, env=None):
            self.cmd = cmd
            self.all_tactics = all_tactics
            self.env = env

        def model_copy(self, *, update):
            copied = _Command(cmd=self.cmd, all_tactics=self.all_tactics, env=self.env)
            for key, value in update.items():
                setattr(copied, key, value)
            return copied

    class _ProofStep:
        def __init__(self, *, proof_state: int, tactic: str):
            self.proof_state = proof_state
            self.tactic = tactic

    class _Response:
        def __init__(self, *, env: int = 1, sorries=None, tactics=None):
            self.env = env
            self.messages: list[object] = []
            self.sorries = sorries or []
            self.tactics = tactics or []

        def has_errors(self):
            return False

        def lean_code_is_valid(self, *, allow_sorry: bool = False):
            return allow_sorry or not self.sorries

    class _StepResponse:
        proof_state = 77
        goals = []
        proof_status = "Completed"

    class _Server:
        def __init__(self, config):
            self.config = config

        def run(self, request, timeout=None):
            if isinstance(request, _ProofStep):
                return _StepResponse()
            if "sorry" in request.cmd:
                pos = SimpleNamespace(line=1, column=40)
                sorry = SimpleNamespace(start_pos=pos, end_pos=pos, goal="n : Nat\n⊢ n = n", proof_state=5)
                return _Response(sorries=[sorry])
            tactics = []
            if request.all_tactics:
                pos = SimpleNamespace(line=1, column=0)
                tactics.append(
                    SimpleNamespace(
                        tactic="trivial",
                        goals="⊢ True",
                        proof_state="⊢ True",
                        start_pos=pos,
                        end_pos=pos,
                        used_constants=[],
                    )
                )
            return _Response(tactics=tactics)

        def kill(self):
            pass

    class _Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Project:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(core, "_import_lean_interact", lambda: (_Command, _ProofStep, _Config, _Server, _Project, ""))
    monkeypatch.setattr(core, "_local_repl_dir", lambda project_root: project_root / ".lake" / "packages" / "repl")


def test_cli_check_outputs_json(monkeypatch, tmp_path, capsys):
    _install_fake_lean_interact(monkeypatch)
    project = tmp_path / "Demo"
    module_dir = project / "Demo"
    module_dir.mkdir(parents=True)
    (project / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
    target = module_dir / "Main.lean"
    target.write_text("theorem demo : True := by\n  trivial\n", encoding="utf-8")

    code = cli.main(["check", str(target), "demo", "--cwd", str(project)])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["tool"] == "lean_probe"
    assert output["action"] == "check"
    assert output["target"] == "demo"


def test_cli_prepare_and_feedback_pretty(monkeypatch, tmp_path, capsys):
    _install_fake_lean_interact(monkeypatch)
    project = tmp_path / "Demo"
    module_dir = project / "Demo"
    module_dir.mkdir(parents=True)
    (project / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
    target = module_dir / "Main.lean"
    target.write_text("theorem demo : True := by\n  trivial\n", encoding="utf-8")

    prepare_code = cli.main(["prepare", str(target), "--theorem-id", "demo", "--cwd", str(project), "--pretty"])
    prepare_text = capsys.readouterr().out
    feedback_code = cli.main(["feedback", str(target), "demo", "--cwd", str(project), "--pretty"])
    feedback_text = capsys.readouterr().out

    assert prepare_code == 0
    assert '\n  "action": "prepare"' in prepare_text
    assert feedback_code == 0
    feedback = json.loads(feedback_text)
    assert feedback["action"] == "feedback"
    assert feedback["tactics"]


def test_cli_replacement_file_and_failure_exit_code(monkeypatch, tmp_path, capsys):
    _install_fake_lean_interact(monkeypatch)
    project = tmp_path / "Demo"
    module_dir = project / "Demo"
    module_dir.mkdir(parents=True)
    (project / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
    target = module_dir / "Main.lean"
    target.write_text("theorem demo : True := by\n  trivial\n", encoding="utf-8")
    replacement = tmp_path / "replacement.lean"
    replacement.write_text("theorem demo : True := by\n  trivial\n", encoding="utf-8")

    ok_code = cli.main(["check", str(target), "demo", "--cwd", str(project), "--replacement-file", str(replacement)])
    capsys.readouterr()
    bad_code = cli.main(["check", str(target), "missing", "--cwd", str(project)])
    output = json.loads(capsys.readouterr().out)

    assert ok_code == 0
    assert bad_code == 1
    assert output["error_code"] == "target_not_found"


def test_cli_state_reads_stdin(monkeypatch, tmp_path, capsys):
    _install_fake_lean_interact(monkeypatch)
    project = tmp_path / "Demo"
    project.mkdir()
    (project / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")

    class _Stdin:
        def read(self):
            return "theorem demo : True := by sorry"

    monkeypatch.setattr(cli.sys, "stdin", _Stdin())
    code = cli.main(["state", "--cwd", str(project)])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["action"] == "state"
    assert output["session_id"]


def test_cli_tactic_script_runs_steps(monkeypatch, tmp_path, capsys):
    _install_fake_lean_interact(monkeypatch)
    project = tmp_path / "Demo"
    project.mkdir()
    (project / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")

    code = cli.main(
        [
            "tactic-script",
            "--cwd",
            str(project),
            "--code",
            "theorem ex (n : Nat) : n = n := by sorry",
            "--tactic",
            "rfl",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["steps"][0]["proof_status"] == "Completed"


def test_cli_version_uses_package_version(capsys):
    try:
        cli.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0

    assert capsys.readouterr().out.startswith("lean-probe 0.1.0")
