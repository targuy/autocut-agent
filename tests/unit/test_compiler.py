"""Tests for the PipelineCompiler (non-LLM parts)."""

from __future__ import annotations

import json

import pytest

from agent.core.config import LLMConfig
from agent.pipeline.compiler import (
    CompilationResult,
    PipelineCompiler,
    _extract_json,
)
from agent.pipeline.models import CommandType
from agent.pipeline.registry import (
    ParameterDef,
    ParamType,
    ProgramEntry,
    ProgramRegistry,
)


class TestExtractJson:
    def test_plain_json(self) -> None:
        result = _extract_json('{"name": "test", "steps": []}')
        assert result == {"name": "test", "steps": []}

    def test_json_in_code_fence(self) -> None:
        text = '```json\n{"name": "test", "steps": []}\n```'
        result = _extract_json(text)
        assert result["name"] == "test"

    def test_json_with_surrounding_text(self) -> None:
        text = 'Here is the pipeline:\n{"name": "test", "steps": []}\nDone.'
        result = _extract_json(text)
        assert result["name"] == "test"

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not extract"):
            _extract_json("no json here")


class TestPayloadToTemplate:
    def test_basic_payload(self) -> None:
        payload = {
            "name": "Test Pipeline",
            "steps": [
                {
                    "name": "step1",
                    "command_type": "shell",
                    "command_template": "echo hello",
                }
            ],
        }
        template = PipelineCompiler._payload_to_template(payload)
        assert template.name == "Test Pipeline"
        assert len(template.steps) == 1
        assert template.steps[0].name == "step1"

    def test_empty_steps_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one step"):
            PipelineCompiler._payload_to_template({"name": "Empty", "steps": []})

    def test_missing_command_template_raises(self) -> None:
        with pytest.raises(ValueError, match="missing a command_template"):
            PipelineCompiler._payload_to_template({
                "name": "Bad",
                "steps": [{"name": "s1", "command_type": "shell"}],
            })

    def test_duplicate_names_deduplicated(self) -> None:
        payload = {
            "name": "Dupes",
            "steps": [
                {"name": "step", "command_type": "shell", "command_template": "echo 1"},
                {"name": "step", "command_type": "shell", "command_template": "echo 2"},
            ],
        }
        template = PipelineCompiler._payload_to_template(payload)
        names = [s.name for s in template.steps]
        assert len(set(names)) == 2  # names are unique

    def test_with_known_program(self) -> None:
        """When a step references a known program, its context is used."""
        prog = ProgramEntry(
            name="facedetect",
            command_type=CommandType.PYTHON,
            command_template="python facedetect.py --threshold {threshold}",
            parameters=[
                ParameterDef(name="threshold", type=ParamType.FLOAT, default=0.5)
            ],
        )
        payload = {
            "name": "Pipeline With Program",
            "steps": [
                {
                    "name": "detect",
                    "program_name": "facedetect",
                    "command_type": "python",
                    "command_template": "",  # empty — should use program's template
                }
            ],
        }
        template = PipelineCompiler._payload_to_template(payload, [prog])
        step = template.steps[0]
        assert "facedetect.py" in step.command_template
        assert step.command_type == CommandType.PYTHON


class TestBuildUserMessage:
    def test_simple_description(self) -> None:
        msg = PipelineCompiler._build_user_message("Do something", {})
        assert "Do something" in msg
        assert "KNOWN PROGRAMS" not in msg

    def test_with_inputs(self) -> None:
        msg = PipelineCompiler._build_user_message(
            "Process video", {"video_path": "/input.mp4"}
        )
        assert "video_path" in msg
        assert "/input.mp4" in msg

    def test_with_known_programs(self) -> None:
        prog = ProgramEntry(
            name="myfilter",
            description="Filters clips by criteria",
            purpose="Remove unwanted clips",
            command_type=CommandType.PYTHON,
            command_template="python myfilter.py --level {level}",
            required_inputs=["clip_path"],
            expected_outputs=["filtered_clips"],
            parameters=[
                ParameterDef(
                    name="level",
                    type=ParamType.INT,
                    default=5,
                    description="Filter aggressiveness",
                )
            ],
        )
        msg = PipelineCompiler._build_user_message("Filter clips", {}, [prog])
        assert "KNOWN PROGRAMS" in msg
        assert "myfilter" in msg
        assert "Filters clips by criteria" in msg
        assert "level (int)" in msg
        assert "[default: 5]" in msg
        assert "clip_path" in msg
        assert "filtered_clips" in msg

    def test_with_current_value(self) -> None:
        prog = ProgramEntry(
            name="prog",
            command_type=CommandType.SHELL,
            command_template="echo {x}",
            parameters=[
                ParameterDef(name="x", default=1, current_value=99)
            ],
        )
        msg = PipelineCompiler._build_user_message("Run prog", {}, [prog])
        assert "[current: 99]" in msg


