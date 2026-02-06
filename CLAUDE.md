# CLAUDE.md - AI Assistant Guide for AutoCut-Agent

## Project Overview

AutoCut-Agent is a **pipeline-oriented task orchestration system** for executing multi-step workflows on media files and Python programs. The LLM is the primary interface: users describe complex processing chains in natural language and the system compiles them into executable pipelines with dependency tracking, fan-out/fan-in, conditional branching, and resumability.

**Primary use case:** Video editing pipelines — e.g., "resize to 720p, detect scene cuts, run face detection on each clip, filter by criteria, assemble final video" — described in natural language, compiled into a DAG of steps, and executed with GPU resource coordination.

**Tech stack:** Python 3.10+, FastAPI, SQLAlchemy 2.0, Pydantic, LangChain, APScheduler, Watchdog, structlog, Redis (optional), SQLite/PostgreSQL, Docker

**License:** MIT | **Version:** 0.1.0 (Bootstrap)

## Core Concepts

### Pipelines (not flat queues)

The central abstraction is a **Pipeline**: an ordered DAG of Steps where each step produces artifacts consumed by downstream steps. This replaces the flat task-in-queue model.

```
Pipeline: "Video editing workflow"
├── Step 1: ffmpeg resize (conditional)   → artifacts: resized_path
├── Step 2: ffmpeg scene detect           → artifacts: [timecodes]
├── Step 3: facedetection.py (fan-out)    → artifacts: [filtered_timecodes]
├── Step 4: other_filter.py (fan-out)     → artifacts: [final_timecodes]
└── Step 5: ffmpeg assembly (fan-in)      → artifacts: final_video_path
```

Key properties:
- **Dependency tracking** — steps declare which previous step outputs they consume
- **Fan-out** — one step can spawn N parallel executions (e.g., process each clip independently)
- **Fan-in** — a step can wait for all parallel branches to complete before running (e.g., assembly)
- **Conditional branching** — steps can be skipped based on conditions (e.g., "if resolution != 720p")
- **Mixed executors** — steps can run ffmpeg, Python scripts, or shell commands
- **Artifact passing** — structured data (timecodes, file paths, JSON) flows between steps

### LLM as Pipeline Compiler (Hybrid Model)

The LLM uses a **compile-then-execute** hybrid approach (Option C):

1. **Compile phase** — LLM parses natural language into a Pipeline definition with steps, dependencies, conditions, and fan-out/fan-in points. Conditional branches are baked into the pipeline as executable rules (e.g., `if resolution != 720p then resize`). The LLM is called once at compile time.
2. **Execute phase** — The orchestrator runs the compiled pipeline mechanically. No LLM calls during execution. Conditions are evaluated by the engine, not re-interpreted by the LLM.

This is predictable, debuggable, and cost-efficient compared to calling the LLM at every step.

### Pipeline Templates

Generic pipelines (not bound to a specific input) are stored as **templates** that can be:
- **Saved** from any successful LLM compilation or manual creation
- **Cloned** with new inputs to create a pipeline instance (e.g., same workflow, different video)
- **Edited** via GUI or API before execution
- **Versioned** so changes to a template don't affect running instances

### Resumability and Step Skipping

Pipelines support **interruption and resumption**:
- If a step produces no result (empty output, error), the pipeline halts at that step with status `interrupted`
- On resume, completed steps are skipped — their cached artifacts are reused
- Partially completed fan-out steps resume only the incomplete branches
- Users can manually skip a failed step via GUI/API, providing substitute artifacts or accepting empty output
- A pipeline run from a template reuses artifacts from previous runs of the same template+input if still valid (cache hit)

### Step State Machine

```
pending → running → completed
                  → failed (retryable) → running (retry)
                  → failed (terminal)
                  → skipped (condition false, or manual skip)
                  → interrupted (no result, awaiting user action)
```

Pipeline-level status derives from step states:
- `running` if any step is running
- `waiting` if blocked on resource or dependency
- `interrupted` if a step needs user action
- `completed` if all steps completed/skipped
- `failed` if a terminal failure with no skip

## Repository Structure

