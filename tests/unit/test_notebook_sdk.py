"""Tests for the Jupyter notebook SDK."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from agent.notebook.sdk import (
    CompilerHelper,
    ConditionHelper,
    ExportHelper,
    PipelineHelper,
    RegistryHelper,
    ScoringHelper,
    Session,
    _run,
)
from agent.pipeline.conditions import evaluate_condition
from agent.pipeline.models import (
    CommandType,
    Pipeline,
    PipelineStatus,
    PipelineStep,
    PipelineStepDef,
    PipelineTemplate,
    StepStatus,
)
from agent.pipeline.registry import ParameterDef, ParamType, ProgramEntry
from agent.pipeline.scoring import (
    Advisory,
    AdvisorySeverity,
    ExecutionOutcome,
    ExecutionScore,
    ProgramStats,
)


# ---------------------------------------------------------------------------
# _run helper
# ---------------------------------------------------------------------------


class TestRunHelper:
    def test_run_plain_coroutine(self) -> None:
        async def add(a: int, b: int) -> int:
            return a + b

        assert _run(add(2, 3)) == 5

    def test_run_returns_none(self) -> None:
        async def noop() -> None:
            pass

        assert _run(noop()) is None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class TestSession:
    def test_creates_session(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        s = Session(f"sqlite+aiosqlite:///{db_path}")
        assert s.registry is not None
        assert s.scoring is not None
        assert s.conditions is not None
        assert s.compiler is not None
        assert s.pipelines is not None
        assert s.export is not None
        s.close()

    def test_repr(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        s = Session(f"sqlite+aiosqlite:///{db_path}")
        assert "Session" in repr(s)
        s.close()


# ---------------------------------------------------------------------------
# ConditionHelper
# ---------------------------------------------------------------------------


class TestConditionHelper:
    def setup_method(self) -> None:
        self.helper = ConditionHelper()

    def test_basic_comparison(self) -> None:
        assert self.helper.test("resolution != 720", {"resolution": 1080}) is True
        assert self.helper.test("resolution != 720", {"resolution": 720}) is False

    def test_greater_than(self) -> None:
        assert self.helper.test("duration > 60", {"duration": 120}) is True
        assert self.helper.test("duration > 60", {"duration": 30}) is False

    def test_has_function(self) -> None:
        assert self.helper.test("has(timecodes)", {"timecodes": [1, 2]}) is True
        assert self.helper.test("has(timecodes)", {"timecodes": None}) is False
        assert self.helper.test("has(missing)", {}) is False

    def test_not_has(self) -> None:
        assert self.helper.test("not has(error)", {"error": None}) is True
        assert self.helper.test("not has(error)", {"error": "fail"}) is False

    def test_combined_and(self) -> None:
        ctx = {"resolution": 1080, "duration": 120}
        assert self.helper.test("resolution != 720 and duration > 60", ctx) is True
        assert self.helper.test("resolution != 720 and duration > 200", ctx) is False

    def test_combined_or(self) -> None:
        assert self.helper.test("face_count > 0 or duration < 30", {"face_count": 5, "duration": 300}) is True
        assert self.helper.test("face_count > 0 or duration < 30", {"face_count": 0, "duration": 10}) is True
        assert self.helper.test("face_count > 0 or duration < 30", {"face_count": 0, "duration": 300}) is False

    def test_boolean_literals(self) -> None:
        assert self.helper.test("true", {}) is True
        assert self.helper.test("false", {}) is False

    def test_empty_expression(self) -> None:
        assert self.helper.test("", {}) is True
        assert self.helper.test("  ", {}) is True

    def test_test_batch(self) -> None:
        ctx = {"resolution": 1080, "duration": 30}
        results = self.helper.test_batch(
            ["resolution != 720", "duration > 60"],
            ctx,
        )
        assert results == [
            ("resolution != 720", True),
            ("duration > 60", False),
        ]

    def test_explain_prints(self, capsys: Any) -> None:
        self.helper.explain("resolution != 720", {"resolution": 1080})
        captured = capsys.readouterr()
        assert "EXECUTE" in captured.out
        assert "resolution" in captured.out

    def test_show_operators_prints(self, capsys: Any) -> None:
        self.helper.show_operators()
        captured = capsys.readouterr()
        assert "Comparison" in captured.out
        assert "has(" in captured.out


# ---------------------------------------------------------------------------
# RegistryHelper (with DB)
# ---------------------------------------------------------------------------


class TestRegistryHelper:
    @pytest.fixture
    def session(self, tmp_path: Path) -> Session:
        db = tmp_path / "test.db"
        s = Session(f"sqlite+aiosqlite:///{db}")
        yield s
        s.close()

    def test_register_and_get(self, session: Session) -> None:
        prog = session.registry.register(
            name="test_prog",
            command_type="python",
            command_template="python test.py --input {input}",
            description="A test program",
            tags=["test"],
            parameters=["threshold:float:0.5:Confidence"],
        )
        assert prog.name == "test_prog"
        assert prog.command_type == CommandType.PYTHON
        assert len(prog.parameters) == 1
        assert prog.parameters[0].name == "threshold"
        assert prog.parameters[0].default == 0.5

        # Get it back
        fetched = session.registry.get("test_prog")
        assert fetched is not None
        assert fetched.name == "test_prog"

    def test_list(self, session: Session) -> None:
        session.registry.register(name="prog_a", command_template="echo a")
        session.registry.register(name="prog_b", command_template="echo b")
        programs = session.registry.list()
        names = [p.name for p in programs]
        assert "prog_a" in names
        assert "prog_b" in names

    def test_set_param(self, session: Session) -> None:
        session.registry.register(
            name="param_prog",
            command_template="echo {val}",
            parameters=["val:int:5:A value"],
        )
        entry = session.registry.set_param("param_prog", "val", 10)
        assert entry.get_parameter("val").current_value == 10

    def test_set_param_not_found(self, session: Session) -> None:
        with pytest.raises(KeyError):
            session.registry.set_param("nonexistent", "x", 1)

    def test_add_and_remove_param(self, session: Session) -> None:
        session.registry.register(name="ext_prog", command_template="echo ok")
        session.registry.add_param("ext_prog", "new_param:int:42:New parameter")
        prog = session.registry.get("ext_prog")
        assert prog.get_parameter("new_param") is not None
        assert prog.get_parameter("new_param").default == 42

        session.registry.remove_param("ext_prog", "new_param")
        prog = session.registry.get("ext_prog")
        assert prog.get_parameter("new_param") is None

    def test_add_duplicate_param_raises(self, session: Session) -> None:
        session.registry.register(
            name="dup_prog",
            command_template="echo ok",
            parameters=["x:int:1:X"],
        )
        with pytest.raises(ValueError, match="already exists"):
            session.registry.add_param("dup_prog", "x:int:2:Another X")

    def test_update(self, session: Session) -> None:
        session.registry.register(name="upd_prog", command_template="echo old", description="old")
        entry = session.registry.update("upd_prog", description="new desc", tags=["updated"])
        assert entry.description == "new desc"
        assert "updated" in entry.tags

    def test_deactivate(self, session: Session) -> None:
        session.registry.register(name="deact_prog", command_template="echo ok")
        result = session.registry.deactivate("deact_prog")
        assert result is True

    def test_search(self, session: Session) -> None:
        session.registry.register(
            name="face_det",
            command_template="python face.py",
            tags=["face"],
        )
        matches = session.registry.search("run face detection on video")
        names = [m.name for m in matches]
        assert "face_det" in names

    def test_show_prints(self, session: Session, capsys: Any) -> None:
        session.registry.register(
            name="show_prog",
            command_type="python",
            command_template="python show.py",
            description="Show test",
            parameters=["val:int:5:A value"],
        )
        session.registry.show("show_prog")
        captured = capsys.readouterr()
        assert "show_prog" in captured.out
        assert "python" in captured.out


# ---------------------------------------------------------------------------
# ScoringHelper (with DB)
# ---------------------------------------------------------------------------


class TestScoringHelper:
    @pytest.fixture
    def session(self, tmp_path: Path) -> Session:
        db = tmp_path / "test.db"
        s = Session(f"sqlite+aiosqlite:///{db}")
        yield s
        s.close()

    def test_inject_and_stats(self, session: Session) -> None:
        session.scoring.inject("test_prog", "success", {"threshold": 0.5}, duration=2.0)
        session.scoring.inject("test_prog", "success", {"threshold": 0.5}, duration=3.0)
        session.scoring.inject("test_prog", "failure", {"threshold": 0.5}, duration=0.5)

        stats = session.scoring.stats("test_prog")
        assert stats.total_runs == 3
        assert stats.successes == 2
        assert stats.failures == 1

    def test_inject_batch(self, session: Session) -> None:
        count = session.scoring.inject_batch("batch_prog", "success", {"a": 1}, count=5)
        assert count == 5
        stats = session.scoring.stats("batch_prog")
        assert stats.total_runs == 5

    def test_clear(self, session: Session) -> None:
        session.scoring.inject_batch("clear_prog", "success", count=3)
        deleted = session.scoring.clear("clear_prog")
        assert deleted == 3
        stats = session.scoring.stats("clear_prog")
        assert stats.total_runs == 0

    def test_param_sets(self, session: Session) -> None:
        session.scoring.inject_batch("ps_prog", "success", {"threshold": 0.3}, count=5)
        session.scoring.inject_batch("ps_prog", "zero_output", {"threshold": 0.9}, count=5)

        ps = session.scoring.param_sets("ps_prog")
        assert len(ps) == 2
        hashes = {p.parameters_hash for p in ps}
        assert len(hashes) == 2

    def test_advisories_with_enough_data(self, session: Session) -> None:
        # Inject enough data to trigger advisories
        session.scoring.inject_batch("adv_prog", "zero_output", {"t": 0.9}, count=10)
        advisories = session.scoring.advisories("adv_prog")
        assert len(advisories) > 0
        assert advisories[0].severity in {AdvisorySeverity.WARNING, AdvisorySeverity.CRITICAL}

    def test_show_prints(self, session: Session, capsys: Any) -> None:
        session.scoring.inject_batch("show_prog", "success", count=5)
        session.scoring.show("show_prog")
        captured = capsys.readouterr()
        assert "show_prog" in captured.out
        assert "5 runs" in captured.out

    def test_all_advisories(self, session: Session) -> None:
        session.scoring.inject_batch("all_adv_a", "zero_output", count=10)
        session.scoring.inject_batch("all_adv_b", "success", count=10)
        all_adv = session.scoring.all_advisories()
        assert "all_adv_a" in all_adv  # has issues
        # all_adv_b may or may not have advisories


# ---------------------------------------------------------------------------
# CompilerHelper
# ---------------------------------------------------------------------------


class TestCompilerHelper:
    @pytest.fixture
    def session(self, tmp_path: Path) -> Session:
        db = tmp_path / "test.db"
        s = Session(f"sqlite+aiosqlite:///{db}")
        yield s
        s.close()

    def test_from_steps(self, session: Session) -> None:
        pipeline, steps = session.compiler.from_steps(
            [
                {"name": "step_a", "command_type": "shell", "command_template": "echo a"},
                {"name": "step_b", "command_type": "python",
                 "command_template": "python b.py",
                 "depends_on_names": ["step_a"]},
            ],
            name="Test Pipeline",
            inputs={"key": "value"},
        )
        assert pipeline.name == "Test Pipeline"
        assert len(steps) == 2
        assert steps[0].name == "step_a"
        assert steps[1].name == "step_b"
        assert len(steps[1].depends_on) == 1  # resolved to UUID

    def test_from_steps_with_conditions(self, session: Session) -> None:
        pipeline, steps = session.compiler.from_steps(
            [
                {"name": "resize", "command_type": "ffmpeg",
                 "command_template": "ffmpeg -i {in} {out}",
                 "condition": "resolution != 720"},
            ],
        )
        assert steps[0].condition == "resolution != 720"

    def test_from_steps_with_fan_out(self, session: Session) -> None:
        pipeline, steps = session.compiler.from_steps(
            [
                {"name": "detect", "command_type": "python",
                 "command_template": "python detect.py",
                 "fan_out_on": "clips"},
            ],
        )
        assert steps[0].fan_out_on == "clips"

    def test_preview_prompt(self, session: Session) -> None:
        # Register a program so it appears in the prompt
        session.registry.register(
            name="facedetection",
            command_type="python",
            command_template="python face.py --threshold {threshold}",
            description="Detect faces",
            tags=["face"],
            parameters=["threshold:float:0.5:Confidence"],
        )
        prompt = session.compiler.preview_prompt(
            "Detect faces in the video",
            {"video": "/test.mp4"},
        )
        assert "facedetection" in prompt
        assert "threshold" in prompt


# ---------------------------------------------------------------------------
# PipelineHelper
# ---------------------------------------------------------------------------


class TestPipelineHelper:
    @pytest.fixture
    def session(self, tmp_path: Path) -> Session:
        db = tmp_path / "test.db"
        s = Session(f"sqlite+aiosqlite:///{db}")
        yield s
        s.close()

    def test_inspect_prints(self, session: Session, capsys: Any) -> None:
        pipeline, steps = session.compiler.from_steps(
            [{"name": "echo", "command_type": "shell", "command_template": "echo ok"}],
            name="Test",
        )
        session.pipelines.inspect(pipeline, steps)
        captured = capsys.readouterr()
        assert "Test" in captured.out
        assert "echo" in captured.out

    def test_inspect_step_prints(self, session: Session, capsys: Any) -> None:
        _, steps = session.compiler.from_steps(
            [{"name": "cond_step", "command_type": "shell",
              "command_template": "echo ok",
              "condition": "x > 5"}],
        )
        session.pipelines.inspect_step(steps[0], context={"x": 10})
        captured = capsys.readouterr()
        assert "EXECUTE" in captured.out

    def test_edit_step(self, session: Session) -> None:
        _, steps = session.compiler.from_steps(
            [{"name": "edit_me", "command_type": "shell", "command_template": "echo old"}],
        )
        session.pipelines.edit_step(steps[0], command_template="echo new", timeout=600)
        assert steps[0].command_template == "echo new"
        assert steps[0].timeout == 600

    def test_save_and_list_templates(self, session: Session) -> None:
        pipeline, steps = session.compiler.from_steps(
            [{"name": "s1", "command_type": "shell", "command_template": "echo ok"}],
            name="My Template",
        )
        template = session.pipelines.save_template(
            pipeline=pipeline, steps=steps, name="My Template"
        )
        assert template.name == "My Template"

        templates = session.pipelines.list_templates()
        assert len(templates) >= 1
        assert any(t.name == "My Template" for t in templates)

    def test_clone_template(self, session: Session) -> None:
        pipeline, steps = session.compiler.from_steps(
            [{"name": "s1", "command_type": "shell", "command_template": "echo {val}"}],
        )
        template = session.pipelines.save_template(pipeline=pipeline, steps=steps)

        new_pipeline, new_steps = session.pipelines.clone_template(
            template, {"val": "hello"}
        )
        assert new_pipeline.id != pipeline.id
        assert new_pipeline.inputs["val"] == "hello"

    def test_delete_template(self, session: Session) -> None:
        pipeline, steps = session.compiler.from_steps(
            [{"name": "s1", "command_type": "shell", "command_template": "echo ok"}],
        )
        template = session.pipelines.save_template(pipeline=pipeline, steps=steps)
        result = session.pipelines.delete_template(str(template.id))
        assert result is True


# ---------------------------------------------------------------------------
# ExportHelper
# ---------------------------------------------------------------------------


class TestExportHelper:
    @pytest.fixture
    def session(self, tmp_path: Path) -> Session:
        db = tmp_path / "test.db"
        s = Session(f"sqlite+aiosqlite:///{db}")
        yield s
        s.close()

    def test_export_pipeline(self, session: Session, tmp_path: Path) -> None:
        pipeline, steps = session.compiler.from_steps(
            [{"name": "s1", "command_type": "shell", "command_template": "echo ok"}],
            name="Export Test",
        )
        out_path = str(tmp_path / "pipeline.json")
        result = session.export.pipeline((pipeline, steps), out_path)

        data = json.loads(Path(result).read_text())
        assert data["name"] == "Export Test"
        assert len(data["steps"]) == 1

    def test_export_registry(self, session: Session, tmp_path: Path) -> None:
        session.registry.register(name="exp_prog", command_template="echo ok", tags=["test"])
        out_path = str(tmp_path / "programs.json")
        result = session.export.registry(out_path)

        data = json.loads(Path(result).read_text())
        assert len(data) >= 1
        assert any(p["name"] == "exp_prog" for p in data)

    def test_export_docker_bundle(self, session: Session, tmp_path: Path) -> None:
        pipeline, steps = session.compiler.from_steps(
            [{"name": "s1", "command_type": "shell", "command_template": "echo ok"}],
            name="Docker Test",
        )
        out_dir = str(tmp_path / "bundle")
        result = session.export.docker_bundle((pipeline, steps), out_dir)

        bundle = Path(result)
        assert (bundle / "pipeline.json").exists()
        assert (bundle / "Dockerfile").exists()
        assert (bundle / "docker-compose.yml").exists()
        assert (bundle / "requirements.txt").exists()

        dockerfile = (bundle / "Dockerfile").read_text()
        assert "python:3.11-slim" in dockerfile
