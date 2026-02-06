"""Pipeline template storage, cloning, and versioning."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.core.database import PipelineTemplateRow
from agent.pipeline.models import (
    Pipeline,
    PipelineStatus,
    PipelineStep,
    PipelineStepDef,
    PipelineTemplate,
    StepStatus,
)

logger = structlog.get_logger()


class TemplateManager:
    """CRUD operations for pipeline templates with cloning and versioning."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def save(self, template: PipelineTemplate) -> PipelineTemplate:
        """Save a new template or update an existing one."""
        async with self._session_factory() as session:
            row = PipelineTemplateRow(
                id=str(template.id),
                name=template.name,
                description=template.description,
                steps_definition=[s.model_dump() for s in template.steps],
                version=template.version,
                created_by=template.created_by,
                created_at=template.created_at,
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            await session.commit()

        logger.info("template.saved", template_id=str(template.id), name=template.name)
        return template

    async def get(self, template_id: uuid.UUID) -> PipelineTemplate | None:
        """Load a template by ID."""
        async with self._session_factory() as session:
            row = await session.get(PipelineTemplateRow, str(template_id))
            if row is None:
                return None
            return self._row_to_template(row)

    async def list_all(self) -> list[PipelineTemplate]:
        """List all templates."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineTemplateRow).order_by(
                    PipelineTemplateRow.updated_at.desc()
                )
            )
            rows = result.scalars().all()
            return [self._row_to_template(r) for r in rows]

    async def update(
        self, template_id: uuid.UUID, updates: dict[str, Any]
    ) -> PipelineTemplate | None:
        """Update a template, incrementing its version."""
        async with self._session_factory() as session:
            row = await session.get(PipelineTemplateRow, str(template_id))
            if row is None:
                return None

            if "name" in updates:
                row.name = updates["name"]
            if "description" in updates:
                row.description = updates["description"]
            if "steps" in updates:
                row.steps_definition = updates["steps"]

            row.version += 1
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()

            logger.info(
                "template.updated",
                template_id=str(template_id),
                version=row.version,
            )
            return self._row_to_template(row)

    async def delete(self, template_id: uuid.UUID) -> bool:
        """Delete a template."""
        async with self._session_factory() as session:
            row = await session.get(PipelineTemplateRow, str(template_id))
            if row is None:
                return False
            await session.delete(row)
            await session.commit()

        logger.info("template.deleted", template_id=str(template_id))
        return True

    def clone(
        self,
        template: PipelineTemplate,
        inputs: dict[str, Any] | None = None,
    ) -> tuple[Pipeline, list[PipelineStep]]:
        """Clone a template into a concrete Pipeline + Steps.

        Returns a (Pipeline, list[PipelineStep]) ready for execution.
        """
        pipeline = Pipeline(
            id=uuid.uuid4(),
            template_id=template.id,
            name=template.name,
            status=PipelineStatus.PENDING,
            inputs=inputs or {},
            context=dict(inputs or {}),
        )

        # Build steps and resolve name-based dependencies to UUIDs
        steps: list[PipelineStep] = []
        name_to_id: dict[str, uuid.UUID] = {}

        for order, step_def in enumerate(template.steps):
            step_id = uuid.uuid4()
            name_to_id[step_def.name] = step_id

            step = PipelineStep(
                id=step_id,
                pipeline_id=pipeline.id,
                order=order,
                name=step_def.name,
                command_type=step_def.command_type,
                command_template=step_def.command_template,
                condition=step_def.condition,
                input_mappings=deepcopy(step_def.input_mappings),
                fan_out_on=step_def.fan_out_on,
                depends_on=[],  # resolved below
                resource_requirements=list(step_def.resource_requirements),
                timeout=step_def.timeout,
                retry_max=step_def.retry_max,
                status=StepStatus.PENDING,
            )
            steps.append(step)

        # Resolve depends_on names → UUIDs
        for step_def, step in zip(template.steps, steps):
            step.depends_on = [
                name_to_id[dep_name]
                for dep_name in step_def.depends_on_names
                if dep_name in name_to_id
            ]

        logger.info(
            "template.cloned",
            template_id=str(template.id),
            pipeline_id=str(pipeline.id),
            step_count=len(steps),
        )
        return pipeline, steps

    @staticmethod
    def _row_to_template(row: PipelineTemplateRow) -> PipelineTemplate:
        steps = [PipelineStepDef(**s) for s in (row.steps_definition or [])]
        return PipelineTemplate(
            id=uuid.UUID(row.id),
            name=row.name,
            description=row.description or "",
            steps=steps,
            version=row.version,
            created_by=row.created_by or "manual",
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