```
autocut-agent/
├── src/agent/                    # Main source code
│   ├── core/                     # Core orchestrator, config, state
│   │   ├── orchestrator.py       # AgentOrchestrator - central coordination
│   │   ├── config.py             # load_config(), AgentConfig Pydantic model
│   │   └── state.py              # State management
│   ├── pipeline/                 # Pipeline engine (core abstraction)
│   │   ├── models.py             # Pipeline, PipelineStep, StepExecution, Artifact
│   │   ├── compiler.py           # LLM-powered natural language → Pipeline compilation
│   │   ├── engine.py             # PipelineEngine - DAG execution, fan-out/fan-in
│   │   ├── templates.py          # PipelineTemplate storage, cloning, versioning
│   │   └── conditions.py         # Conditional branch evaluation
│   ├── triggers/                 # Trigger systems
│   │   ├── scheduler.py          # APScheduler cron/interval triggers
│   │   ├── watcher.py            # Watchdog file system monitoring
│   │   ├── api.py                # FastAPI REST trigger endpoints
│   │   └── llm.py                # LangChain NLP command parsing
│   ├── queue/                    # Queue management
│   │   ├── manager.py            # QueueManager + TaskDispatcher
│   │   ├── worker.py             # Worker implementation
│   │   └── models.py             # Queue/Task data models
│   ├── resources/                # Resource management
│   │   ├── manager.py            # ResourceManager with acquire/release
│   │   ├── gpu.py                # CUDA/PyTorch detection, VRAM monitoring
│   │   └── locks.py              # RedisLockManager (or local fallback)
│   ├── executor/                 # Program execution engine
│   │   ├── runner.py             # ProgramExecutor - subprocess management
│   │   ├── venv.py               # Virtual environment activation/isolation
│   │   └── capture.py            # Real-time log streaming, output collection
│   ├── monitoring/               # Logging, metrics, alerting
│   │   ├── logger.py             # structlog structured JSON logging
│   │   ├── metrics.py            # Prometheus metrics export
│   │   └── alerts.py             # AlertManager (email, webhook, Slack, Discord)
│   ├── api/                      # REST API (FastAPI)
│   │   ├── main.py               # FastAPI app creation, CORS, router includes
│   │   ├── auth.py               # Authentication (JWT, API keys)
│   │   └── routes/               # API endpoint modules
│   │       ├── pipelines.py      # /api/v1/pipelines endpoints
│   │       ├── templates.py      # /api/v1/templates endpoints
│   │       ├── queues.py         # /api/v1/queues endpoints
│   │       ├── tasks.py          # /api/v1/tasks endpoints
│   │       └── status.py         # /api/v1/status, /health, /metrics
│   ├── gui/                      # Web frontend (React)
│   └── cli.py                    # Command-line interface entry point
├── tests/                        # Test suite (pytest)
│   ├── unit/                     # Unit tests
│   ├── integration/              # Integration tests
│   └── e2e/                      # End-to-end tests
├── docs/                         # Documentation
│   └── ARCHITECTURE.md           # System architecture and design
├── examples/                     # Usage examples
│   ├── simple_task.py            # Basic task execution
│   └── gpu_task.py               # GPU-locked task example
├── configs/                      # Configuration templates
│   ├── default.yaml              # Default/production configuration
│   └── development.yaml          # Development configuration
├── scripts/                      # Setup and deployment scripts
│   ├── setup.sh                  # Linux/macOS setup
│   ├── setup.ps1                 # Windows setup
│   └── docker-entrypoint.sh      # Container entrypoint
├── .vscode/                      # VSCode configuration
│   ├── settings.json             # Editor settings, Python config
│   ├── launch.json               # Debug configurations
│   └── tasks.json                # Common dev tasks
├── .github/
│   └── copilot-instructions.md   # GitHub Copilot instructions
├── pyproject.toml                # Poetry deps, project metadata, tool config
├── requirements.txt              # Pip fallback dependencies
├── Dockerfile                    # Multi-stage Docker build
├── docker-compose.yml            # Full stack (agent + PostgreSQL + Redis)
├── .env.template                 # Environment variables template
├── .gitignore                    # Python project ignore patterns
├── .cursorrules                  # Cursor AI rules
├── .aider.conf.yml               # Aider configuration
├── autocut-agent.code-workspace  # VSCode workspace file
├── README.md                     # Project overview and quick start
├── AI-AGENTS-GUIDE.md            # Comprehensive guide for AI assistants
├── CONTRIBUTING.md               # Contribution guidelines
├── specifications-v1.md          # Complete project specifications (30KB)
└── LICENSE                       # MIT License
```

