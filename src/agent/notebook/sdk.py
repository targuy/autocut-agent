"""Notebook SDK — sync wrappers for interactive pipeline development.

All public methods are synchronous (blocking). Internally they call the
async AutoCut-Agent APIs via ``asyncio.run()`` or, when already inside
a running event loop (like Jupyter), via ``nest_asyncio``.

Every mutating operation writes directly to the database — changes are
immediately visible to the GUI, CLI, and API.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from agent.core.database import Database
from agent.pipeline.conditions import evaluate_condition
from agent.pipeline.models import (
    Artifact,
    CommandType,
    Pipeline,
    PipelineStatus,
    PipelineStep,
    PipelineStepDef,
    PipelineTemplate,
    StepStatus,
)
from agent.pipeline.registry import (
    ParameterDef,
    ProgramEntry,
    ProgramRegistry,
    parse_parameter_spec,
)
from agent.pipeline.scoring import (
    Advisory,
    ExecutionOutcome,
    ExecutionScore,
    ProgramStats,
    ScoringManager,
)
from agent.pipeline.templates import TemplateManager


# ---------------------------------------------------------------------------
# Async bridge — works both in plain Python and inside Jupyter
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    """Run an async coroutine from synchronous code.

    In a plain Python script this uses ``asyncio.run()``.
    Inside Jupyter (where an event loop is already running) it patches
    the loop with ``nest_asyncio`` so ``asyncio.run()`` works.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # We are inside Jupyter or another async environment.
        try:
            import nest_asyncio  # type: ignore[import-untyped]

            nest_asyncio.apply()
        except ImportError:
            raise RuntimeError(
                "An event loop is already running (e.g. Jupyter). "
                "Install nest_asyncio to use the notebook SDK: "
                "pip install nest_asyncio"
            ) from None
        return asyncio.run(coro)

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Session — main entry point
# ---------------------------------------------------------------------------


class Session:
    """Interactive session for notebook-based pipeline development.

    Connects to the AutoCut-Agent database and exposes sync helpers
    for registry editing, scoring, condition testing, pipeline
    compilation, and data export.

    Parameters
    ----------
    db_url:
        SQLAlchemy async connection string.
        Default: ``"sqlite+aiosqlite:///agent.db"``

    Examples
    --------
    ::

        from agent.notebook import Session

        s = Session()                                  # default DB
        s = Session("sqlite+aiosqlite:///my_project.db")

        # List registered programs
        for prog in s.registry.list():
            print(prog.name, prog.command_type)

        # Test a condition expression
        s.conditions.test("duration > 60", {"duration": 120})
    """

    def __init__(self, db_url: str = "sqlite+aiosqlite:///agent.db") -> None:
        self._db = Database(db_url)
        _run(self._db.create_tables())

        self._program_registry = ProgramRegistry(self._db.session_factory)
        _run(self._program_registry.load_cache())

        self._scoring_manager = ScoringManager(self._db.session_factory)
        self._template_manager = TemplateManager(self._db.session_factory)

        self.registry = RegistryHelper(self._program_registry, self._db)
        self.scoring = ScoringHelper(self._scoring_manager)
        self.conditions = ConditionHelper()
        self.compiler = CompilerHelper(self._program_registry)
        self.pipelines = PipelineHelper(self._db, self._template_manager)
        self.export = ExportHelper(self._program_registry, self._scoring_manager)

    def close(self) -> None:
        """Dispose of the database engine."""
        _run(self._db.engine.dispose())

    def __repr__(self) -> str:
        return f"<Session db={self._db.engine.url!r}>"


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------


