# CLAUDE.md - AI Assistant Guide for AutoCut-Agent

## Project Overview

AutoCut-Agent is an intelligent task orchestration system for executing Python programs. It supports multi-trigger execution (schedule, event, API, GUI, LLM), intelligent queue management with resource locking, and a web-based administration interface.

**Tech stack:** Python 3.10+, FastAPI, LangChain, APScheduler, Watchdog, structlog, Pydantic, Redis (optional), SQLite/PostgreSQL, Docker

**License:** MIT

## Repository Structure

```
autocut-agent/
├── src/agent/              # Main source code
│   ├── core/               # Core orchestrator, config loading (Pydantic models)
│   │   ├── orchestrator.py # AgentOrchestrator - central coordination class
│   │   └── config.py       # load_config(), AgentConfig Pydantic model
│   ├── triggers/           # Trigger systems
│   │   ├── scheduler.py    # APScheduler-based cron/interval triggers
│   │   ├── file_watcher.py # Watchdog-based file system monitoring
│   │   ├── api_trigger.py  # FastAPI REST trigger endpoints
│   │   └── llm_trigger.py  # LangChain NLP command parsing
│   ├── queue/              # Queue management
│   │   ├── manager.py      # QueueManager + TaskDispatcher
│   │   ├── fifo.py         # FIFO queue implementation
│   │   ├── priority.py     # Priority queue with starvation prevention
│   │   └── parallel.py     # Parallel queue with load balancing
│   ├── resources/          # Resource management
│   │   ├── manager.py      # ResourceManager with lock acquisition/release
│   │   ├── gpu.py          # CUDA/PyTorch GPU detection, VRAM monitoring
│   │   ├── cpu.py          # CPU core allocation, shared concurrency
│   │   └── locks.py        # RedisLockManager (or local fallback)
│   ├── executor/           # Program execution engine
│   │   ├── runner.py       # ProgramExecutor - subprocess management
│   │   ├── venv.py         # Virtual environment activation/isolation
│   │   └── output.py       # Real-time log streaming, output collection
│   ├── monitoring/         # Logging, metrics, alerting
│   │   ├── system.py       # MonitoringSystem (structlog + Prometheus + AlertManager)
│   │   ├── metrics.py      # PrometheusMetrics export
│   │   └── alerts.py       # AlertManager (email, webhook, Slack, Discord)
│   ├── api/                # REST API endpoints (FastAPI)
│   │   ├── app.py          # FastAPI app creation, CORS, router includes
│   │   ├── queues.py       # /api/v1/queues endpoints
│   │   ├── tasks.py        # /api/v1/tasks endpoints
│   │   └── status.py       # /api/v1/status, /health, /metrics endpoints
│   └── gui/                # Web frontend (React, future)
├── tests/                  # Test suite (pytest)
├── docs/                   # Documentation
│   ├── ARCHITECTURE.md     # System architecture and design
│   ├── API.md              # Complete API documentation
│   ├── CONFIGURATION.md    # Configuration options
│   ├── DEPLOYMENT.md       # Production deployment guide
│   └── DEVELOPMENT.md      # Contributing guidelines
├── examples/               # Usage examples
├── configs/                # Configuration templates (YAML)
├── scripts/                # Setup/deployment scripts
├── README.md               # Main project documentation
└── AI-AGENTS-GUIDE.md      # Guide for AI coding assistants
```

## Architecture

The system follows a layered orchestration pattern with 8 distinct layers:

```
Trigger Layer (Scheduler | File Watcher | API | LLM)
        |
  Agent Core / Orchestrator (AgentOrchestrator)
        |
    Queue Manager (FIFO | Priority | Parallel)
        |
   Resource Manager (GPU/CPU/Custom locking)
        |
    Executor Pool (subprocess, venv, output capture)
        |
 Monitoring & Logging (structlog, Prometheus, alerts)
        |
    API Layer (FastAPI REST, CORS, auth)
        |
    Web GUI (React dashboard - future)
```

### Layer 1: Trigger Layer

Four trigger mechanisms initiate task execution:

