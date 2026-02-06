"""LLM Pipeline Compiler — converts natural language into Pipeline DAGs.

Uses the hybrid "compile-then-execute" approach (Option C): the LLM is called
ONCE to generate the full pipeline structure (steps, dependencies, conditions,
fan-out/fan-in points).  No LLM calls happen during execution.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import structlog

from agent.core.config import LLMConfig
from agent.pipeline.models import (
    CommandType,
    Pipeline,
    PipelineStatus,
    PipelineStep,
    PipelineStepDef,
    PipelineTemplate,
    StepStatus,
)

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# System prompt sent to the LLM
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a pipeline architect.  Given a natural-language description of a \
data-processing or automation workflow, you MUST return a single JSON object \
(no markdown fences, no commentary) that describes a directed acyclic graph \
of execution steps.

JSON schema:
{
  "name": "<short pipeline name>",
  "steps": [
    {
      "name": "<unique step name>",
      "command_type": "python" | "ffmpeg" | "shell",
      "command_template": "<command with {variable} placeholders>",
      "condition": "<condition expression or null>",
      "input_mappings": {"<local_name>": "<step_name>.<artifact_key>"},
      "fan_out_on": "<artifact key to parallelise on, or null>",
      "depends_on_names": ["<name of a step this depends on>"],
      "resource_requirements": ["<resource id, e.g. gpu:cuda:0>"],
      "timeout": 3600,
      "retry_max": 0
    }
  ]
}

Rules:
- Every step MUST have a unique ``name``.
- ``depends_on_names`` references other steps by their ``name`` field.
- The graph MUST be a DAG (no cycles).
- ``command_type`` MUST be one of: python, ffmpeg, shell.
- Only include ``condition``, ``fan_out_on``, ``input_mappings``, and \
  ``resource_requirements`` when they are meaningful; otherwise set them \
  to null or empty.
- ``timeout`` defaults to 3600 and ``retry_max`` defaults to 0 when omitted.
- Return ONLY the JSON object.  No explanation, no markdown code fences.
"""