## Architecture

```
LLM Pipeline Compiler (natural language → Pipeline DAG)
        |
Trigger Layer (Scheduler | File Watcher | API | GUI)
        |
  Agent Core / Orchestrator (AgentOrchestrator)
        |
  Pipeline Engine (DAG execution, fan-out/fan-in, conditions, resume)
        |
    Queue Manager (priority + max_workers per queue)
        |
   Resource Manager (GPU/CPU/Custom locking)
        |
    Executor Pool (ffmpeg, Python, shell — subprocess, venv, capture)
        |
 Monitoring & Logging (structlog, Prometheus, alerts)
        |
    API Layer (FastAPI REST, CORS, auth)
        |
    Web GUI (React — pipelines, queues, monitoring, LLM chat)
```

### Pipeline Engine (new core layer)

The pipeline engine sits between the orchestrator and the queue manager. It:

1. Receives a compiled Pipeline (from LLM compiler, API, or template clone)
2. Resolves the step DAG and determines which steps are ready to run
3. Evaluates conditions on each step — skips if condition is false
4. For fan-out steps, spawns N parallel step executions
5. Submits ready steps to the queue manager as individual tasks
6. On task completion, stores artifacts and advances the DAG
7. For fan-in steps, waits until all upstream branches complete
8. On interruption (no result), pauses the pipeline and awaits user action
9. On resume, loads cached artifacts for completed steps and continues from the interruption point

### Pipeline Data Model

```python
class PipelineTemplate:
    id: UUID
    name: str
    description: str
    steps: list[PipelineStepDef]    # step definitions (no runtime state)
    version: int
    created_by: str                 # "llm" | "manual" | "api"

class Pipeline:
    id: UUID
    template_id: UUID | None        # source template, if cloned
    name: str
    status: PipelineStatus          # running | waiting | interrupted | completed | failed
    inputs: dict                    # runtime inputs (e.g., video path)
    context: dict                   # shared mutable state across steps
    created_at: datetime
    resumed_at: datetime | None

class PipelineStep:
    id: UUID
    pipeline_id: UUID
    order: int
    name: str
    command_type: str               # "ffmpeg" | "python" | "shell"
    command_template: str           # with {variable} placeholders
    condition: str | None           # expression evaluated at runtime, skip if false
    inputs: dict                    # references to previous step artifacts
    fan_out_on: str | None          # artifact key to parallelize over
    depends_on: list[UUID]          # step IDs that must complete first (fan-in)
    status: StepStatus
    resource_requirements: list[str]  # e.g., ["gpu:cuda:0"]

class StepExecution:
    id: UUID
    step_id: UUID
    index: int | None               # fan-out index (0, 1, 2, ...) or None
    status: ExecutionStatus
    started_at: datetime | None
    completed_at: datetime | None
    result: dict | None
    error: str | None

class Artifact:
    id: UUID
    pipeline_id: UUID
    step_id: UUID
    execution_id: UUID | None       # None for aggregated fan-in artifacts
    key: str                        # e.g., "timecodes", "resized_path"
    value: JSON                     # structured data
    file_path: str | None           # for large outputs (video files, etc.)
    created_at: datetime
```

### Trigger Layer

Four trigger mechanisms initiate pipeline execution:

| Trigger | Library | Key Features |
|---------|---------|-------------|
| **Scheduler** | APScheduler | Cron + interval scheduling, persistent job storage, timezone-aware |
| **File Watcher** | Watchdog | Pattern matching, debouncing, recursive directory monitoring |
| **API** | FastAPI | REST endpoints, auth, rate limiting, webhook support |
| **LLM** | LangChain | NLP pipeline compilation, intent recognition, multi-turn conversation |

### Resource Management

`ResourceManager` handles exclusive and shared resource allocation via `RedisLockManager` (or local locks fallback):

