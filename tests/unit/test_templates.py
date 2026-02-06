"""Tests for pipeline template cloning."""

from __future__ import annotations

from agent.pipeline.models import CommandType, PipelineStepDef, PipelineTemplate
from agent.pipeline.templates import TemplateManager


class TestTemplateClone:
    def test_clone_simple_template(self) -> None:
        template = PipelineTemplate(
            name="Video workflow",
            steps=[
                PipelineStepDef(
                    name="resize",
                    command_type=CommandType.FFMPEG,
                    command_template="ffmpeg -i {video_path} -vf scale=1280:720 {output}",
                    condition="resolution != 720",
                ),
                PipelineStepDef(
                    name="detect_scenes",
                    command_type=CommandType.FFMPEG,
                    command_template="ffmpeg -i {input} -filter:v select='gt(scene,0.3)' -f null -",
                    depends_on_names=["resize"],
                ),
                PipelineStepDef(
                    name="face_detection",
                    command_type=CommandType.PYTHON,
                    command_template="python facedetection.py --clip {clip_path}",
                    depends_on_names=["detect_scenes"],
                    fan_out_on="timecodes",
                    resource_requirements=["gpu:cuda:0"],
                ),
            ],
            created_by="test",
        )

        # Use a dummy session factory — clone doesn't need DB
        mgr = TemplateManager(session_factory=None)
        pipeline, steps = mgr.clone(template, inputs={"video_path": "/test/video.mp4"})

        assert pipeline.name == "Video workflow"
        assert pipeline.template_id == template.id
        assert pipeline.inputs["video_path"] == "/test/video.mp4"
        assert len(steps) == 3

        # Check dependency resolution
        resize_id = steps[0].id
        detect_id = steps[1].id
        assert steps[1].depends_on == [resize_id]
        assert steps[2].depends_on == [detect_id]

        # Check fan-out and resources preserved
        assert steps[2].fan_out_on == "timecodes"
        assert steps[2].resource_requirements == ["gpu:cuda:0"]

        # Check condition preserved
        assert steps[0].condition == "resolution != 720"

    def test_clone_with_different_inputs(self) -> None:
        template = PipelineTemplate(
            name="Simple",
            steps=[
                PipelineStepDef(
                    name="step1",
                    command_type=CommandType.SHELL,
                    command_template="echo {greeting}",
                ),
            ],
        )
        mgr = TemplateManager(session_factory=None)

        p1, s1 = mgr.clone(template, inputs={"greeting": "hello"})
        p2, s2 = mgr.clone(template, inputs={"greeting": "world"})

        assert p1.id != p2.id
        assert p1.inputs["greeting"] == "hello"
        assert p2.inputs["greeting"] == "world"
        assert s1[0].id != s2[0].id