class RegistryHelper:
    """View and edit program definitions.  All changes persist to DB.

    Examples
    --------
    ::

        # List all programs
        s.registry.list()

        # Get a single program
        prog = s.registry.get("facedetection")

        # Update a parameter value (persisted immediately)
        s.registry.set_param("facedetection", "threshold", 0.7)

        # Add a new parameter
        s.registry.add_param("facedetection", "max_faces:int:10:Max faces to detect")

        # Remove a parameter
        s.registry.remove_param("facedetection", "max_faces")

        # Register a new program
        s.registry.register(
            name="my_script",
            command_type="python",
            command_template="python my_script.py --input {input_path}",
            description="My custom processing script",
            tags=["custom", "filter"],
            parameters=["threshold:float:0.5:Confidence threshold"],
        )

        # Update program metadata
        s.registry.update("my_script", description="Updated desc", tags=["new"])

        # Deactivate
        s.registry.deactivate("my_script")
    """

    def __init__(self, registry: ProgramRegistry, db: Database) -> None:
        self._registry = registry
        self._db = db

    def list(self, active_only: bool = True) -> list[ProgramEntry]:
        """List all registered programs."""
        return _run(self._registry.list_all(active_only=active_only))

    def get(self, name: str) -> ProgramEntry | None:
        """Get a program by name.  Returns None if not found."""
        return _run(self._registry.get_by_name(name))

    def search(self, description: str) -> list[ProgramEntry]:
        """Find programs whose name or tags match a description."""
        return self._registry.find_programs_for_description(description)

    def register(
        self,
        name: str,
        command_type: str = "shell",
        command_template: str = "",
        *,
        description: str = "",
        purpose: str = "",
        required_inputs: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        tags: list[str] | None = None,
        parameters: list[str] | None = None,
    ) -> ProgramEntry:
        """Register a new program.

        Parameters
        ----------
        parameters:
            List of ``"name:type:default:description"`` strings.
            Example: ``["threshold:float:0.5:Confidence threshold"]``

        Returns
        -------
        ProgramEntry
            The newly registered program.
        """
        params = [parse_parameter_spec(p) for p in (parameters or [])]
        entry = ProgramEntry(
            name=name,
            description=description,
            purpose=purpose,
            command_type=CommandType(command_type),
            command_template=command_template,
            required_inputs=required_inputs or [],
            expected_outputs=expected_outputs or [],
            parameters=params,
            tags=tags or [],
            registered_by="notebook",
        )
        return _run(self._registry.register(entry))

    def update(self, name: str, **fields: Any) -> ProgramEntry:
        """Update a program's metadata fields.

        Accepts any ProgramEntry field as keyword argument::

            s.registry.update("myprog", description="new desc", tags=["a","b"])

        Returns the updated entry.
        """
        entry = _run(self._registry.get_by_name(name))
        if entry is None:
            raise KeyError(f"Program '{name}' not found")
        for key, val in fields.items():
            if key == "command_type":
                val = CommandType(val)
            if hasattr(entry, key):
                setattr(entry, key, val)
        return _run(self._registry.register(entry))

    def set_param(self, program_name: str, param_name: str, value: Any) -> ProgramEntry:
        """Set a parameter's current_value.  Validates and persists.

        Examples
        --------
        ::

            s.registry.set_param("facedetection", "threshold", 0.3)
        """
        entry, errors = _run(
            self._registry.update_parameters(program_name, {param_name: value})
        )
        if entry is None:
            raise KeyError(errors[0] if errors else f"'{program_name}' not found")
        if errors:
            raise ValueError("; ".join(errors))
        return entry

    def add_param(self, program_name: str, spec: str) -> ProgramEntry:
        """Add a parameter from a spec string.

        Parameters
        ----------
        spec:
            ``"name:type:default:description"`` format.
            Example: ``"max_faces:int:10:Maximum faces to detect"``
        """
        entry = _run(self._registry.get_by_name(program_name))
        if entry is None:
            raise KeyError(f"Program '{program_name}' not found")
        param = parse_parameter_spec(spec)
        if entry.get_parameter(param.name) is not None:
            raise ValueError(f"Parameter '{param.name}' already exists")
        entry.parameters.append(param)
        return _run(self._registry.register(entry))

    def remove_param(self, program_name: str, param_name: str) -> ProgramEntry:
        """Remove a parameter from a program."""
        entry = _run(self._registry.get_by_name(program_name))
        if entry is None:
            raise KeyError(f"Program '{program_name}' not found")
        if entry.get_parameter(param_name) is None:
            raise KeyError(f"Parameter '{param_name}' not found on '{program_name}'")
        entry.parameters = [p for p in entry.parameters if p.name != param_name]
        return _run(self._registry.register(entry))

    def deactivate(self, name: str) -> bool:
        """Soft-delete (deactivate) a program."""
        return _run(self._registry.deactivate(name))

    def show(self, name: str) -> None:
        """Pretty-print a program's details (for notebook display)."""
        entry = self.get(name)
        if entry is None:
            print(f"Program '{name}' not found")
            return
        print(f"Program: {entry.name}")
        print(f"  Type:     {entry.command_type.value}")
        print(f"  Desc:     {entry.description}")
        print(f"  Purpose:  {entry.purpose}")
        print(f"  Command:  {entry.command_template}")
        print(f"  Inputs:   {', '.join(entry.required_inputs) or '-'}")
        print(f"  Outputs:  {', '.join(entry.expected_outputs) or '-'}")
        print(f"  Tags:     {', '.join(entry.tags) or '-'}")
        print(f"  Active:   {entry.active}")
        print(f"  Version:  {entry.version}")
        if entry.parameters:
            print("  Parameters:")
            for p in entry.parameters:
                val = p.current_value if p.current_value is not None else p.default
                print(f"    {p.name} ({p.type.value}) = {val}  — {p.description}")