- **GPU resources** - CUDA/PyTorch detection, exclusive locking (one task per GPU), VRAM monitoring, multi-GPU
- **CPU resources** - Core allocation, shared access with configurable `max_concurrent`
- **Custom resources** - Plugin architecture for license tokens, API quotas, etc.

Key methods: `acquire(resource_id, task_id) -> bool` and `release(resource_id, task_id)`.

### Executor Layer

`ProgramExecutor` runs programs in isolated environments. Supports multiple command types:

- **Python** - subprocess with optional venv activation, dependency isolation
- **ffmpeg** - direct CLI execution with argument templating
- **Shell** - arbitrary shell commands

All executors share: stdout/stderr capture, timeout enforcement, error handling, real-time log streaming.

### Monitoring Layer

`MonitoringSystem` using structlog + Prometheus + AlertManager:

- **Logging** - Structured JSON via structlog, context binding (pipeline_id, step_id, task_id), log rotation
- **Metrics** - Prometheus export: pipeline/step/task counts and durations, resource utilization, queue depth
- **Alerting** - Email, webhook, Slack, Discord channels with throttling and deduplication
- **Tracing** - OpenTelemetry integration, request ID propagation

### API Layer

FastAPI application at `/api/v1/`:

**Pipelines:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/pipelines` | Create pipeline (from template or inline) |
| POST | `/pipelines/compile` | LLM compile natural language → pipeline |
| GET | `/pipelines` | List pipelines |
| GET | `/pipelines/{id}` | Pipeline details with step statuses |
| POST | `/pipelines/{id}/resume` | Resume interrupted pipeline |
| POST | `/pipelines/{id}/cancel` | Cancel running pipeline |
| GET | `/pipelines/{id}/artifacts` | List pipeline artifacts |
| POST | `/pipelines/{id}/steps/{step_id}/skip` | Skip a failed/interrupted step |

**Templates:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/templates` | Create/save template |
| GET | `/templates` | List templates |
| GET | `/templates/{id}` | Template details |
| POST | `/templates/{id}/clone` | Clone template with new inputs |
| PUT | `/templates/{id}` | Update template |
| DELETE | `/templates/{id}` | Delete template |

**Queues:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/queues` | Create queue |
| GET | `/queues` | List queues |
| GET | `/queues/{name}` | Queue details |
| POST | `/queues/{name}/pause` | Pause queue |
| POST | `/queues/{name}/resume` | Resume queue |
| DELETE | `/queues/{name}` | Delete queue |

**Tasks:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tasks` | Submit standalone task |
| GET | `/tasks` | List tasks |
| GET | `/tasks/{id}` | Task details |
| GET | `/tasks/{id}/logs` | Task logs |
| GET | `/tasks/{id}/output` | Task output |
| DELETE | `/tasks/{id}` | Cancel task |

**System:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | System status |
| GET | `/health` | Health check |
| GET | `/metrics` | Prometheus metrics |

### Web GUI

React-based administration interface (not optional — required for pipeline management):

- **Dashboard** — system overview, active pipelines, resource utilization
- **Pipeline Builder** — visual DAG editor, step configuration, condition editing
- **Pipeline Monitor** — real-time step progress, artifact inspection, log streaming
- **Template Manager** — browse, clone, edit, version pipeline templates
- **Queue Management** — create, pause, resume, delete queues
- **LLM Chat** — natural language pipeline creation and management
- **Log Viewer** — search and filter execution logs across pipelines
- **Output Browser** — preview and download artifacts (videos, images, files)
- **Resource Monitor** — GPU/CPU utilization, lock status, worker activity

## Data Flow

### Pipeline Execution Flow

```
1. User describes workflow (LLM chat, API, or GUI)
2. LLM compiler generates Pipeline with Steps, conditions, and dependencies
   (or user clones an existing template with new inputs)
3. Pipeline saved to DB with status "pending"
4. Pipeline engine resolves DAG, finds steps with no unmet dependencies
5. For each ready step:
   a. Evaluate condition → skip step if false
   b. If fan-out: create N StepExecutions from the artifact list
   c. Submit step executions to queue as individual tasks
   d. Queue manager dispatches to workers when resources available
6. Executor runs the command (ffmpeg/python/shell)
7. On completion: store artifacts, advance DAG
8. On fan-in: wait for all upstream branches, aggregate artifacts
9. On failure: retry if retryable, else mark step failed/interrupted
10. On interruption (no result): pause pipeline, await user action
11. On resume: reload cached artifacts, continue from interruption point
12. Pipeline completes when all steps are completed or skipped
```

