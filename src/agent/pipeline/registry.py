"""Program Registry — persistent knowledge base about programs used in pipelines.

Each registered program stores:
- Identity and purpose (what it does, when to use it)
- Execution context (command type, template, required inputs, expected outputs)
- Parameter schema with types, defaults, constraints, and current values
- Usage metadata (who registered, last used, version)

The registry is consulted during pipeline compilation so the LLM can
leverage known program capabilities, and unknown programs trigger an
interactive information-gathering flow.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent.core.database import ProgramRegistryRow
from agent.pipeline.models import CommandType

logger = structlog.get_logger()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Parameter types
# ---------------------------------------------------------------------------


class ParamType(StrEnum):
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    ENUM = "enum"
    PATH = "path"


class ParameterDef(BaseModel):
    """Schema for a single program parameter."""

    name: str
    type: ParamType = ParamType.STRING
    description: str = ""
    default: Any = None
    current_value: Any = None
    required: bool = False

    # Constraints (type-dependent)
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: list[str] = Field(default_factory=list)  # for enum type
    examples: list[str] = Field(default_factory=list)

    def effective_value(self) -> Any:
        """Return current_value if set, otherwise default."""
        return self.current_value if self.current_value is not None else self.default

    def validate_value(self, value: Any) -> tuple[bool, str]:
        """Validate a value against this parameter's constraints.

        Returns (is_valid, error_message).
        """
        if value is None:
            if self.required:
                return False, f"Parameter '{self.name}' is required"
            return True, ""

        if self.type == ParamType.INT:
            try:
                v = int(value)
            except (TypeError, ValueError):
                return False, f"'{self.name}' must be an integer, got {type(value).__name__}"
            if self.min_value is not None and v < self.min_value:
                return False, f"'{self.name}' must be >= {self.min_value}, got {v}"
            if self.max_value is not None and v > self.max_value:
                return False, f"'{self.name}' must be <= {self.max_value}, got {v}"

        elif self.type == ParamType.FLOAT:
            try:
                v = float(value)
            except (TypeError, ValueError):
                return False, f"'{self.name}' must be a number, got {type(value).__name__}"
            if self.min_value is not None and v < self.min_value:
                return False, f"'{self.name}' must be >= {self.min_value}, got {v}"
            if self.max_value is not None and v > self.max_value:
                return False, f"'{self.name}' must be <= {self.max_value}, got {v}"

        elif self.type == ParamType.BOOL:
            if not isinstance(value, bool):
                return False, f"'{self.name}' must be a boolean"

        elif self.type == ParamType.ENUM:
            if self.allowed_values and str(value) not in self.allowed_values:
                return False, (
                    f"'{self.name}' must be one of {self.allowed_values}, got {value!r}"
                )

        return True, ""


# ---------------------------------------------------------------------------
# Program entry
# ---------------------------------------------------------------------------


class ProgramEntry(BaseModel):
    """A registered program with its full context."""

    id: uuid.UUID = Field(default_factory=_new_id)
    name: str  # unique identifier (e.g., "facedetection", "ffmpeg_resize")
    description: str = ""  # human-readable purpose
    purpose: str = ""  # when/why to use this program
    command_type: CommandType = CommandType.SHELL
    command_template: str = ""  # e.g., "python facedetection.py --threshold {threshold}"

    # I/O contract
    required_inputs: list[str] = Field(default_factory=list)  # artifact keys consumed
    expected_outputs: list[str] = Field(default_factory=list)  # artifact keys produced

    # Parameters
    parameters: list[ParameterDef] = Field(default_factory=list)

    # Metadata
    registered_by: str = "manual"  # "manual" | "llm" | "api"
    tags: list[str] = Field(default_factory=list)  # e.g., ["filter", "video", "gpu"]
    version: str = "1.0"
    active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def get_parameter(self, name: str) -> ParameterDef | None:
        """Find a parameter by name."""
        for p in self.parameters:
            if p.name == name:
                return p
        return None

    def get_effective_params(self) -> dict[str, Any]:
        """Return all parameters with their effective values."""
        return {p.name: p.effective_value() for p in self.parameters}

    def set_parameter(self, name: str, value: Any) -> tuple[bool, str]:
        """Update a parameter's current_value with validation.

        Returns (success, error_message).
        """
        param = self.get_parameter(name)
        if param is None:
            return False, f"Unknown parameter '{name}' for program '{self.name}'"
        valid, err = param.validate_value(value)
        if not valid:
            return False, err
        param.current_value = value
        self.updated_at = _utcnow()
        return True, ""

    def resolve_command(self, overrides: dict[str, Any] | None = None) -> str:
        """Resolve the command template with effective parameter values.

        Parameters in *overrides* take precedence over stored values.
        """
        params = self.get_effective_params()
        if overrides:
            params.update(overrides)

        result = self.command_template
        for key, val in params.items():
            result = result.replace(f"{{{key}}}", str(val) if val is not None else "")
        return result

    def to_compiler_context(self) -> dict[str, Any]:
        """Return a dict suitable for including in the LLM compiler prompt."""
        return {
            "name": self.name,
            "description": self.description,
            "purpose": self.purpose,
            "command_type": self.command_type.value,
            "command_template": self.command_template,
            "required_inputs": self.required_inputs,
            "expected_outputs": self.expected_outputs,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type.value,
                    "description": p.description,
                    "default": p.default,
                    "current_value": p.current_value,
                    "required": p.required,
                    **({"min": p.min_value} if p.min_value is not None else {}),
                    **({"max": p.max_value} if p.max_value is not None else {}),
                    **({"allowed": p.allowed_values} if p.allowed_values else {}),
                }
                for p in self.parameters
            ],
            "tags": self.tags,
        }


# ---------------------------------------------------------------------------
# Questions for unknown programs
# ---------------------------------------------------------------------------


class ProgramQuestion(BaseModel):
    """A question the system needs answered to register a new program."""

    field: str  # which ProgramEntry field this answers
    question: str  # human-readable question
    required: bool = True
    hint: str = ""  # example or guidance


def generate_questions_for_unknown(program_name: str) -> list[ProgramQuestion]:
    """Generate the questions needed to register an unknown program."""
    return [
        ProgramQuestion(
            field="description",
            question=f"What does '{program_name}' do? Describe its purpose briefly.",
            required=True,
            hint="e.g., 'Detects faces in video frames and filters clips by face count'",
        ),
        ProgramQuestion(
            field="command_type",
            question=f"How is '{program_name}' executed?",
            required=True,
            hint="One of: python, ffmpeg, shell",
        ),
        ProgramQuestion(
            field="command_template",
            question=f"What is the full command to run '{program_name}'? Use {{param}} for parameters.",
            required=True,
            hint="e.g., 'python facedetection.py --input {input_path} --threshold {threshold}'",
        ),
        ProgramQuestion(
            field="required_inputs",
            question="What inputs does it need? (comma-separated artifact keys from previous steps)",
            required=False,
            hint="e.g., 'clip_path, timecodes'",
        ),
        ProgramQuestion(
            field="expected_outputs",
            question="What does it produce? (comma-separated artifact keys)",
            required=False,
            hint="e.g., 'filtered_timecodes, face_count'",
        ),
        ProgramQuestion(
            field="parameters",
            question=(
                "List its configurable parameters as 'name:type:default:description' "
                "(one per line, type is string/int/float/bool/enum)"
            ),
            required=False,
            hint="e.g., 'threshold:float:0.5:Face detection confidence threshold'",
        ),
        ProgramQuestion(
            field="resource_requirements",
            question="Does it need special resources? (e.g., GPU)",
            required=False,
            hint="e.g., 'gpu:cuda:0' or leave empty for CPU-only",
        ),
    ]


def parse_parameter_spec(spec: str) -> ParameterDef:
    """Parse a 'name:type:default:description' string into a ParameterDef."""
    parts = spec.strip().split(":", 3)
    name = parts[0].strip()
    ptype = ParamType.STRING
    default: Any = None
    desc = ""

    if len(parts) >= 2:
        raw_type = parts[1].strip().lower()
        try:
            ptype = ParamType(raw_type)
        except ValueError:
            ptype = ParamType.STRING

    if len(parts) >= 3:
        raw_default = parts[2].strip()
        if raw_default:
            if ptype == ParamType.INT:
                try:
                    default = int(raw_default)
                except ValueError:
                    default = raw_default
            elif ptype == ParamType.FLOAT:
                try:
                    default = float(raw_default)
                except ValueError:
                    default = raw_default
            elif ptype == ParamType.BOOL:
                default = raw_default.lower() in ("true", "1", "yes")
            else:
                default = raw_default

    if len(parts) >= 4:
        desc = parts[3].strip()

    return ParameterDef(name=name, type=ptype, default=default, description=desc)


# ---------------------------------------------------------------------------
# Registry manager (persistence layer)
# ---------------------------------------------------------------------------


class ProgramRegistry:
    """CRUD manager for the program knowledge base.

    Programs are stored in the ``program_registry`` database table and
    cached in memory for fast lookup during compilation.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory
        self._cache: dict[str, ProgramEntry] = {}

    async def load_cache(self) -> None:
        """Pre-load all active programs into memory."""
        programs = await self.list_all(active_only=True)
        self._cache = {p.name: p for p in programs}
        logger.info("registry.cache_loaded", count=len(self._cache))

    def lookup(self, name: str) -> ProgramEntry | None:
        """Fast in-memory lookup by program name."""
        return self._cache.get(name)

    def lookup_by_tag(self, tag: str) -> list[ProgramEntry]:
        """Find all programs with a given tag."""
        return [p for p in self._cache.values() if tag in p.tags]

    def get_all_cached(self) -> list[ProgramEntry]:
        """Return all cached program entries."""
        return list(self._cache.values())

    def find_programs_for_description(self, description: str) -> list[ProgramEntry]:
        """Find programs whose name or tags appear in a description.

        This is a simple keyword match used during pipeline compilation
        to identify which known programs a user might be referring to.
        """
        desc_lower = description.lower()
        matches: list[ProgramEntry] = []
        for prog in self._cache.values():
            if prog.name.lower() in desc_lower:
                matches.append(prog)
                continue
            # Check tags and description keywords
            for tag in prog.tags:
                if tag.lower() in desc_lower:
                    matches.append(prog)
                    break
        return matches

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def register(self, entry: ProgramEntry) -> ProgramEntry:
        """Register or update a program in the knowledge base."""
        async with self._session_factory() as session:
            existing = await session.get(ProgramRegistryRow, str(entry.id))
            if existing is not None:
                # Update existing
                existing.name = entry.name
                existing.description = entry.description
                existing.purpose = entry.purpose
                existing.command_type = entry.command_type.value
                existing.command_template = entry.command_template
                existing.required_inputs = entry.required_inputs
                existing.expected_outputs = entry.expected_outputs
                existing.parameters = [p.model_dump() for p in entry.parameters]
                existing.registered_by = entry.registered_by
                existing.tags = entry.tags
                existing.version = entry.version
                existing.active = entry.active
                existing.updated_at = _utcnow()
            else:
                row = ProgramRegistryRow(
                    id=str(entry.id),
                    name=entry.name,
                    description=entry.description,
                    purpose=entry.purpose,
                    command_type=entry.command_type.value,
                    command_template=entry.command_template,
                    required_inputs=entry.required_inputs,
                    expected_outputs=entry.expected_outputs,
                    parameters=[p.model_dump() for p in entry.parameters],
                    registered_by=entry.registered_by,
                    tags=entry.tags,
                    version=entry.version,
                    active=entry.active,
                    created_at=entry.created_at,
                    updated_at=entry.updated_at,
                )
                session.add(row)
            await session.commit()

        self._cache[entry.name] = entry
        logger.info("registry.registered", program=entry.name, id=str(entry.id))
        return entry

    async def get(self, program_id: uuid.UUID) -> ProgramEntry | None:
        """Load a program by ID."""
        async with self._session_factory() as session:
            row = await session.get(ProgramRegistryRow, str(program_id))
            if row is None:
                return None
            return self._row_to_entry(row)

    async def get_by_name(self, name: str) -> ProgramEntry | None:
        """Load a program by name."""
        cached = self._cache.get(name)
        if cached is not None:
            return cached

        async with self._session_factory() as session:
            result = await session.execute(
                select(ProgramRegistryRow).where(ProgramRegistryRow.name == name)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            entry = self._row_to_entry(row)
            self._cache[entry.name] = entry
            return entry

    async def list_all(self, active_only: bool = False) -> list[ProgramEntry]:
        """List all programs, optionally filtered to active only."""
        async with self._session_factory() as session:
            query = select(ProgramRegistryRow).order_by(ProgramRegistryRow.name)
            if active_only:
                query = query.where(ProgramRegistryRow.active.is_(True))
            result = await session.execute(query)
            rows = result.scalars().all()
            return [self._row_to_entry(r) for r in rows]

    async def update_parameters(
        self, name: str, param_updates: dict[str, Any]
    ) -> tuple[ProgramEntry | None, list[str]]:
        """Update parameter values for a program.

        Returns (updated_entry, list_of_errors). If the entry is not found,
        returns (None, [error]).
        """
        entry = await self.get_by_name(name)
        if entry is None:
            return None, [f"Program '{name}' not found"]

        errors: list[str] = []
        for param_name, value in param_updates.items():
            ok, err = entry.set_parameter(param_name, value)
            if not ok:
                errors.append(err)

        if not errors:
            await self.register(entry)

        return entry, errors

    async def deactivate(self, name: str) -> bool:
        """Mark a program as inactive (soft delete)."""
        entry = await self.get_by_name(name)
        if entry is None:
            return False
        entry.active = False
        await self.register(entry)
        self._cache.pop(name, None)
        return True

    async def delete(self, program_id: uuid.UUID) -> bool:
        """Hard-delete a program from the registry."""
        async with self._session_factory() as session:
            row = await session.get(ProgramRegistryRow, str(program_id))
            if row is None:
                return False
            name = row.name
            await session.delete(row)
            await session.commit()

        self._cache.pop(name, None)
        logger.info("registry.deleted", program=name, id=str(program_id))
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: ProgramRegistryRow) -> ProgramEntry:
        params = [ParameterDef(**p) for p in (row.parameters or [])]
        return ProgramEntry(
            id=uuid.UUID(row.id),
            name=row.name,
            description=row.description or "",
            purpose=row.purpose or "",
            command_type=CommandType(row.command_type),
            command_template=row.command_template or "",
            required_inputs=row.required_inputs or [],
            expected_outputs=row.expected_outputs or [],
            parameters=params,
            registered_by=row.registered_by or "manual",
            tags=row.tags or [],
            version=row.version or "1.0",
            active=row.active if row.active is not None else True,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