# ---------------------------------------------------------------------------
# Scoring helper
# ---------------------------------------------------------------------------


class ScoringHelper:
    """View execution stats and inject training scores.

    Examples
    --------
    ::

        # View stats
        stats = s.scoring.stats("facedetection")
        print(f"Success rate: {stats.success_rate:.0%}")

        # Inject a successful run
        s.scoring.inject("facedetection", "success", {"threshold": 0.5})

        # Inject a batch of scores for training
        s.scoring.inject_batch("facedetection", "success", {"threshold": 0.3}, count=20)
        s.scoring.inject_batch("facedetection", "zero_output", {"threshold": 0.9}, count=10)

        # View advisories after training
        for a in s.scoring.advisories("facedetection"):
            print(f"[{a.severity}] {a.title}: {a.message}")

        # Compare parameter sets
        for ps in s.scoring.param_sets("facedetection"):
            print(f"  {ps.parameters} -> {ps.success_rate:.0%} ({ps.total_runs} runs)")

        # Clear all scores to start fresh
        s.scoring.clear("facedetection")
    """

    def __init__(self, manager: ScoringManager) -> None:
        self._manager = manager

    def stats(self, program_name: str) -> ProgramStats:
        """Get aggregated execution statistics."""
        return _run(self._manager.get_program_stats(program_name))

    def param_sets(self, program_name: str) -> list[Any]:
        """Get per-parameter-set stats breakdown."""
        return _run(self._manager.get_param_set_stats(program_name))

    def advisories(self, program_name: str) -> list[Advisory]:
        """Get advisories for a program based on scoring history."""
        return _run(self._manager.get_advisories(program_name))

    def all_advisories(self) -> dict[str, list[Advisory]]:
        """Get advisories for all programs."""
        return _run(self._manager.get_all_advisories())

    def inject(
        self,
        program_name: str,
        outcome: str,
        parameters: dict[str, Any] | None = None,
        *,
        duration: float = 1.0,
        output_size: int = 100,
        error_message: str = "",
    ) -> str:
        """Inject a single execution score.

        Parameters
        ----------
        outcome:
            One of: ``"success"``, ``"failure"``, ``"zero_output"``,
            ``"timeout"``, ``"skipped"``

        Returns the score ID.
        """
        score = ExecutionScore(
            program_name=program_name,
            outcome=ExecutionOutcome(outcome),
            parameters_used=parameters or {},
            duration_seconds=duration,
            output_size=output_size,
            error_message=error_message,
        )
        _run(self._manager.record_score(score))
        return str(score.id)

    def inject_batch(
        self,
        program_name: str,
        outcome: str,
        parameters: dict[str, Any] | None = None,
        *,
        count: int = 10,
        duration: float = 1.0,
        output_size: int = 100,
    ) -> int:
        """Inject multiple identical scores for training.

        Returns the number of scores injected.
        """
        for _ in range(count):
            self.inject(
                program_name,
                outcome,
                parameters,
                duration=duration,
                output_size=output_size,
            )
        return count

    def clear(self, program_name: str) -> int:
        """Delete all scores for a program.  Returns count deleted."""
        return _run(self._manager.clear_scores(program_name))

    def show(self, program_name: str) -> None:
        """Pretty-print stats and advisories for a program."""
        st = self.stats(program_name)
        print(f"Stats for '{program_name}' ({st.total_runs} runs):")
        print(f"  Success:     {st.successes:>4}  ({st.success_rate:.0%})")
        print(f"  Failures:    {st.failures:>4}  ({st.failure_rate:.0%})")
        print(f"  Zero output: {st.zero_outputs:>4}  ({st.zero_output_rate:.0%})")
        print(f"  Timeouts:    {st.timeouts:>4}")
        print(f"  Avg duration: {st.avg_duration_seconds:.1f}s")

        param_stats = self.param_sets(program_name)
        if param_stats:
            print(f"\n  Parameter set breakdown ({len(param_stats)} sets):")
            for ps in param_stats:
                print(
                    f"    {ps.parameters} -> "
                    f"{ps.success_rate:.0%} success, "
                    f"{ps.total_runs} runs, "
                    f"avg {ps.avg_duration_seconds:.1f}s"
                )

        advs = self.advisories(program_name)
        if advs:
            print(f"\n  Advisories ({len(advs)}):")
            for a in advs:
                print(f"    [{a.severity.value.upper()}] {a.title}")
                print(f"      {a.message}")