### Resource Locking Flow

```
1. Step execution requires resource (e.g., GPU cuda:0)
2. Dispatcher queries ResourceManager.acquire(resource_id, task_id)
3. ResourceManager checks lock state (Redis or local)
4. If locked → task remains in queue, retried on next dispatch cycle
5. If available → lock acquired, resource assigned, task executes, lock released on completion
```

## Database Schema

Seven core tables (SQLite or PostgreSQL via SQLAlchemy 2.0):

- **pipeline_templates** - `id` (UUID PK), `name`, `description`, `steps_definition` (JSON), `version`, `created_by`, `created_at`, `updated_at`.
- **pipelines** - `id` (UUID PK), `template_id` (FK->pipeline_templates, nullable), `name`, `status`, `inputs` (JSON), `context` (JSON), `created_at`, `resumed_at`. Indexed on `status`.
- **pipeline_steps** - `id` (UUID PK), `pipeline_id` (FK->pipelines), `order`, `name`, `command_type`, `command_template`, `condition`, `inputs` (JSON), `fan_out_on`, `depends_on` (JSON), `status`, `resource_requirements` (JSON). Indexed on `(pipeline_id, order)`.
- **step_executions** - `id` (UUID PK), `step_id` (FK->pipeline_steps), `index` (nullable, fan-out index), `status`, `started_at`, `completed_at`, `result` (JSON), `error`. Indexed on `(step_id, status)`.
- **artifacts** - `id` (UUID PK), `pipeline_id` (FK->pipelines), `step_id` (FK->pipeline_steps), `execution_id` (FK->step_executions, nullable), `key`, `value` (JSON), `file_path` (nullable), `created_at`. Indexed on `(pipeline_id, step_id, key)`.
- **queues** - `name` (PK), `type`, `workers`, `priority`, `status`, `config` (JSON).
- **execution_logs** - `id` (auto PK), `pipeline_id` (FK, nullable), `step_id` (FK, nullable), `task_id`, `timestamp`, `level`, `message`, `context` (JSON). Indexed on `(pipeline_id, timestamp)` and `(step_id, timestamp)`.

## Configuration

Configuration is done via YAML files (see `configs/` directory). Validated with Pydantic models.

| Section | Purpose | Key Constraints |
|---------|---------|----------------|
| `agent` | Name, worker count, log level | workers: 1-32, log_level: DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `database` | Connection URL, pool size | pool_size: 1-100 |
| `redis` | Connection URL, enabled flag | Optional for distributed locking |
| `resources` | GPU/CPU/custom definitions | type: gpu/cpu/custom, exclusive: bool, max_concurrent: int |
| `queues` | Queue definitions | type: fifo/priority/parallel, names must be unique |
| `programs` | Python scripts to execute | id unique, path required, optional venv/timeout/retry |
| `triggers` | Cron schedules, file watchers, API, LLM | type: schedule/file_watcher/api/llm |
| `monitoring` | Metrics, logging, alerts | format: json/text, alert types: email/webhook/slack |
| `api` | Host, port, CORS, auth | port: 1-65535 |
| `llm` | Provider, model, API key | provider: openai/anthropic |

Environment variables are supported via `${VAR_NAME}` syntax in YAML config files.

**Config files:**
- `configs/default.yaml` - Production defaults
- `configs/development.yaml` - Dev mode (debug logging, auto-reload)
- `.env.template` - Environment variable template (copy to `.env`)

## Key Entry Points