# ---------------------------------------------------------------------------
# Helper: extract JSON from possibly-messy LLM output
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from raw LLM output.

    Handles common quirks such as markdown code fences wrapping the JSON.
    """
    # Strip leading/trailing whitespace
    text = text.strip()

    # Remove markdown code fences if present
    fence_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
    match = fence_pattern.search(text)
    if match:
        text = match.group(1).strip()

    # Try direct parse first
    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        pass

    # Fallback: find the first { … } block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

    raise ValueError("Could not extract valid JSON from LLM response")


# ---------------------------------------------------------------------------
# Helper: build LangChain chat model from config
# ---------------------------------------------------------------------------


def _build_chat_model(config: LLMConfig) -> Any:
    """Instantiate a LangChain chat model based on the provider setting."""
    if config.provider == "openai":
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "langchain-openai is required for the OpenAI provider. "
                "Install it with: pip install langchain-openai"
            ) from exc

        kwargs: dict[str, Any] = {
            "model": config.model,
            "temperature": config.temperature,
        }
        if config.api_key:
            kwargs["api_key"] = config.api_key
        return ChatOpenAI(**kwargs)

    if config.provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "langchain-anthropic is required for the Anthropic provider. "
                "Install it with: pip install langchain-anthropic"
            ) from exc

        kwargs = {
            "model": config.model,
            "temperature": config.temperature,
        }
        if config.api_key:
            kwargs["api_key"] = config.api_key
        return ChatAnthropic(**kwargs)

    raise ValueError(f"Unsupported LLM provider: {config.provider!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class CompilationError(Exception):
    """Raised when the compiler cannot produce a valid pipeline."""


class PipelineCompiler:
    """Compile natural-language descriptions into executable Pipeline DAGs.

    The compiler calls the LLM **once** to produce a full pipeline structure
    (steps, dependencies, conditions, fan-out/fan-in).  No LLM calls are made
    during pipeline execution.

    Parameters
    ----------
    llm_config:
        An ``LLMConfig`` instance specifying the provider, model, API key,
        and temperature to use for compilation.
    """

    def __init__(self, llm_config: LLMConfig) -> None:
        self._llm_config = llm_config
        self._log = logger.bind(component="pipeline_compiler")

    # ------------------------------------------------------------------
    # compile
    # ------------------------------------------------------------------

    async def compile(
        self,
        description: str,
        inputs: dict[str, Any] | None = None,
        template: PipelineTemplate | None = None,
    ) -> tuple[Pipeline, list[PipelineStep]]:
        """Compile a description (or template) into a Pipeline + steps.

        Parameters
        ----------
        description:
            Natural-language description of the desired workflow.  Ignored
            when *template* is provided.
        inputs:
            Optional mapping of initial input values for the pipeline.
        template:
            If supplied, the LLM call is skipped and the pipeline is built
            directly from this template.

        Returns
        -------
        tuple[Pipeline, list[PipelineStep]]
            A ``Pipeline`` instance and an ordered list of ``PipelineStep``
            objects with dependency UUIDs fully resolved.

        Raises
        ------
        CompilationError
            If the LLM response cannot be parsed into a valid pipeline.
        """
        inputs = inputs or {}

        if template is not None:
            self._log.info(
                "compiling_from_template",
                template_id=str(template.id),
                template_name=template.name,
            )
            return self._instantiate_from_template(template, inputs)

        self._log.info(
            "compiling_from_description",
            description_length=len(description),
        )

        # --- Call the LLM ---------------------------------------------------
        raw_response = await self._call_llm(description, inputs)

        # --- Parse JSON from response ----------------------------------------
        try:
            payload = _extract_json(raw_response)
        except ValueError as exc:
            self._log.error(
                "json_extraction_failed",
                error=str(exc),
                raw_response=raw_response[:500],
            )
            raise CompilationError(
                f"Failed to extract JSON from LLM response: {exc}"
            ) from exc

        # --- Build template from parsed JSON ---------------------------------
        try:
            compiled_template = self._payload_to_template(payload)
        except Exception as exc:
            self._log.error(
                "template_construction_failed",
                error=str(exc),
                payload_keys=list(payload.keys()) if isinstance(payload, dict) else None,
            )
            raise CompilationError(
                f"Failed to build pipeline template from LLM output: {exc}"
            ) from exc

        self._log.info(
            "compilation_successful",
            template_name=compiled_template.name,
            step_count=len(compiled_template.steps),
        )

        return self._instantiate_from_template(compiled_template, inputs)

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    async def _call_llm(
        self, description: str, inputs: dict[str, Any]
    ) -> str:
        """Send the structured prompt to the LLM and return the raw text."""
        chat_model = _build_chat_model(self._llm_config)

        user_message = self._build_user_message(description, inputs)

        self._log.debug(
            "calling_llm",
            provider=self._llm_config.provider,
            model=self._llm_config.model,
        )

        try:
            from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "langchain-core is required for pipeline compilation. "
                "Install it with: pip install langchain-core"
            ) from exc

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ]

        response = await chat_model.ainvoke(messages)
        raw_text: str = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )

        self._log.debug(
            "llm_response_received",
            response_length=len(raw_text),
        )

        return raw_text

    # ------------------------------------------------------------------
    # User message construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_message(
        description: str, inputs: dict[str, Any]
    ) -> str:
        """Assemble the user-facing portion of the prompt."""
        parts: list[str] = [
            "Create a pipeline for the following workflow:\n",
            description,
        ]

        if inputs:
            parts.append("\n\nAvailable input variables:")
            for key, value in inputs.items():
                parts.append(f"  - {key}: {value!r}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Payload -> PipelineTemplate
    # ------------------------------------------------------------------

    @staticmethod
    def _payload_to_template(payload: dict[str, Any]) -> PipelineTemplate:
        """Convert the raw JSON payload into a ``PipelineTemplate``."""
        name = payload.get("name") or "Untitled Pipeline"
        raw_steps: list[dict[str, Any]] = payload.get("steps", [])

        if not raw_steps:
            raise ValueError("Pipeline must contain at least one step")

        step_defs: list[PipelineStepDef] = []
        seen_names: set[str] = set()

        for idx, raw in enumerate(raw_steps):
            step_name = raw.get("name")
            if not step_name:
                step_name = f"step_{idx + 1}"

            if step_name in seen_names:
                step_name = f"{step_name}_{idx}"
            seen_names.add(step_name)

            # Normalise command_type with a fallback
            raw_cmd_type = str(raw.get("command_type", "shell")).lower()
            try:
                command_type = CommandType(raw_cmd_type)
            except ValueError:
                command_type = CommandType.SHELL

            command_template = raw.get("command_template", "")
            if not command_template:
                raise ValueError(
                    f"Step {step_name!r} is missing a command_template"
                )

            step_defs.append(
                PipelineStepDef(
                    name=step_name,
                    command_type=command_type,
                    command_template=command_template,
                    condition=raw.get("condition") or None,
                    input_mappings=raw.get("input_mappings") or {},
                    fan_out_on=raw.get("fan_out_on") or None,
                    depends_on_names=raw.get("depends_on_names") or [],
                    resource_requirements=raw.get("resource_requirements") or [],
                    timeout=int(raw.get("timeout", 3600)),
                    retry_max=int(raw.get("retry_max", 0)),
                )
            )

        return PipelineTemplate(
            name=name,
            description=f"LLM-compiled pipeline: {name}",
            steps=step_defs,
            created_by="llm",
        )

    # ------------------------------------------------------------------
    # Template -> Pipeline + PipelineStep list
    # ------------------------------------------------------------------

    @staticmethod
    def _instantiate_from_template(
        template: PipelineTemplate,
        inputs: dict[str, Any],
    ) -> tuple[Pipeline, list[PipelineStep]]:
        """Create a ``Pipeline`` and resolved ``PipelineStep`` list from a template."""
        pipeline = Pipeline(
            template_id=template.id,
            name=template.name,
            status=PipelineStatus.PENDING,
            inputs=inputs,
        )

        # First pass: create steps and build a name -> UUID lookup
        name_to_id: dict[str, uuid.UUID] = {}
        steps: list[PipelineStep] = []

        for order, step_def in enumerate(template.steps):
            step = PipelineStep(
                pipeline_id=pipeline.id,
                order=order,
                name=step_def.name,
                command_type=step_def.command_type,
                command_template=step_def.command_template,
                condition=step_def.condition,
                input_mappings=step_def.input_mappings,
                fan_out_on=step_def.fan_out_on,
                depends_on=[],  # resolved in the second pass
                resource_requirements=step_def.resource_requirements,
                timeout=step_def.timeout,
                retry_max=step_def.retry_max,
                status=StepStatus.PENDING,
            )
            name_to_id[step_def.name] = step.id
            steps.append(step)

        # Second pass: resolve depends_on names to UUIDs
        for step, step_def in zip(steps, template.steps):
            resolved_deps: list[uuid.UUID] = []
            for dep_name in step_def.depends_on_names:
                dep_id = name_to_id.get(dep_name)
                if dep_id is None:
                    raise CompilationError(
                        f"Step {step.name!r} depends on unknown step "
                        f"{dep_name!r}. Known steps: "
                        f"{sorted(name_to_id.keys())}"
                    )
                resolved_deps.append(dep_id)
            step.depends_on = resolved_deps

        return pipeline, steps