# ---------------------------------------------------------------------------
# Condition helper
# ---------------------------------------------------------------------------


class ConditionHelper:
    """Test and debug condition expressions.

    Examples
    --------
    ::

        # Test a condition against context data
        s.conditions.test("resolution != 720", {"resolution": 1080})
        # -> True  (step would execute)

        s.conditions.test("has(timecodes) and duration > 60",
                          {"timecodes": [1.5, 3.2], "duration": 45})
        # -> False  (step would be skipped, duration is 45)

        # Batch test multiple conditions
        s.conditions.test_batch(
            ["resolution != 720", "has(faces)", "duration > 30"],
            {"resolution": 720, "faces": None, "duration": 120},
        )
        # -> [False, False, True]

        # Explain why a condition passed or failed
        s.conditions.explain("resolution != 720", {"resolution": 720})
        # Prints detailed explanation
    """

    def test(self, expression: str, context: dict[str, Any]) -> bool:
        """Evaluate a condition expression against a context dict.

        Returns True if the step would execute, False if it would be
        skipped.
        """
        return evaluate_condition(expression, context)

    def test_batch(
        self, expressions: list[str], context: dict[str, Any]
    ) -> list[tuple[str, bool]]:
        """Test multiple conditions against the same context.

        Returns a list of (expression, result) tuples.
        """
        return [(expr, evaluate_condition(expr, context)) for expr in expressions]

    def explain(self, expression: str, context: dict[str, Any]) -> None:
        """Pretty-print a condition evaluation with explanation."""
        result = evaluate_condition(expression, context)
        action = "EXECUTE (condition is True)" if result else "SKIP (condition is False)"
        print(f"Condition: {expression!r}")
        print(f"Context:   {context}")
        print(f"Result:    {result}")
        print(f"Action:    Step would {action}")

        # Show which context keys the expression references
        referenced = [
            key for key in context if key.lower() in expression.lower()
        ]
        if referenced:
            print(f"Referenced keys: {referenced}")
            for key in referenced:
                print(f"  {key} = {context[key]!r}")

    def show_operators(self) -> None:
        """Print supported condition syntax."""
        print("Supported condition syntax:")
        print("  Comparison: resolution != 720, duration > 60, fps >= 30")
        print("  Existence:  has(timecodes), not has(error)")
        print("  Boolean:    true, false")
        print("  Combined:   has(timecodes) and duration > 0")
        print("  Logic:      expr1 and expr2, expr1 or expr2, not expr")


# ---------------------------------------------------------------------------
# Compiler helper
# ---------------------------------------------------------------------------