class TestCompilationResult:
    def test_complete_result(self) -> None:
        from agent.pipeline.models import Pipeline, PipelineStatus

        p = Pipeline(name="test", status=PipelineStatus.PENDING)
        result = CompilationResult(pipeline=p, steps=[])
        assert result.is_complete
        assert not result.needs_user_input

    def test_incomplete_result(self) -> None:
        from agent.pipeline.registry import ProgramQuestion

        result = CompilationResult(
            pending_questions={
                "unknown_prog": [
                    ProgramQuestion(field="description", question="What does it do?")
                ]
            },
            unknown_programs=["unknown_prog"],
        )
        assert not result.is_complete
        assert result.needs_user_input
        assert "unknown_prog" in result.pending_questions


class TestInstantiateFromTemplate:
    def test_resolves_dependencies(self) -> None:
        from agent.pipeline.models import PipelineStepDef, PipelineTemplate

        template = PipelineTemplate(
            name="test",
            steps=[
                PipelineStepDef(
                    name="a",
                    command_type=CommandType.SHELL,
                    command_template="echo a",
                ),
                PipelineStepDef(
                    name="b",
                    command_type=CommandType.SHELL,
                    command_template="echo b",
                    depends_on_names=["a"],
                ),
            ],
        )
        pipeline, steps = PipelineCompiler._instantiate_from_template(template, {})
        assert len(steps) == 2
        assert steps[1].depends_on == [steps[0].id]

    def test_unknown_dependency_raises(self) -> None:
        from agent.pipeline.compiler import CompilationError
        from agent.pipeline.models import PipelineStepDef, PipelineTemplate

        template = PipelineTemplate(
            name="test",
            steps=[
                PipelineStepDef(
                    name="a",
                    command_type=CommandType.SHELL,
                    command_template="echo a",
                    depends_on_names=["nonexistent"],
                ),
            ],
        )
        with pytest.raises(CompilationError, match="unknown step"):
            PipelineCompiler._instantiate_from_template(template, {})


class TestDetectUnknownPrograms:
    def test_no_registry_returns_empty(self) -> None:
        config = LLMConfig(provider="openai", model="gpt-4")
        compiler = PipelineCompiler(config, registry=None)
        payload = {
            "steps": [{"program_name": "anything"}]
        }
        assert compiler._detect_unknown_programs(payload) == []

    def test_known_program_not_flagged(self) -> None:
        config = LLMConfig(provider="openai", model="gpt-4")
        registry = ProgramRegistry(session_factory=None)
        prog = ProgramEntry(
            name="known",
            command_type=CommandType.SHELL,
            command_template="echo",
        )
        registry._cache = {"known": prog}

        compiler = PipelineCompiler(config, registry=registry)
        payload = {
            "steps": [{"program_name": "known"}]
        }
        assert compiler._detect_unknown_programs(payload) == []

    def test_unknown_program_flagged(self) -> None:
        config = LLMConfig(provider="openai", model="gpt-4")
        registry = ProgramRegistry(session_factory=None)

        compiler = PipelineCompiler(config, registry=registry)
        payload = {
            "steps": [
                {"program_name": "unknown1"},
                {"program_name": "unknown2"},
                {"program_name": "unknown1"},  # duplicate
            ]
        }
        unknown = compiler._detect_unknown_programs(payload)
        assert unknown == ["unknown1", "unknown2"]

    def test_null_program_name_ignored(self) -> None:
        config = LLMConfig(provider="openai", model="gpt-4")
        registry = ProgramRegistry(session_factory=None)

        compiler = PipelineCompiler(config, registry=registry)
        payload = {
            "steps": [{"program_name": None}, {"name": "step1"}]
        }
        assert compiler._detect_unknown_programs(payload) == []
