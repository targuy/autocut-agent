"""Tests for the Program Registry."""

from __future__ import annotations

import uuid

from agent.pipeline.models import CommandType
from agent.pipeline.registry import (
    ParameterDef,
    ParamType,
    ProgramEntry,
    ProgramQuestion,
    ProgramRegistry,
    generate_questions_for_unknown,
    parse_parameter_spec,
)


class TestParameterDef:
    def test_effective_value_current(self) -> None:
        p = ParameterDef(name="threshold", default=0.5, current_value=0.8)
        assert p.effective_value() == 0.8

    def test_effective_value_default(self) -> None:
        p = ParameterDef(name="threshold", default=0.5)
        assert p.effective_value() == 0.5

    def test_effective_value_none(self) -> None:
        p = ParameterDef(name="x")
        assert p.effective_value() is None

    def test_validate_int_range(self) -> None:
        p = ParameterDef(name="n", type=ParamType.INT, min_value=1, max_value=10)
        ok, _ = p.validate_value(5)
        assert ok
        ok, err = p.validate_value(0)
        assert not ok
        assert ">=" in err
        ok, err = p.validate_value(11)
        assert not ok
        assert "<=" in err

    def test_validate_float_range(self) -> None:
        p = ParameterDef(name="f", type=ParamType.FLOAT, min_value=0.0, max_value=1.0)
        ok, _ = p.validate_value(0.5)
        assert ok
        ok, _ = p.validate_value(-0.1)
        assert not ok

    def test_validate_enum(self) -> None:
        p = ParameterDef(
            name="mode", type=ParamType.ENUM, allowed_values=["fast", "slow"]
        )
        ok, _ = p.validate_value("fast")
        assert ok
        ok, err = p.validate_value("turbo")
        assert not ok
        assert "must be one of" in err

    def test_validate_required(self) -> None:
        p = ParameterDef(name="req", required=True)
        ok, err = p.validate_value(None)
        assert not ok
        assert "required" in err

    def test_validate_not_required_none(self) -> None:
        p = ParameterDef(name="opt", required=False)
        ok, _ = p.validate_value(None)
        assert ok

    def test_validate_bool(self) -> None:
        p = ParameterDef(name="flag", type=ParamType.BOOL)
        ok, _ = p.validate_value(True)
        assert ok
        ok, _ = p.validate_value("not_bool")
        assert not ok


class TestProgramEntry:
    def _make_entry(self) -> ProgramEntry:
        return ProgramEntry(
            name="facedetection",
            description="Detects faces in video frames",
            purpose="Filter clips by face presence",
            command_type=CommandType.PYTHON,
            command_template="python facedetection.py --input {input_path} --threshold {threshold}",
            required_inputs=["clip_path"],
            expected_outputs=["filtered_timecodes", "face_count"],
            parameters=[
                ParameterDef(
                    name="threshold",
                    type=ParamType.FLOAT,
                    default=0.5,
                    min_value=0.0,
                    max_value=1.0,
                    description="Face detection confidence threshold",
                ),
                ParameterDef(
                    name="input_path",
                    type=ParamType.PATH,
                    required=True,
                    description="Path to the video clip",
                ),
            ],
            tags=["filter", "video", "gpu"],
        )

    def test_get_parameter(self) -> None:
        entry = self._make_entry()
        p = entry.get_parameter("threshold")
        assert p is not None
        assert p.type == ParamType.FLOAT
        assert entry.get_parameter("nonexistent") is None

    def test_get_effective_params(self) -> None:
        entry = self._make_entry()
        params = entry.get_effective_params()
        assert params["threshold"] == 0.5
        assert params["input_path"] is None

    def test_set_parameter_valid(self) -> None:
        entry = self._make_entry()
        ok, err = entry.set_parameter("threshold", 0.8)
        assert ok
        assert err == ""
        assert entry.get_parameter("threshold").current_value == 0.8

    def test_set_parameter_invalid(self) -> None:
        entry = self._make_entry()
        ok, err = entry.set_parameter("threshold", 1.5)
        assert not ok
        assert "<=" in err

    def test_set_parameter_unknown(self) -> None:
        entry = self._make_entry()
        ok, err = entry.set_parameter("nonexistent", 42)
        assert not ok
        assert "Unknown parameter" in err

    def test_resolve_command(self) -> None:
        entry = self._make_entry()
        entry.set_parameter("threshold", 0.7)
        cmd = entry.resolve_command(overrides={"input_path": "/video/clip.mp4"})
        assert "0.7" in cmd
        assert "/video/clip.mp4" in cmd

    def test_resolve_command_defaults(self) -> None:
        entry = self._make_entry()
        cmd = entry.resolve_command()
        assert "0.5" in cmd  # default threshold

    def test_to_compiler_context(self) -> None:
        entry = self._make_entry()
        ctx = entry.to_compiler_context()
        assert ctx["name"] == "facedetection"
        assert ctx["command_type"] == "python"
        assert len(ctx["parameters"]) == 2
        assert ctx["tags"] == ["filter", "video", "gpu"]
        assert "clip_path" in ctx["required_inputs"]