class CompilerHelper:
    """Compile natural language into pipelines.

    Examples
    --------
    ::

        # Compile a pipeline
        result = s.compiler.compile(
            "Resize video to 720p, detect scenes, run facedetection on each clip",
            inputs={"video_path": "/data/input.mp4"},
        )

        # Check if it compiled cleanly
        if result.is_complete:
            print(f"Pipeline: {result.pipeline.name}")
            for step in result.steps:
                print(f"  {step.order}. {step.name} [{step.command_type}]")
        else:
            print("Unknown programs need registration:")
            for name, questions in result.pending_questions.items():
                print(f"  {name}:")
                for q in questions:
                    print(f"    - {q.question}")

        # Build a pipeline manually from a template definition
        pipeline, steps = s.compiler.from_steps([
            {"name": "resize", "command_type": "ffmpeg",
             "command_template": "ffmpeg -i {input} -vf scale=1280:720 {output}"},
            {"name": "detect", "command_type": "python",
             "command_template": "python detect.py --input {output}",
             "depends_on_names": ["resize"]},
        ], name="My Pipeline", inputs={"input": "/data/video.mp4"})
    """

    def __init__(self, registry: ProgramRegistry) -> None:
        self._registry = registry

    def compile(
        self,
        description: str,
        inputs: dict[str, Any] | None = None,
        *,
        provider: str = "openai",
        model: str = "gpt-4",
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.3,
    ) -> Any:
        """Compile a natural-language description into a pipeline.

        Requires an LLM provider to be configured.  Returns a
        ``CompilationResult``.
        """
        from agent.core.config import LLMConfig
        from agent.pipeline.compiler import PipelineCompiler

        config = LLMConfig(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
        )
        compiler = PipelineCompiler(config, registry=self._registry)
        return _run(compiler.compile_with_context(description, inputs))

    def from_steps(
        self,
        step_defs: list[dict[str, Any]],
        *,
        name: str = "Notebook Pipeline",
        inputs: dict[str, Any] | None = None,
    ) -> tuple[Pipeline, list[PipelineStep]]:
        """Build a pipeline manually from step definitions.

        Each dict in *step_defs* should have at least ``name``,
        ``command_type``, and ``command_template``.  Optional:
        ``condition``, ``depends_on_names``, ``fan_out_on``,
        ``resource_requirements``, ``timeout``, ``retry_max``.

        Returns ``(Pipeline, list[PipelineStep])``.
        """
        template_steps = [
            PipelineStepDef(
                name=sd["name"],
                command_type=CommandType(sd.get("command_type", "shell")),
                command_template=sd.get("command_template", ""),
                condition=sd.get("condition"),
                input_mappings=sd.get("input_mappings", {}),
                fan_out_on=sd.get("fan_out_on"),
                depends_on_names=sd.get("depends_on_names", []),
                resource_requirements=sd.get("resource_requirements", []),
                timeout=sd.get("timeout", 3600),
                retry_max=sd.get("retry_max", 0),
            )
            for sd in step_defs
        ]
        template = PipelineTemplate(
            name=name,
            description=f"Built in notebook: {name}",
            steps=template_steps,
            created_by="notebook",
        )

        # Resolve into Pipeline + PipelineStep list
        from agent.pipeline.compiler import PipelineCompiler

        pipeline, steps = PipelineCompiler._instantiate_from_template(
            template, inputs or {}
        )
        return pipeline, steps

    def preview_prompt(
        self, description: str, inputs: dict[str, Any] | None = None
    ) -> str:
        """Show the LLM prompt that would be sent, without calling the LLM.

        Useful for debugging what registry context the compiler injects.
        """
        from agent.pipeline.compiler import PipelineCompiler

        known_programs = self._registry.find_programs_for_description(description)
        return PipelineCompiler._build_user_message(
            description, inputs or {}, known_programs
        )


# ---------------------------------------------------------------------------
# Pipeline helper
# ---------------------------------------------------------------------------


