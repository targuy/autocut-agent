"""Tests for pipeline data models."""

from __future__ import annotations

import uuid

from agent.pipeline.models import (
    Artifact,
    CommandType,
    Pipeline,
    PipelineStatus,
    PipelineStep,
    PipelineStepDef,
    PipelineTemplate,
    StepExecution,
    StepStatus,
)


class TestPipeline:
    def test_create_pipeline(self) -> None:
        p = Pipeline(name="Test Pipeline")
        assert p.name == "Test Pipeline"
        assert p.status == PipelineStatus.PENDING
        assert isinstance(p.id, uuid.UUID)
        assert p.inputs == {}
        assert p.context == {}

    def test_is_terminal(self) -> None:
        p = Pipeline(name="Test")
        assert not p.is_terminal()

        p.status = PipelineStatus.COMPLETED
        assert p.is_terminal()

        p.status = PipelineStatus.FAILED
        assert p.is_terminal()

        p.status = PipelineStatus.RUNNING
        assert not p.is_terminal()


class TestPipelineStep:
    def test_create_step(self) -> None:
        pid = uuid.uuid4()
        step = PipelineStep(
            pipeline_id=pid,
            order=0,
            name="Resize video",
            command_type=CommandType.FFMPEG,
            command_template="ffmpeg -i {input} -vf scale=1280:720 {output}",
        )
        assert step.name == "Resize video"
        assert step.command_type == CommandType.FFMPEG
        assert step.status == StepStatus.PENDING
        assert step.retry_max == 0

    def test_is_ready(self) -> None:
        pid = uuid.uuid4()
        dep_id = uuid.uuid4()
        step = PipelineStep(
            pipeline_id=pid,
            order=1,
            name="Step 2",
            command_type=CommandType.PYTHON,
            command_template="python script.py",
            depends_on=[dep_id],
        )
        assert not step.is_ready(set())
        assert step.is_ready({dep_id})

    def test_can_retry(self) -> None:
        pid = uuid.uuid4()
        step = PipelineStep(
            pipeline_id=pid,
            order=0,
            name="Retryable",
            command_type=CommandType.SHELL,
            command_template="echo test",
            retry_max=3,
        )
        step.status = StepStatus.FAILED_RETRYABLE
        step.retry_count = 1
        assert step.can_retry()

        step.retry_count = 3
        assert not step.can_retry()


class TestPipelineTemplate:
    def test_create_template(self) -> None:
        steps = [
            PipelineStepDef(
                name="Step 1",
                command_type=CommandType.FFMPEG,
                command_template="ffmpeg -i {input} {output}",
            ),
            PipelineStepDef(
                name="Step 2",
                command_type=CommandType.PYTHON,
                command_template="python analyze.py {input}",
                depends_on_names=["Step 1"],
            ),
        ]
        tmpl = PipelineTemplate(name="Video workflow", steps=steps)
        assert tmpl.name == "Video workflow"
        assert len(tmpl.steps) == 2
        assert tmpl.version == 1
        assert tmpl.steps[1].depends_on_names == ["Step 1"]


class TestArtifact:
    def test_create_artifact(self) -> None:
        pid = uuid.uuid4()
        sid = uuid.uuid4()
        art = Artifact(
            pipeline_id=pid,
            step_id=sid,
            key="timecodes",
            value=[1.0, 5.3, 12.7],
        )
        assert art.key == "timecodes"
        assert art.value == [1.0, 5.3, 12.7]
        assert art.file_path is None