class TestProgramRegistry:
    def test_cache_operations(self) -> None:
        """Test in-memory cache without DB."""
        registry = ProgramRegistry(session_factory=None)

        entry = ProgramEntry(
            name="test_prog",
            description="Test",
            command_type=CommandType.SHELL,
            command_template="echo test",
            tags=["test"],
        )
        # Direct cache manipulation (simulate loaded state)
        registry._cache[entry.name] = entry

        assert registry.lookup("test_prog") is not None
        assert registry.lookup("nonexistent") is None

    def test_find_programs_for_description(self) -> None:
        registry = ProgramRegistry(session_factory=None)

        prog1 = ProgramEntry(
            name="facedetection",
            description="Detects faces",
            command_type=CommandType.PYTHON,
            command_template="python facedetection.py",
            tags=["filter", "face"],
        )
        prog2 = ProgramEntry(
            name="ffmpeg_resize",
            description="Resize video",
            command_type=CommandType.FFMPEG,
            command_template="ffmpeg -i {input} -vf scale=1280:720 {output}",
            tags=["resize", "scaling"],
        )
        registry._cache = {prog1.name: prog1, prog2.name: prog2}

        # Match by program name
        matches = registry.find_programs_for_description(
            "run facedetection on each clip"
        )
        assert len(matches) == 1
        assert matches[0].name == "facedetection"

        # Match by tag
        matches = registry.find_programs_for_description("resize the image")
        assert len(matches) == 1
        assert matches[0].name == "ffmpeg_resize"

        # Match multiple (by name + tag)
        matches = registry.find_programs_for_description(
            "facedetection then resize the output"
        )
        assert len(matches) == 2

    def test_lookup_by_tag(self) -> None:
        registry = ProgramRegistry(session_factory=None)
        prog = ProgramEntry(
            name="test",
            command_type=CommandType.SHELL,
            command_template="echo",
            tags=["gpu", "filter"],
        )
        registry._cache = {prog.name: prog}

        assert len(registry.lookup_by_tag("gpu")) == 1
        assert len(registry.lookup_by_tag("cpu")) == 0

    def test_get_all_cached(self) -> None:
        registry = ProgramRegistry(session_factory=None)
        prog = ProgramEntry(
            name="test",
            command_type=CommandType.SHELL,
            command_template="echo",
        )
        registry._cache = {prog.name: prog}
        assert len(registry.get_all_cached()) == 1


class TestGenerateQuestions:
    def test_generates_required_questions(self) -> None:
        questions = generate_questions_for_unknown("new_program")
        assert len(questions) > 0
        assert all(isinstance(q, ProgramQuestion) for q in questions)

        # Should ask about description, command type, and command template
        fields = {q.field for q in questions}
        assert "description" in fields
        assert "command_type" in fields
        assert "command_template" in fields

    def test_includes_program_name(self) -> None:
        questions = generate_questions_for_unknown("my_filter")
        for q in questions:
            if q.field == "description":
                assert "my_filter" in q.question


class TestParseParameterSpec:
    def test_full_spec(self) -> None:
        p = parse_parameter_spec("threshold:float:0.5:Confidence level")
        assert p.name == "threshold"
        assert p.type == ParamType.FLOAT
        assert p.default == 0.5
        assert p.description == "Confidence level"

    def test_int_spec(self) -> None:
        p = parse_parameter_spec("count:int:10:Number of items")
        assert p.name == "count"
        assert p.type == ParamType.INT
        assert p.default == 10

    def test_bool_spec(self) -> None:
        p = parse_parameter_spec("verbose:bool:true:Enable verbose output")
        assert p.name == "verbose"
        assert p.type == ParamType.BOOL
        assert p.default is True

    def test_name_only(self) -> None:
        p = parse_parameter_spec("simple")
        assert p.name == "simple"
        assert p.type == ParamType.STRING
        assert p.default is None

    def test_name_and_type(self) -> None:
        p = parse_parameter_spec("path:path")
        assert p.name == "path"
        assert p.type == ParamType.PATH