class PipelineHelper:
    """Inspect pipelines and manage templates.

    Examples
    --------
    ::

        # Inspect a compiled pipeline
        s.pipelines.inspect(result.pipeline, result.steps)

        # View a single step
        s.pipelines.inspect_step(result.steps[0], context={"video": "test.mp4"})

        # Save as template for reuse
        template = s.pipelines.save_template(result)

        # List saved templates
        for t in s.pipelines.list_templates():
            print(f"{t.name} v{t.version} ({len(t.steps)} steps)")

        # Clone a template with new inputs
        pipeline, steps = s.pipelines.clone_template(template, {"video": "other.mp4"})

        # Edit a step's command before running
        result.steps[1].command_template = "python detect.py --threshold 0.3 --input {clip}"
    """

    def __init__(self, db: Database, template_manager: TemplateManager) -> None:
        self._db = db
        self._template_manager = template_manager

    def inspect(
        self,
        pipeline: Pipeline,
        steps: list[PipelineStep] | None = None,
    ) -> None:
        """Pretty-print a pipeline and its steps."""
        print(f"Pipeline: {pipeline.name}")
        print(f"  ID:       {pipeline.id}")
        print(f"  Status:   {pipeline.status.value}")
        print(f"  Template: {pipeline.template_id or '-'}")
        if pipeline.inputs:
            print(f"  Inputs:   {pipeline.inputs}")
        if steps:
            print(f"  Steps ({len(steps)}):")
            for s in steps:
                deps = f" (depends: {len(s.depends_on)})" if s.depends_on else ""
                cond = f" [if {s.condition}]" if s.condition else ""
                fan = f" (fan-out: {s.fan_out_on})" if s.fan_out_on else ""
                print(
                    f"    {s.order}. {s.name} [{s.command_type.value}]"
                    f" — {s.status.value}{deps}{cond}{fan}"
                )
                if s.command_template:
                    cmd = s.command_template
                    if len(cmd) > 80:
                        cmd = cmd[:77] + "..."
                    print(f"       cmd: {cmd}")

    def inspect_step(
        self,
        step: PipelineStep,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Detailed view of a single step with condition evaluation."""
        print(f"Step: {step.name}")
        print(f"  Order:    {step.order}")
        print(f"  Type:     {step.command_type.value}")
        print(f"  Status:   {step.status.value}")
        print(f"  Command:  {step.command_template}")
        print(f"  Timeout:  {step.timeout}s")
        print(f"  Retries:  {step.retry_count}/{step.retry_max}")
        if step.condition:
            print(f"  Condition: {step.condition}")
            if context:
                result = evaluate_condition(step.condition, context)
                action = "EXECUTE" if result else "SKIP"
                print(f"  Condition result: {result} -> {action}")
        if step.fan_out_on:
            print(f"  Fan-out on: {step.fan_out_on}")
        if step.depends_on:
            print(f"  Depends on: {len(step.depends_on)} step(s)")
        if step.resource_requirements:
            print(f"  Resources: {step.resource_requirements}")

    def edit_step(
        self, step: PipelineStep, **fields: Any
    ) -> PipelineStep:
        """Modify a step's fields in-memory.

        Common fields: ``command_template``, ``condition``,
        ``timeout``, ``retry_max``, ``fan_out_on``.

        Example::

            s.pipelines.edit_step(steps[1],
                command_template="python detect.py --threshold 0.3 --input {clip}",
                timeout=600,
            )
        """
        for key, val in fields.items():
            if key == "command_type":
                val = CommandType(val)
            if hasattr(step, key):
                setattr(step, key, val)
        return step

    def save_template(
        self,
        compilation_result: Any = None,
        *,
        pipeline: Pipeline | None = None,
        steps: list[PipelineStep] | None = None,
        name: str | None = None,
    ) -> PipelineTemplate:
        """Save a pipeline as a reusable template (persisted to DB).

        Accepts either a ``CompilationResult`` or explicit pipeline + steps.
        """
        if compilation_result is not None:
            if hasattr(compilation_result, "template") and compilation_result.template:
                template = compilation_result.template
                if name:
                    template.name = name
                return _run(self._template_manager.save(template))
            pipeline = compilation_result.pipeline
            steps = compilation_result.steps

        if pipeline is None or steps is None:
            raise ValueError("Provide either compilation_result or pipeline + steps")

        step_defs = [
            PipelineStepDef(
                name=s.name,
                command_type=s.command_type,
                command_template=s.command_template,
                condition=s.condition,
                input_mappings=s.input_mappings,
                fan_out_on=s.fan_out_on,
                depends_on_names=[],
                resource_requirements=s.resource_requirements,
                timeout=s.timeout,
                retry_max=s.retry_max,
            )
            for s in steps
        ]
        template = PipelineTemplate(
            name=name or pipeline.name,
            description=f"Saved from notebook: {pipeline.name}",
            steps=step_defs,
            created_by="notebook",
        )
        return _run(self._template_manager.save(template))

    def list_templates(self) -> list[PipelineTemplate]:
        """List all saved templates."""
        return _run(self._template_manager.list_all())

    def get_template(self, template_id: str) -> PipelineTemplate | None:
        """Load a template by ID."""
        return _run(self._template_manager.get(uuid.UUID(template_id)))

    def clone_template(
        self,
        template: PipelineTemplate,
        inputs: dict[str, Any] | None = None,
    ) -> tuple[Pipeline, list[PipelineStep]]:
        """Clone a template into a new pipeline instance."""
        return self._template_manager.clone(template, inputs)

    def delete_template(self, template_id: str) -> bool:
        """Delete a template from the database."""
        return _run(self._template_manager.delete(uuid.UUID(template_id)))


# ---------------------------------------------------------------------------
# Export helper
# ---------------------------------------------------------------------------


class ExportHelper:
    """Export pipelines and registry data to files.

    Examples
    --------
    ::

        # Export a compiled pipeline as JSON
        s.export.pipeline(result, "pipelines/my_workflow.json")

        # Export all registry programs
        s.export.registry("configs/programs.json")

        # Export as Docker deployment bundle
        s.export.docker_bundle(result, "deploy/my_workflow/")
    """

    def __init__(
        self, registry: ProgramRegistry, scoring: ScoringManager
    ) -> None:
        self._registry = registry
        self._scoring = scoring

    def pipeline(self, compilation_result: Any, path: str) -> str:
        """Export a pipeline definition as JSON.

        Parameters
        ----------
        compilation_result:
            A ``CompilationResult`` or a ``(Pipeline, list[PipelineStep])`` tuple.
        path:
            Output file path (e.g. ``"pipelines/workflow.json"``).

        Returns the absolute path written.
        """
        if hasattr(compilation_result, "pipeline"):
            pipe = compilation_result.pipeline
            steps = compilation_result.steps
        else:
            pipe, steps = compilation_result

        data = {
            "name": pipe.name,
            "inputs": pipe.inputs,
            "steps": [
                {
                    "name": s.name,
                    "order": s.order,
                    "command_type": s.command_type.value,
                    "command_template": s.command_template,
                    "condition": s.condition,
                    "input_mappings": s.input_mappings,
                    "fan_out_on": s.fan_out_on,
                    "depends_on": [str(d) for d in s.depends_on],
                    "resource_requirements": s.resource_requirements,
                    "timeout": s.timeout,
                    "retry_max": s.retry_max,
                }
                for s in steps
            ],
        }

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2, default=str))
        print(f"Pipeline exported to {out.resolve()}")
        return str(out.resolve())

    def registry(self, path: str, active_only: bool = True) -> str:
        """Export all programs as JSON.

        Returns the absolute path written.
        """
        programs = _run(self._registry.list_all(active_only=active_only))
        data = [
            {
                "name": p.name,
                "description": p.description,
                "purpose": p.purpose,
                "command_type": p.command_type.value,
                "command_template": p.command_template,
                "required_inputs": p.required_inputs,
                "expected_outputs": p.expected_outputs,
                "parameters": [param.model_dump() for param in p.parameters],
                "tags": p.tags,
                "version": p.version,
                "active": p.active,
            }
            for p in programs
        ]

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2, default=str))
        print(f"Registry exported to {out.resolve()} ({len(data)} programs)")
        return str(out.resolve())

    def docker_bundle(
        self,
        compilation_result: Any,
        output_dir: str,
        *,
        base_image: str = "python:3.11-slim",
        port: int = 8080,
    ) -> str:
        """Generate a Docker deployment bundle for a pipeline.

        Creates:
        - ``pipeline.json`` — the pipeline definition
        - ``Dockerfile`` — builds a container that runs the pipeline
        - ``docker-compose.yml`` — ready to ``docker-compose up``

        Returns the output directory path.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Pipeline JSON
        self.pipeline(compilation_result, str(out / "pipeline.json"))

        # Dockerfile
        if hasattr(compilation_result, "pipeline"):
            pipe = compilation_result.pipeline
        else:
            pipe = compilation_result[0]

        dockerfile = f"""\
FROM {base_image}
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE {port}
CMD ["python", "-m", "agent.cli", "start", "--config", "config.yaml"]
"""
        (out / "Dockerfile").write_text(dockerfile)

        # docker-compose.yml
        compose = f"""\
version: "3.8"
services:
  autocut-agent:
    build: .
    ports:
      - "{port}:{port}"
    environment:
      - DATABASE_URL=sqlite+aiosqlite:///agent.db
    volumes:
      - ./data:/app/data
      - ./pipeline.json:/app/pipeline.json
"""
        (out / "docker-compose.yml").write_text(compose)

        # requirements.txt
        (out / "requirements.txt").write_text("autocut-agent\n")

        print(f"Docker bundle created in {out.resolve()}/")
        print(f"  docker-compose up --build")
        return str(out.resolve())