- **CLI:** `src/agent/cli.py` - Command-line interface (`autocut-agent start ...`)
- **Orchestrator:** `src/agent/core/orchestrator.py` - `AgentOrchestrator` class
- **Config:** `src/agent/core/config.py` - `load_config()`, `AgentConfig` Pydantic model
- **Pipeline Engine:** `src/agent/pipeline/engine.py` - `PipelineEngine` DAG execution
- **Pipeline Compiler:** `src/agent/pipeline/compiler.py` - LLM natural language → Pipeline
- **Pipeline Templates:** `src/agent/pipeline/templates.py` - Template CRUD and cloning
- **Pipeline Models:** `src/agent/pipeline/models.py` - Pipeline, Step, Execution, Artifact
- **Queue Manager:** `src/agent/queue/manager.py` - `QueueManager` + `TaskDispatcher`
- **Resource Manager:** `src/agent/resources/manager.py` - `ResourceManager`
- **Locks:** `src/agent/resources/locks.py` - `RedisLockManager`
- **Executor:** `src/agent/executor/runner.py` - `ProgramExecutor` (python, ffmpeg, shell)
- **Monitoring:** `src/agent/monitoring/logger.py` - structlog setup
- **API App:** `src/agent/api/main.py` - FastAPI app with routers at `/api/v1/`
- **API Routes:** `src/agent/api/routes/` - Endpoint modules (pipelines, templates, queues, tasks, status)
- **Web GUI:** `http://localhost:8080` when running

## Implementation Phases

Implementation should follow this priority order:

| Phase | Focus | Key Files |
|-------|-------|-----------|
| 1 | **Core + Pipeline Data Model** | `core/config.py`, `core/state.py`, `pipeline/models.py`, `pipeline/conditions.py` |
| 2 | **LLM Pipeline Compiler** | `pipeline/compiler.py`, `triggers/llm.py` |
| 3 | **Pipeline Engine (DAG execution)** | `pipeline/engine.py` — step ordering, fan-out/fan-in, resume |
| 4 | **Executor (multi-type)** | `executor/runner.py` (python + ffmpeg + shell), `executor/capture.py` |
| 5 | **Resource Management** | `resources/gpu.py`, `resources/locks.py`, `resources/manager.py` |
| 6 | **Queue System** | `queue/models.py`, `queue/manager.py`, `queue/worker.py` |
| 7 | **Pipeline Templates** | `pipeline/templates.py` — save, clone, version |
| 8 | **API** | `api/main.py`, `api/routes/*.py`, `api/auth.py` |
| 9 | **Monitoring** | `monitoring/logger.py`, `monitoring/metrics.py`, `monitoring/alerts.py` |
| 10 | **Triggers** | `triggers/scheduler.py`, `triggers/watcher.py`, `triggers/api.py` |
| 11 | **Web GUI** | `gui/` — pipeline builder, monitor, template manager, LLM chat, output browser |

## Deployment Models

**Single Node:** One process with SQLite + optional Redis. All components in one `AgentOrchestrator`.

**Distributed:** Nginx load balancer -> multiple API nodes -> multiple Agent nodes (each with own Orchestrator/Queues/Executors) -> PostgreSQL primary + Redis cluster. Requires Redis for distributed locking.

**Docker Compose:** Full stack with agent + PostgreSQL + Redis via `docker-compose.yml`. Multi-stage `Dockerfile` with `scripts/docker-entrypoint.sh`.

## Development Commands

### Setup

```bash
# Linux/macOS
bash scripts/setup.sh

# Windows
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

# Activate venv
source .venv/bin/activate       # Linux/macOS
.venv\Scripts\activate          # Windows

# Copy env template
cp .env.template .env
```

### Package Management

```bash
poetry install                 # install deps
poetry install --with dev      # include dev deps
poetry shell                   # activate venv
pip install -r requirements.txt && pip install -e .  # pip alternative
```

### Running the Agent

```bash
autocut-agent start                                # default config
autocut-agent start --config configs/default.yaml  # custom config
autocut-agent start --dev                          # dev mode (auto-reload)
autocut-agent start --log-level DEBUG              # verbose logging

# Or run API directly with uvicorn
uvicorn agent.api.main:app --reload --host 0.0.0.0 --port 8080
```

### Testing

```bash
pytest                                   # run all tests
pytest tests/unit/                       # unit tests only
pytest tests/integration/                # integration tests only
pytest tests/e2e/                        # end-to-end tests only
pytest tests/unit/test_pipeline_engine.py  # specific test file
pytest -v -s                             # verbose with stdout
pytest --lf                              # rerun failed tests
pytest --cov                             # with coverage
pytest --cov --cov-report=html           # HTML coverage report
```