| Trigger | Library | Key Features |
|---------|---------|-------------|
| **Scheduler** | APScheduler | Cron + interval scheduling, persistent job storage, timezone-aware |
| **File Watcher** | Watchdog | Pattern matching, debouncing, recursive directory monitoring |
| **API** | FastAPI | REST endpoints, auth, rate limiting, webhook support |
| **LLM** | LangChain | NLP intent recognition, parameter extraction, multi-turn conversation |

### Layer 2: Agent Core / Orchestrator

Central coordination class that owns all subsystems:

```python
class AgentOrchestrator:
    def __init__(self, config: AgentConfig):
        self.queue_manager = QueueManager(config.queues)
        self.resource_manager = ResourceManager(config.resources)
        self.trigger_manager = TriggerManager(config.triggers)
        self.executor_pool = ExecutorPool(config.workers)
        self.monitor = MonitoringSystem(config.monitoring)

    async def start(self):  # Initialize resources -> queues -> triggers -> executors
    async def submit_task(self, task: Task):  # Validate -> enqueue -> notify
    async def stop(self):   # Graceful shutdown
```

### Layer 3: Queue Management

Three queue types, managed by `QueueManager` + `TaskDispatcher`:

- **FIFO Queue** - First-in first-out, simple sequential processing
- **Priority Queue** - Weight-based ordering with starvation prevention
- **Parallel Queue** - Concurrent execution respecting resource constraints, load-balanced

`QueueManager.add_task()` pushes to the named queue, then `TaskDispatcher.notify()` triggers worker assignment.

### Layer 4: Resource Management

`ResourceManager` handles exclusive and shared resource allocation via `RedisLockManager` (or local locks fallback):

- **GPU resources** - CUDA/PyTorch detection, exclusive locking (one task per GPU), VRAM monitoring, multi-GPU
- **CPU resources** - Core allocation, shared access with configurable `max_concurrent`
- **Custom resources** - Plugin architecture for license tokens, API quotas, etc.

Key methods: `acquire(resource_id, task_id) -> bool` and `release(resource_id, task_id)`.

### Layer 5: Executor Layer

`ProgramExecutor` runs Python programs in isolated environments:

- **Runner** - Subprocess management, stdout/stderr capture, timeout enforcement, error handling
- **Venv Manager** - Virtual environment activation, dependency isolation, env var injection
- **Output Capture** - Real-time log streaming, structured output parsing, file output collection

### Layer 6: Monitoring Layer

`MonitoringSystem` using structlog + Prometheus + AlertManager:

- **Logging** - Structured JSON via structlog, context binding (task_id, queue_name), log rotation
- **Metrics** - Prometheus export: task count/duration/success rate, resource utilization, queue depth
- **Alerting** - Email, webhook, Slack, Discord channels with throttling and deduplication
- **Tracing** - OpenTelemetry integration, request ID propagation

### Layer 7: API Layer

FastAPI application at `/api/v1/`:

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
| POST | `/tasks` | Submit task |
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

### Layer 8: Web GUI (Future)

React-based: Dashboard, Queue Management, Task Monitor, Config Editor, Log Viewer, Output Browser, LLM Chat.

## Data Flow

### Task Submission Flow

```
1. Trigger detects condition (schedule fires, file appears, API call, LLM command)
2. Trigger creates Task object
3. Task validated against program configuration
4. Task added to appropriate Queue
5. Queue notifies TaskDispatcher
6. Dispatcher checks resource availability via ResourceManager
7. If resources available:
   a. ResourceManager acquires lock (Redis or local)
   b. Task assigned to Worker in ExecutorPool
   c. ProgramExecutor runs program (subprocess)
   d. Output captured and stored
   e. ResourceManager releases lock
   f. Task marked complete
8. MonitoringSystem logs all events at each step
9. AlertManager sends alerts if configured for the event type
```

### Resource Locking Flow

```
1. Task requires resource (e.g., GPU cuda:0)
2. Dispatcher queries ResourceManager.acquire(resource_id, task_id)
3. ResourceManager checks lock state (Redis or local)
4. If locked -> Task remains in queue, retried on next dispatch cycle
5. If available -> Lock acquired, resource assigned, task executes, lock released on completion
```

## Database Schema

Four core tables (SQLite or PostgreSQL):

