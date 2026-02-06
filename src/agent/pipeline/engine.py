"""PipelineEngine — DAG execution engine for compiled pipelines.

Sits between the orchestrator and the queue manager. Manages the full
lifecycle of a pipeline run: scheduling ready steps, fan-out / fan-in,
retry logic, artifact storage, and status derivation.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select, update

from agent.core.database import (
    ArtifactRow,
    Database,
    PipelineRow,
    PipelineStepRow,
    StepExecutionRow,
)
from agent.executor.runner import ExecutorPool
from agent.pipeline.conditions import evaluate_condition
from agent.pipeline.models import (
    Artifact,
    ExecutionStatus,
    Pipeline,
    PipelineStatus,
    PipelineStep,
    StepExecution,
    StepStatus,
)
from agent.queue.manager import QueueManager
from agent.resources.manager import ResourceManager

logger = structlog.get_logger()

_PLACEHOLDER_RE = re.compile(r"\{(\w+(?:\.\w+)*)}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PipelineEngine:
    """DAG execution engine that runs compiled pipelines.

    The engine iterates over pipeline steps in topological order (respecting
    ``depends_on`` edges), evaluates conditions, handles fan-out / fan-in,
    manages retries, and persists all state transitions to the database.
    """

    def __init__(
        self,
        db: Database,
        queue_manager: QueueManager,
        executor_pool: ExecutorPool,
        resource_manager: ResourceManager,
    ) -> None:
        self.db = db
        self.queue_manager = queue_manager
        self.executor_pool = executor_pool
        self.resource_manager = resource_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_pipeline(
        self,
        pipeline: Pipeline,
        steps: list[PipelineStep],
    ) -> None:
        """Execute a full pipeline run.

        Sets the pipeline to RUNNING, then iteratively schedules steps whose
        dependencies are satisfied until every step is completed, skipped, or
        a terminal failure is encountered.
        """
        log = logger.bind(pipeline_id=str(pipeline.id), pipeline_name=pipeline.name)
        log.info("pipeline.starting", step_count=len(steps))

        pipeline.status = PipelineStatus.RUNNING
        pipeline.started_at = _utcnow()
        await self._persist_pipeline(pipeline)

        # Track which steps are already done (supports resume).
        completed_step_ids: set[uuid.UUID] = {
            s.id
            for s in steps
            if s.status in {StepStatus.COMPLETED, StepStatus.SKIPPED}
        }

        try:
            await self._execution_loop(pipeline, steps, completed_step_ids, log)
        except asyncio.CancelledError:
            log.warning("pipeline.cancelled_externally")
            pipeline.status = PipelineStatus.CANCELLED
            await self._persist_pipeline(pipeline)
            raise
        except Exception:
            log.exception("pipeline.unexpected_error")
            pipeline.status = PipelineStatus.FAILED
            pipeline.completed_at = _utcnow()
            await self._persist_pipeline(pipeline)
            raise

    async def resume_pipeline(self, pipeline_id: uuid.UUID) -> None:
        """Resume an interrupted or partially-completed pipeline."""
        log = logger.bind(pipeline_id=str(pipeline_id))
        log.info("pipeline.resuming")

        pipeline = await self._load_pipeline(pipeline_id)
        if pipeline is None:
            log.error("pipeline.not_found")
            raise ValueError(f"Pipeline {pipeline_id} not found")

        if pipeline.is_terminal():
            log.warning(
                "pipeline.already_terminal",
                status=pipeline.status,
            )
            return

        steps = await self._load_steps(pipeline_id)
        pipeline.resumed_at = _utcnow()
        await self._persist_pipeline(pipeline)
        await self.run_pipeline(pipeline, steps)

    async def cancel_pipeline(self, pipeline_id: uuid.UUID) -> None:
        """Cancel a running or pending pipeline."""
        log = logger.bind(pipeline_id=str(pipeline_id))
        log.info("pipeline.cancelling")

        pipeline = await self._load_pipeline(pipeline_id)
        if pipeline is None:
            log.error("pipeline.not_found")
            raise ValueError(f"Pipeline {pipeline_id} not found")

        if pipeline.is_terminal():
            log.warning(
                "pipeline.already_terminal",
                status=pipeline.status,
            )
            return

        # Mark all non-terminal steps as skipped.
        steps = await self._load_steps(pipeline_id)
        for step in steps:
            if step.status in {StepStatus.PENDING, StepStatus.RUNNING}:
                step.status = StepStatus.SKIPPED
                await self._persist_step(step)

        pipeline.status = PipelineStatus.CANCELLED
        pipeline.completed_at = _utcnow()
        await self._persist_pipeline(pipeline)
        log.info("pipeline.cancelled")

    async def skip_step(
        self,
        pipeline_id: uuid.UUID,
        step_id: uuid.UUID,
    ) -> None:
        """Manually skip a step (e.g. after a retryable failure)."""
        log = logger.bind(pipeline_id=str(pipeline_id), step_id=str(step_id))
        log.info("step.manual_skip")

        step = await self._load_step(step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found")
        if step.pipeline_id != pipeline_id:
            raise ValueError("Step does not belong to the specified pipeline")
        if step.is_terminal():
            log.warning("step.already_terminal", status=step.status)
            return

        step.status = StepStatus.SKIPPED
        await self._persist_step(step)
        log.info("step.skipped")

    # ------------------------------------------------------------------
    # Core execution loop
    # ------------------------------------------------------------------

    async def _execution_loop(
        self,
        pipeline: Pipeline,
        steps: list[PipelineStep],
        completed_step_ids: set[uuid.UUID],
        log: Any,
    ) -> None:
        """Iterate until all steps are done or a terminal failure occurs."""
        step_map: dict[uuid.UUID, PipelineStep] = {s.id: s for s in steps}

        while True:
            # Gather steps that are ready to run.
            ready_steps = [
                s
                for s in steps
                if s.status == StepStatus.PENDING and s.is_ready(completed_step_ids)
            ]

            # Also pick up retryable-failed steps that still have retries left.
            retryable_steps = [
                s for s in steps if s.can_retry() and s.is_ready(completed_step_ids)
            ]
            ready_steps.extend(retryable_steps)

            if not ready_steps:
                # Nothing ready — check if we are truly done or stuck.
                pending = [
                    s
                    for s in steps
                    if s.status
                    in {StepStatus.PENDING, StepStatus.RUNNING, StepStatus.FAILED_RETRYABLE}
                ]
                if not pending:
                    # All steps are terminal — derive final pipeline status.
                    break
                # Steps exist but none are ready — deadlock or waiting.
                log.warning(
                    "pipeline.no_ready_steps",
                    pending_count=len(pending),
                )
                break

            # Execute ready steps concurrently.
            tasks = [
                self._run_step(pipeline, step, completed_step_ids, log)
                for step in ready_steps
            ]
            await asyncio.gather(*tasks)

        # Derive final status from step outcomes.
        await self._update_pipeline_status(pipeline, steps)

    # ------------------------------------------------------------------
    # Single-step execution
    # ------------------------------------------------------------------

    async def _run_step(
        self,
        pipeline: Pipeline,
        step: PipelineStep,
        completed_step_ids: set[uuid.UUID],
        log: Any,
    ) -> None:
        """Execute a single pipeline step (with condition check, fan-out, etc.)."""
        step_log = log.bind(step_id=str(step.id), step_name=step.name)

        # --- Condition evaluation ---
        if step.condition:
            if not evaluate_condition(step.condition, pipeline.context):
                step_log.info("step.condition_false", condition=step.condition)
                step.status = StepStatus.SKIPPED
                await self._persist_step(step)
                completed_step_ids.add(step.id)
                return

        step.status = StepStatus.RUNNING
        if step.can_retry():
            step.retry_count += 1
            step_log.info("step.retrying", retry_count=step.retry_count)
        await self._persist_step(step)

        # --- Collect artifacts from upstream steps ---
        artifacts = await self._get_step_artifacts(pipeline.id, step.id, step)

        # --- Fan-out handling ---
        if step.fan_out_on:
            fan_items = artifacts.get(step.fan_out_on) or pipeline.context.get(
                step.fan_out_on
            )
            if not isinstance(fan_items, list):
                fan_items = [fan_items] if fan_items is not None else []

            step_log.info("step.fan_out", key=step.fan_out_on, count=len(fan_items))
            executions = [
                StepExecution(step_id=step.id, index=i)
                for i in range(len(fan_items))
            ]
            execution_tasks = [
                self._execute_step(
                    pipeline,
                    step,
                    execution,
                    {**artifacts, step.fan_out_on: item},
                    step_log,
                )
                for execution, item in zip(executions, fan_items)
            ]
        else:
            executions = [StepExecution(step_id=step.id, index=0)]
            execution_tasks = [
                self._execute_step(pipeline, step, executions[0], artifacts, step_log)
            ]

        results = await asyncio.gather(*execution_tasks, return_exceptions=True)

        # --- Process results ---
        all_succeeded = True
        any_empty = False

        for execution, result in zip(executions, results):
            if isinstance(result, Exception):
                step_log.error("step.execution_error", error=str(result))
                execution.status = ExecutionStatus.FAILED
                execution.error = str(result)
                execution.completed_at = _utcnow()
                await self._persist_execution(execution)
                all_succeeded = False
                continue

            # result is the execution object updated by _execute_step
            if execution.status == ExecutionStatus.FAILED:
                all_succeeded = False
            elif execution.result is None:
                any_empty = True

        # --- Determine step outcome ---
        if all_succeeded and not any_empty:
            step.status = StepStatus.COMPLETED
            completed_step_ids.add(step.id)
            step_log.info("step.completed")
        elif any_empty and all_succeeded:
            step.status = StepStatus.INTERRUPTED
            step_log.warning("step.interrupted_empty_result")
        elif step.retry_count < step.retry_max:
            step.status = StepStatus.FAILED_RETRYABLE
            step_log.warning(
                "step.failed_retryable",
                retry_count=step.retry_count,
                retry_max=step.retry_max,
            )
        else:
            step.status = StepStatus.FAILED_TERMINAL
            step_log.error("step.failed_terminal")

        await self._persist_step(step)

    async def _execute_step(
        self,
        pipeline: Pipeline,
        step: PipelineStep,
        execution: StepExecution,
        artifacts: dict[str, Any],
        log: Any,
    ) -> StepExecution:
        """Run a single StepExecution via the executor pool."""
        exec_log = log.bind(execution_id=str(execution.id), index=execution.index)

        command = self._resolve_command(
            step.command_template,
            pipeline.context,
            artifacts,
        )
        exec_log.info("step.executing", command=command)

        execution.status = ExecutionStatus.RUNNING
        execution.started_at = _utcnow()
        await self._persist_execution(execution)

        try:
            result = await self.executor_pool.execute(
                command=command,
                timeout=step.timeout,
            )
        except Exception as exc:
            execution.status = ExecutionStatus.FAILED
            execution.error = str(exc)
            execution.completed_at = _utcnow()
            await self._persist_execution(execution)
            exec_log.error("execution.failed", error=str(exc))
            return execution

        execution.completed_at = _utcnow()

        if result is None:
            execution.status = ExecutionStatus.COMPLETED
            execution.result = None
            await self._persist_execution(execution)
            return execution

        # Interpret execution result.
        return_code = getattr(result, "return_code", None)
        stdout = getattr(result, "stdout", "")
        stderr = getattr(result, "stderr", "")
        output_files = getattr(result, "output_files", [])

        execution.result = {
            "return_code": return_code,
            "stdout": stdout,
            "stderr": stderr,
            "output_files": output_files,
        }

        if return_code is not None and return_code != 0:
            execution.status = ExecutionStatus.FAILED
            execution.error = stderr or f"Process exited with code {return_code}"
            exec_log.warning(
                "execution.nonzero_exit",
                return_code=return_code,
            )
        else:
            execution.status = ExecutionStatus.COMPLETED
            exec_log.info("execution.completed")

            # Store output files as artifacts.
            for file_path in output_files:
                artifact = Artifact(
                    pipeline_id=pipeline.id,
                    step_id=step.id,
                    execution_id=execution.id,
                    key=step.name,
                    file_path=file_path,
                )
                await self._store_artifact(artifact)

            # Store stdout as an artifact when present.
            if stdout:
                artifact = Artifact(
                    pipeline_id=pipeline.id,
                    step_id=step.id,
                    execution_id=execution.id,
                    key=f"{step.name}_stdout",
                    value=stdout,
                )
                await self._store_artifact(artifact)

        await self._persist_execution(execution)
        return execution

    # ------------------------------------------------------------------
    # Helpers — command resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_command(
        template: str,
        context: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> str:
        """Substitute ``{var}`` placeholders in a command template.

        Looks up keys first in *artifacts*, then in *context*.  Dotted keys
        like ``{step_name.key}`` are resolved by splitting on the first dot.
        """
        merged: dict[str, Any] = {**context, **artifacts}

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            # Try direct lookup.
            if key in merged:
                return str(merged[key])
            # Try dotted lookup (one level).
            if "." in key:
                prefix, suffix = key.split(".", 1)
                nested = merged.get(prefix)
                if isinstance(nested, dict) and suffix in nested:
                    return str(nested[suffix])
            # Leave unresolved placeholders intact so they surface clearly.
            return match.group(0)

        return _PLACEHOLDER_RE.sub(_replace, template)

    # ------------------------------------------------------------------
    # Helpers — artifact management
    # ------------------------------------------------------------------

    async def _get_step_artifacts(
        self,
        pipeline_id: uuid.UUID,
        step_id: uuid.UUID,
        step: PipelineStep | None = None,
    ) -> dict[str, Any]:
        """Load artifacts produced by upstream steps for this step.

        When *step* is provided its ``depends_on`` list is used to scope the
        query; otherwise all artifacts for the pipeline are returned.
        """
        async with self.db.session() as session:
            query = select(ArtifactRow).where(
                ArtifactRow.pipeline_id == str(pipeline_id)
            )
            if step and step.depends_on:
                dep_ids = [str(d) for d in step.depends_on]
                query = query.where(ArtifactRow.step_id.in_(dep_ids))

            result = await session.execute(query)
            rows = result.scalars().all()

        artifacts: dict[str, Any] = {}
        for row in rows:
            if row.value is not None:
                artifacts[row.key] = row.value
            elif row.file_path is not None:
                artifacts.setdefault(row.key, [])
                if isinstance(artifacts[row.key], list):
                    artifacts[row.key].append(row.file_path)
                else:
                    artifacts[row.key] = [artifacts[row.key], row.file_path]
        return artifacts

    async def _store_artifact(self, artifact: Artifact) -> None:
        """Persist an artifact to the database."""
        async with self.db.session() as session:
            row = ArtifactRow(
                id=str(artifact.id),
                pipeline_id=str(artifact.pipeline_id),
                step_id=str(artifact.step_id),
                execution_id=str(artifact.execution_id) if artifact.execution_id else None,
                key=artifact.key,
                value=artifact.value,
                file_path=artifact.file_path,
                created_at=artifact.created_at,
            )
            session.add(row)
            await session.commit()
            logger.debug(
                "artifact.stored",
                artifact_id=str(artifact.id),
                key=artifact.key,
            )

    # ------------------------------------------------------------------
    # Helpers — status derivation
    # ------------------------------------------------------------------

    async def _update_pipeline_status(
        self,
        pipeline: Pipeline,
        steps: list[PipelineStep] | None = None,
    ) -> None:
        """Derive the pipeline status from the states of its steps."""
        if steps is None:
            steps = await self._load_steps(pipeline.id)

        statuses = {s.status for s in steps}

        if StepStatus.INTERRUPTED in statuses:
            pipeline.status = PipelineStatus.INTERRUPTED
        elif StepStatus.FAILED_TERMINAL in statuses:
            pipeline.status = PipelineStatus.FAILED
        elif statuses <= {StepStatus.COMPLETED, StepStatus.SKIPPED}:
            pipeline.status = PipelineStatus.COMPLETED
            pipeline.completed_at = _utcnow()
        elif StepStatus.RUNNING in statuses:
            pipeline.status = PipelineStatus.RUNNING
        elif StepStatus.FAILED_RETRYABLE in statuses:
            pipeline.status = PipelineStatus.WAITING
        else:
            pipeline.status = PipelineStatus.RUNNING

        await self._persist_pipeline(pipeline)
        logger.info(
            "pipeline.status_updated",
            pipeline_id=str(pipeline.id),
            status=pipeline.status,
        )

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def _persist_pipeline(self, pipeline: Pipeline) -> None:
        """Write pipeline state back to the database."""
        async with self.db.session() as session:
            await session.execute(
                update(PipelineRow)
                .where(PipelineRow.id == str(pipeline.id))
                .values(
                    status=pipeline.status.value,
                    context=pipeline.context,
                    started_at=pipeline.started_at,
                    resumed_at=pipeline.resumed_at,
                    completed_at=pipeline.completed_at,
                )
            )
            await session.commit()

    async def _persist_step(self, step: PipelineStep) -> None:
        """Write step state back to the database."""
        async with self.db.session() as session:
            await session.execute(
                update(PipelineStepRow)
                .where(PipelineStepRow.id == str(step.id))
                .values(
                    status=step.status.value,
                    retry_count=step.retry_count,
                )
            )
            await session.commit()

    async def _persist_execution(self, execution: StepExecution) -> None:
        """Write a step execution to the database (insert or update)."""
        async with self.db.session() as session:
            existing = await session.get(StepExecutionRow, str(execution.id))
            if existing is None:
                row = StepExecutionRow(
                    id=str(execution.id),
                    step_id=str(execution.step_id),
                    index=execution.index,
                    status=execution.status.value,
                    started_at=execution.started_at,
                    completed_at=execution.completed_at,
                    result=execution.result,
                    error=execution.error,
                )
                session.add(row)
            else:
                existing.status = execution.status.value
                existing.started_at = execution.started_at
                existing.completed_at = execution.completed_at
                existing.result = execution.result
                existing.error = execution.error
            await session.commit()

    # ------------------------------------------------------------------
    # Data loading helpers
    # ------------------------------------------------------------------

    async def _load_pipeline(self, pipeline_id: uuid.UUID) -> Pipeline | None:
        """Load a pipeline from the database by ID."""
        async with self.db.session() as session:
            row = await session.get(PipelineRow, str(pipeline_id))
            if row is None:
                return None
            return Pipeline(
                id=uuid.UUID(row.id),
                template_id=uuid.UUID(row.template_id) if row.template_id else None,
                name=row.name,
                status=PipelineStatus(row.status),
                inputs=row.inputs or {},
                context=row.context or {},
                created_at=row.created_at,
                started_at=row.started_at,
                resumed_at=row.resumed_at,
                completed_at=row.completed_at,
            )

    async def _load_steps(self, pipeline_id: uuid.UUID) -> list[PipelineStep]:
        """Load all steps for a pipeline, ordered by ``order``."""
        async with self.db.session() as session:
            result = await session.execute(
                select(PipelineStepRow)
                .where(PipelineStepRow.pipeline_id == str(pipeline_id))
                .order_by(PipelineStepRow.order)
            )
            rows = result.scalars().all()

        return [
            PipelineStep(
                id=uuid.UUID(row.id),
                pipeline_id=uuid.UUID(row.pipeline_id),
                order=row.order,
                name=row.name,
                command_type=row.command_type,
                command_template=row.command_template,
                condition=row.condition,
                input_mappings=row.input_mappings or {},
                fan_out_on=row.fan_out_on,
                depends_on=[uuid.UUID(d) for d in (row.depends_on or [])],
                resource_requirements=row.resource_requirements or [],
                timeout=row.timeout,
                retry_max=row.retry_max,
                retry_count=row.retry_count,
                status=StepStatus(row.status),
            )
            for row in rows
        ]

    async def _load_step(self, step_id: uuid.UUID) -> PipelineStep | None:
        """Load a single step by ID."""
        async with self.db.session() as session:
            row = await session.get(PipelineStepRow, str(step_id))
            if row is None:
                return None
            return PipelineStep(
                id=uuid.UUID(row.id),
                pipeline_id=uuid.UUID(row.pipeline_id),
                order=row.order,
                name=row.name,
                command_type=row.command_type,
                command_template=row.command_template,
                condition=row.condition,
                input_mappings=row.input_mappings or {},
                fan_out_on=row.fan_out_on,
                depends_on=[uuid.UUID(d) for d in (row.depends_on or [])],
                resource_requirements=row.resource_requirements or [],
                timeout=row.timeout,
                retry_max=row.retry_max,
                retry_count=row.retry_count,
                status=StepStatus(row.status),
            )