### Code Quality

```bash
ruff check .        # linting
black .             # formatting
mypy src/           # type checking
pre-commit install  # set up pre-commit hooks
```

### Docker

```bash
docker build -t autocut-agent .
docker-compose up -d       # start all services (agent + PostgreSQL + Redis)
docker-compose logs -f agent  # view agent logs
docker-compose down        # stop services
```

## Coding Conventions

- **Formatter:** Black (line length default: 88)
- **Linter:** Ruff
- **Type checker:** mypy
- **Test framework:** pytest with pytest-cov
- **ORM:** SQLAlchemy 2.0 with async support
- **Validation:** Pydantic `BaseModel` with validators for all config and API models
- **Logging:** structlog with structured JSON output and context binding
- **Pre-commit hooks:** configured via pre-commit
- **Python version:** 3.10+ (use modern syntax: `match/case`, `X | Y` union types, etc.)
- **Async:** The orchestrator, pipeline engine, executor, resource manager, and API all use `async/await`
- **Imports:** Use `from agent.pipeline.engine import PipelineEngine` style
- **Tests:** Organize into `tests/unit/`, `tests/integration/`, `tests/e2e/`

## Security

- **Authentication:** JWT tokens, API keys, OAuth2 integration
- **Authorization:** RBAC with queue-level and pipeline-level permissions
- **Data:** TLS/SSL connections, secrets via env vars (never hardcoded), DB encryption at rest
- **Process isolation:** Subprocess sandboxing, resource limits (CPU/memory/disk), network isolation
- **Audit:** All operations logged with correlation IDs

## Environment Variables

Defined in `.env.template`, copy to `.env` for local use:

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI LLM provider key |
| `ANTHROPIC_API_KEY` | Anthropic LLM provider key |
| `API_SECRET_KEY` | REST API authentication |
| `SMTP_PASSWORD` | Email alert SMTP password |

## Git Workflow

- Feature branches: `feature/<descriptive-name>`
- Commit messages: imperative mood, e.g., "Add pipeline engine DAG execution"
- Run `pytest`, `ruff check .`, `black .`, and `mypy src/` before committing
- Pre-commit hooks enforce code quality checks automatically

## Key Documentation References

| Document | Purpose | Size |
|----------|---------|------|
| `specifications-v1.md` | Complete project specs, requirements, use cases, phases | 30KB |
| `AI-AGENTS-GUIDE.md` | AI assistant patterns, best practices, testing strategies | 30KB |
| `docs/ARCHITECTURE.md` | System architecture, data flow, DB schema, deployment | 17KB |
| `README.md` | Overview, quick start, installation, API examples | 12KB |
| `CONTRIBUTING.md` | Contribution guidelines, code style, PR process | 8KB |
| `.github/copilot-instructions.md` | GitHub Copilot-specific instructions | 9KB |
| `.cursorrules` | Cursor AI rules | 3KB |
| `.aider.conf.yml` | Aider configuration | - |

## Important Notes

- This project is in bootstrap phase; source modules are not yet implemented
- **The pipeline is the core abstraction, not flat tasks** — all orchestration flows through Pipeline → Steps → Executions
- The LLM is the primary interface for creating pipelines from natural language (compile-then-execute hybrid)
- Pipeline templates enable reuse: save a generic workflow, clone it with new inputs
- Pipelines are resumable: interrupted pipelines skip completed steps and reuse cached artifacts
- Steps with no result interrupt the pipeline; users can skip or retry via GUI/API
- The executor supports multiple command types: Python, ffmpeg, and shell
- GPU resources use exclusive locking — tasks requiring the same GPU are serialized
- Redis is optional; without it, resource locking is process-local only (single-node mode)
- The Web GUI is required (not optional) for pipeline management, monitoring, and LLM chat
- All network I/O is async; use `async def` for new API endpoints and service methods
- Database uses SQLAlchemy 2.0 async; 7 core tables with JSON columns for flexible storage
- Tests are split into unit, integration, and e2e directories