- **tasks** - `id` (UUID PK), `queue_name`, `program_path`, `status`, `priority`, `created_at`, `started_at`, `completed_at`, `config` (JSON), `result` (JSON), `error`. Indexed on `(queue_name, status)` and `(status, created_at)`.
- **queues** - `name` (PK), `type` (fifo/priority/parallel), `workers`, `priority`, `status`, `config` (JSON).
- **resources** - `id` (PK), `type` (gpu/cpu/custom), `exclusive` (bool), `max_concurrent`, `config` (JSON), `status`. Indexed on `type`.
- **execution_logs** - `id` (auto PK), `task_id` (FK->tasks), `timestamp`, `level`, `message`, `context` (JSON). Indexed on `(task_id, timestamp)` and `level`.

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

Config loading uses Pydantic validation:
```python
def load_config(path: str) -> AgentConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return AgentConfig(**data)  # Pydantic validates all fields
```

## Key Entry Points

- **Orchestrator:** `src/agent/core/orchestrator.py` - `AgentOrchestrator` class
- **Config loading:** `src/agent/core/config.py` - `load_config()` function, `AgentConfig` Pydantic model
- **Queue Manager:** `src/agent/queue/manager.py` - `QueueManager` + `TaskDispatcher`
- **Resource Manager:** `src/agent/resources/manager.py` - `ResourceManager` + `RedisLockManager`
- **Executor:** `src/agent/executor/runner.py` - `ProgramExecutor`
- **Monitoring:** `src/agent/monitoring/system.py` - `MonitoringSystem`
- **REST API:** `src/agent/api/app.py` - FastAPI app with routers at `/api/v1/`
- **Web GUI:** `http://localhost:8080` when running

## Deployment Models

**Single Node:** One process with SQLite + optional Redis. All components in one `AgentOrchestrator`.

**Distributed:** Nginx load balancer -> multiple API nodes -> multiple Agent nodes (each with own Orchestrator/Queues/Executors) -> PostgreSQL primary + Redis cluster. Requires Redis for distributed locking.

## Development Commands

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
```

### Testing

```bash
pytest                                   # run all tests
pytest tests/unit/test_queue_manager.py  # specific test file
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
docker-compose up -d       # start all services
docker-compose logs -f     # view logs
docker-compose down        # stop services
```

## Coding Conventions

- **Formatter:** Black (line length default: 88)
- **Linter:** Ruff
- **Type checker:** mypy
- **Test framework:** pytest with pytest-cov
- **Pre-commit hooks:** configured via pre-commit
- **Python version:** 3.10+ (use modern syntax: `match/case`, `X | Y` union types, etc.)
- **Async:** The orchestrator, executor, resource manager, and API all use `async/await`
- **Config validation:** All configuration uses Pydantic `BaseModel` with validators
- **Logging:** structlog with structured JSON output and context binding
- **Imports:** Use `from agent.core.orchestrator import AgentOrchestrator` style

## Security

- **Authentication:** JWT tokens, API keys, OAuth2 integration
- **Authorization:** RBAC with queue-level and task-level permissions
- **Data:** TLS/SSL connections, secrets via env vars (never hardcoded), DB encryption at rest
- **Process isolation:** Subprocess sandboxing, resource limits (CPU/memory/disk), network isolation
- **Audit:** All operations logged with correlation IDs

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI LLM provider key |
| `ANTHROPIC_API_KEY` | Anthropic LLM provider key |
| `API_SECRET_KEY` | REST API authentication |
| `SMTP_PASSWORD` | Email alert SMTP password |

## Git Workflow

- Feature branches: `feature/<descriptive-name>`
- Commit messages: imperative mood, e.g., "Add queue management endpoint"
- Run `pytest`, `ruff check .`, `black .`, and `mypy src/` before committing
- Pre-commit hooks enforce code quality checks automatically

## Important Notes

- This project is in early development; the README and architecture docs describe the target design
- GPU resources use exclusive locking - tasks requiring the same GPU are serialized via Redis/local locks
- Redis is optional; without it, resource locking is process-local only (single-node mode)
- The LLM integration supports both OpenAI and Anthropic providers via LangChain
- Configuration supports environment variable substitution for secrets
- Queue names must be unique (enforced by Pydantic validator)
- All network I/O is async; use `async def` for new API endpoints and service methods
- Database tables use JSON columns for flexible config/result storage
