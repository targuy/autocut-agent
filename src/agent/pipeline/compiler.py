"""LLM Pipeline Compiler — converts natural language into Pipeline DAGs.

Uses the hybrid "compile-then-execute" approach (Option C): the LLM is called
ONCE to generate the full pipeline structure (steps, dependencies, conditions,
fan-out/fan-in points).  No LLM calls happen during execution.

The compiler is **registry-aware**: it consults the ProgramRegistry for known
programs and injects their context (parameters, I/O contracts, scoring history)
into the LLM prompt.  Unknown programs trigger an interactive question flow to
gather the information needed before compilation can proceed.
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
from agent.pipeline.registry import (
    ProgramEntry,
    ProgramQuestion,
    ProgramRegistry,
    generate_questions_for_unknown,
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
      "program_name": "<name of a known program from the registry, or null>",
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
- When a step uses a known program from the registry, set ``program_name`` \
  to match the registry entry name and use its command_template and parameters.
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

    if config.provider == "gemini":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "langchain-google-genai is required for the Gemini provider. "
                "Install it with: pip install langchain-google-genai"
            ) from exc

        kwargs = {"model": config.model, "temperature": config.temperature}
        if config.api_key:
            kwargs["google_api_key"] = config.api_key
        return ChatGoogleGenerativeAI(**kwargs)

    if config.provider == "lmstudio":
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "langchain-openai is required for the LMStudio provider. "
                "Install it with: pip install langchain-openai"
            ) from exc

        kwargs = {
            "model": config.model,
            "temperature": config.temperature,
            "base_url": config.base_url or "http://localhost:1234/v1",
        }
        if config.api_key:
            kwargs["api_key"] = config.api_key
        else:
            kwargs["api_key"] = "lm-studio"  # LMStudio doesn't require a real key
        return ChatOpenAI(**kwargs)

    raise ValueError(f"Unsupported LLM provider: {config.provider!r}")


# ---------------------------------------------------------------------------
# Compilation result
# ---------------------------------------------------------------------------


class CompilationError(Exception):
    """Raised when the compiler cannot produce a valid pipeline."""


class CompilationResult:
    """Result of a pipeline compilation attempt.

    A compilation can be:
    - **complete**: pipeline and steps are ready to execute
    - **pending_info**: unknown programs were found; questions must be
      answered before compilation can proceed
    """

    def __init__(
        self,
        *,
        pipeline: Pipeline | None = None,
        steps: list[PipelineStep] | None = None,
        template: PipelineTemplate | None = None,
        pending_questions: dict[str, list[ProgramQuestion]] | None = None,
        known_programs_used: list[str] | None = None,
        unknown_programs: list[str] | None = None,
        advisories: list[dict[str, Any]] | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.steps = steps or []
        self.template = template
        self.pending_questions = pending_questions or {}
        self.known_programs_used = known_programs_used or []
        self.unknown_programs = unknown_programs or []
        self.advisories = advisories or []

    @property
    def is_complete(self) -> bool:
        """True if the pipeline is ready to execute (no pending questions)."""
        return self.pipeline is not None and not self.pending_questions

    @property
    def needs_user_input(self) -> bool:
        """True if the user must answer questions about unknown programs."""
        return bool(self.pending_questions)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class PipelineCompiler:
    """Compile natural-language descriptions into executable Pipeline DAGs.

    The compiler calls the LLM **once** to produce a full pipeline structure
    (steps, dependencies, conditions, fan-out/fan-in).  No LLM calls are made
    during pipeline execution.

    When a ``ProgramRegistry`` is provided, known programs are injected into
    the LLM prompt, and unknown programs trigger questions for the user.

    Parameters
    ----------
    llm_config:
        An ``LLMConfig`` instance specifying the provider, model, API key,
        and temperature to use for compilation.
    registry:
        Optional ``ProgramRegistry`` for program context lookup.
    """

    def __init__(
        self,
        llm_config: LLMConfig,
        registry: ProgramRegistry | None = None,
    ) -> None:
        self._llm_config = llm_config
        self._registry = registry
        self._log = logger.bind(component="pipeline_compiler")

    # ------------------------------------------------------------------
    # compile (original API preserved)
    # ------------------------------------------------------------------

    async def compile(
        self,
        description: str,
        inputs: dict[str, Any] | None = None,
        template: PipelineTemplate | None = None,
    ) -> tuple[Pipeline, list[PipelineStep]]:
        """Compile a description (or template) into a Pipeline + steps.

        This is the original API. For registry-aware compilation with
        unknown-program detection, use :meth:`compile_with_context`.

        Returns
        -------
        tuple[Pipeline, list[PipelineStep]]
        """
        result = await self.compile_with_context(description, inputs, template)
        if result.pipeline is None:
            raise CompilationError(
                "Compilation incomplete — unknown programs require user input. "
                f"Unknown: {result.unknown_programs}"
            )
        return result.pipeline, result.steps

    # ------------------------------------------------------------------
    # compile_with_context (new registry-aware API)
    # ------------------------------------------------------------------

    async def compile_with_context(
        self,
        description: str,
        inputs: dict[str, Any] | None = None,
        template: PipelineTemplate | None = None,
    ) -> CompilationResult:
        """Compile with full registry awareness.

        If all programs are known (or no registry is attached), returns a
        complete result. If unknown programs are detected, returns a result
        with ``pending_questions`` that must be answered first.
        """
        inputs = inputs or {}

        if template is not None:
            self._log.info(
                "compiling_from_template",
                template_id=str(template.id),
                template_name=template.name,
            )
            pipeline, steps = self._instantiate_from_template(template, inputs)
            return CompilationResult(
                pipeline=pipeline,
                steps=steps,
                template=template,
            )

        # --- Identify known programs in the description ---
        known_programs: list[ProgramEntry] = []
        if self._registry is not None:
            known_programs = self._registry.find_programs_for_description(description)
            self._log.info(
                "registry_lookup",
                known_matches=[p.name for p in known_programs],
            )

        # --- Collect advisories for known programs ---
        advisories: list[dict[str, Any]] = []
        for prog in known_programs:
            # Advisories are retrieved by the API layer or scoring manager;
            # here we just note which programs are in use.
            advisories_context = {
                "program": prog.name,
                "success_rate_note": (
                    "Check scoring API for detailed stats and recommendations"
                ),
            }
            advisories.append(advisories_context)

        # --- Call the LLM with enriched context ---
        self._log.info(
            "compiling_from_description",
            description_length=len(description),
            known_programs_count=len(known_programs),
        )
        raw_response = await self._call_llm(description, inputs, known_programs)

        # --- Parse JSON from response ---
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

        # --- Detect unknown programs in the compiled pipeline ---
        unknown_programs = self._detect_unknown_programs(payload)
        if unknown_programs:
            self._log.info(
                "unknown_programs_detected",
                unknown=unknown_programs,
            )
            pending_questions = {
                name: generate_questions_for_unknown(name)
                for name in unknown_programs
            }
            return CompilationResult(
                pending_questions=pending_questions,
                known_programs_used=[p.name for p in known_programs],
                unknown_programs=unknown_programs,
                advisories=advisories,
            )

        # --- Build template from parsed JSON ---
        try:
            compiled_template = self._payload_to_template(payload, known_programs)
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

        pipeline, steps = self._instantiate_from_template(compiled_template, inputs)
        return CompilationResult(
            pipeline=pipeline,
            steps=steps,
            template=compiled_template,
            known_programs_used=[p.name for p in known_programs],
            advisories=advisories,
        )

    # ------------------------------------------------------------------
    # Unknown program detection
    # ------------------------------------------------------------------

    def _detect_unknown_programs(self, payload: dict[str, Any]) -> list[str]:
        """Find program_name references in the payload that aren't in the registry."""
        if self._registry is None:
            return []

        unknown: list[str] = []
        for raw_step in payload.get("steps", []):
            program_name = raw_step.get("program_name")
            if program_name and self._registry.lookup(program_name) is None:
                if program_name not in unknown:
                    unknown.append(program_name)
        return unknown

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        description: str,
        inputs: dict[str, Any],
        known_programs: list[ProgramEntry] | None = None,
    ) -> str:
        """Send the structured prompt to the LLM and return the raw text."""
        chat_model = _build_chat_model(self._llm_config)

        user_message = self._build_user_message(
            description, inputs, known_programs or []
        )

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
        description: str,
        inputs: dict[str, Any],
        known_programs: list[ProgramEntry] | None = None,
    ) -> str:
        """Assemble the user-facing portion of the prompt.

        When known programs are provided, their context (description,
        command template, parameters, I/O contract) is injected so the
        LLM can use them directly.
        """
        parts: list[str] = [
            "Create a pipeline for the following workflow:\n",
            description,
        ]

        if inputs:
            parts.append("\n\nAvailable input variables:")
            for key, value in inputs.items():
                parts.append(f"  - {key}: {value!r}")

        if known_programs:
            parts.append("\n\n--- KNOWN PROGRAMS (use these when applicable) ---")
            for prog in known_programs:
                ctx = prog.to_compiler_context()
                parts.append(f"\nProgram: {ctx['name']}")
                parts.append(f"  Description: {ctx['description']}")
                if ctx["purpose"]:
                    parts.append(f"  Purpose: {ctx['purpose']}")
                parts.append(f"  Command type: {ctx['command_type']}")
                parts.append(f"  Command template: {ctx['command_template']}")
                if ctx["required_inputs"]:
                    parts.append(f"  Required inputs: {', '.join(ctx['required_inputs'])}")
                if ctx["expected_outputs"]:
                    parts.append(f"  Produces: {', '.join(ctx['expected_outputs'])}")
                if ctx["parameters"]:
                    parts.append("  Parameters:")
                    for p in ctx["parameters"]:
                        line = f"    - {p['name']} ({p['type']})"
                        if p.get("description"):
                            line += f": {p['description']}"
                        if p.get("current_value") is not None:
                            line += f" [current: {p['current_value']}]"
                        elif p.get("default") is not None:
                            line += f" [default: {p['default']}]"
                        parts.append(line)
            parts.append("\n--- END KNOWN PROGRAMS ---\n")
            parts.append(
                "IMPORTANT: For steps using known programs, set the ``program_name`` "
                "field to the program name and use the program's command_template with "
                "its parameters. For unknown programs, set program_name to null."
            )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Payload -> PipelineTemplate
    # ------------------------------------------------------------------

    @staticmethod
    def _payload_to_template(
        payload: dict[str, Any],
        known_programs: list[ProgramEntry] | None = None,
    ) -> PipelineTemplate:
        """Convert the raw JSON payload into a ``PipelineTemplate``.

        When known programs are provided, their registered command templates
        and resource requirements are used to enrich the compiled steps.
        """
        name = payload.get("name") or "Untitled Pipeline"
        raw_steps: list[dict[str, Any]] = payload.get("steps", [])

        if not raw_steps:
            raise ValueError("Pipeline must contain at least one step")

        # Build lookup for known programs
        program_lookup: dict[str, ProgramEntry] = {}
        if known_programs:
            program_lookup = {p.name: p for p in known_programs}

        step_defs: list[PipelineStepDef] = []
        seen_names: set[str] = set()

        for idx, raw in enumerate(raw_steps):
            step_name = raw.get("name")
            if not step_name:
                step_name = f"step_{idx + 1}"

            if step_name in seen_names:
                step_name = f"{step_name}_{idx}"
            seen_names.add(step_name)

            # Check if this step references a known program
            program_name = raw.get("program_name")
            program = program_lookup.get(program_name) if program_name else None

            # Normalise command_type with a fallback
            raw_cmd_type = str(raw.get("command_type", "shell")).lower()
            if program:
                raw_cmd_type = program.command_type.value
            try:
                command_type = CommandType(raw_cmd_type)
            except ValueError:
                command_type = CommandType.SHELL

            # Use program's command template if available
            command_template = raw.get("command_template", "")
            if program and not command_template:
                command_template = program.resolve_command()
            if not command_template:
                raise ValueError(
                    f"Step {step_name!r} is missing a command_template"
                )

            # Merge resource requirements from program registry
            resource_requirements = raw.get("resource_requirements") or []
            if program and not resource_requirements:
                resource_requirements = list(program.required_inputs)

            step_defs.append(
                PipelineStepDef(
                    name=step_name,
                    command_type=command_type,
                    command_template=command_template,
                    condition=raw.get("condition") or None,
                    input_mappings=raw.get("input_mappings") or {},
                    fan_out_on=raw.get("fan_out_on") or None,
                    depends_on_names=raw.get("depends_on_names") or [],
                    resource_requirements=resource_requirements,
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
