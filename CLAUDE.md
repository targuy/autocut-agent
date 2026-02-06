# CLAUDE.md - AI Assistant Guide for AutoCut-Agent

## Project Overview

AutoCut-Agent is an intelligent task orchestration system for executing Python programs. It supports multi-trigger execution (schedule, event, API, GUI, LLM), intelligent queue management with resource locking, and a web-based administration interface.

**Tech stack:** Python 3.10+, FastAPI, LangChain, Redis (optional), SQLite/PostgreSQL, Docker

**License:** MIT

## Repository Structure

```
autocut-agent/
├── src/agent/              # Main source code
│   ├── core/               # Core orchestrator and config loading
│   ├── triggers/           # Trigger systems (scheduler, file watcher, API, LLM)
│   ├── queue/              # Queue management (priority, FIFO)
│   ├── resources/          # Resource management (GPU/CPU locking)
│   ├── executor/           # Program execution engine
│   ├── monitoring/         # Logging, metrics, alerting
│   ├── api/                # REST API endpoints (FastAPI)
│   └── gui/                # Web frontend
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

The system follows a layered orchestration pattern:

```
Trigger Layer (Scheduler | Events | API | LLM)
        |
  Agent Core / Orchestrator
        |
    Queue Manager
        |
   Resource Manager
        |
    Executor Pool
        |
 Monitoring & Logging
```

Key architectural concepts:
- **Triggers** initiate task execution via schedule (cron), file events, REST API, or LLM chat
- **Queue Manager** handles priority and FIFO queues with configurable workers
- **Resource Manager** provides exclusive GPU/CUDA locking and CPU concurrency control
- **Executor Pool** runs Python programs with timeout, retry, and virtual environment support
- **Monitoring** provides Prometheus metrics, JSON logging, and email alerting

## Development Commands

### Package Management

```bash
# Install with Poetry (recommended)
poetry install
poetry install --with dev    # include dev dependencies
poetry shell                 # activate venv

# Install with pip
pip install -r requirements.txt
pip install -e .             # editable install
```

### Running the Agent

```bash
autocut-agent start                          # default config
autocut-agent start --config configs/default.yaml  # custom config
autocut-agent start --dev                    # dev mode (auto-reload)
autocut-agent start --log-level DEBUG        # verbose logging
```

### Testing

```bash
pytest                                # run all tests
pytest tests/unit/test_queue_manager.py  # specific test file
pytest -v -s                          # verbose with stdout
pytest --lf                           # rerun failed tests
pytest --cov                          # with coverage
pytest --cov --cov-report=html        # HTML coverage report
```

### Code Quality

```bash
ruff check .     # linting
black .          # formatting
mypy src/        # type checking
pre-commit install  # set up pre-commit hooks
```

### Docker

```bash
docker build -t autocut-agent .
docker-compose up -d          # start all services
docker-compose logs -f        # view logs
docker-compose down           # stop services
```

## Configuration

Configuration is done via YAML files (see `configs/` directory). Key sections:

| Section      | Purpose                                      |
|-------------|----------------------------------------------|
| `agent`     | Name, worker count, log level                |
| `database`  | SQLite or PostgreSQL connection URL           |
| `redis`     | Optional distributed locking                  |
| `resources` | GPU/CPU definitions with concurrency limits   |
| `queues`    | Queue definitions with type and worker count  |
| `programs`  | Python scripts to execute, with timeout/retry |
| `triggers`  | Cron schedules, file watchers, API settings   |
| `monitoring`| Metrics, logging, email alerts                |
| `api`       | Host, port, CORS, secret key                  |
| `llm`       | Provider (openai/anthropic), model, API key   |

Environment variables are supported via `${VAR_NAME}` syntax in YAML config files.

## Key Entry Points

- **Orchestrator:** `src/agent/core/orchestrator.py` - `AgentOrchestrator` class
- **Config loading:** `src/agent/core/config.py` - `load_config()` function
- **REST API:** `src/agent/api/` - FastAPI endpoints at `/api/v1/`
- **Web GUI:** `http://localhost:8080` when running

## API Endpoints

Base URL: `http://localhost:8080/api/v1/`

| Method | Endpoint                          | Description          |
|--------|-----------------------------------|----------------------|
| POST   | `/queues`                         | Create a queue       |
| GET    | `/queues`                         | List queues          |
| POST   | `/queues/{name}/pause`            | Pause a queue        |
| POST   | `/tasks`                          | Submit a task        |
| GET    | `/tasks/{task_id}`                | Get task status      |

## Coding Conventions

- **Formatter:** Black (line length default: 88)
- **Linter:** Ruff
- **Type checker:** mypy
- **Test framework:** pytest with pytest-cov
- **Pre-commit hooks:** configured via pre-commit
- **Python version:** 3.10+ (use modern syntax: `match/case`, `X | Y` union types, etc.)
- **Async:** The orchestrator and API use `async/await` patterns
- **Imports:** Use `from agent.core.orchestrator import AgentOrchestrator` style

## Environment Variables

| Variable          | Purpose                    |
|-------------------|----------------------------|
| `OPENAI_API_KEY`  | OpenAI LLM provider key    |
| `ANTHROPIC_API_KEY` | Anthropic LLM provider key |
| `API_SECRET_KEY`  | REST API authentication    |
| `SMTP_PASSWORD`   | Email alert SMTP password  |

## Git Workflow

- Feature branches: `feature/<descriptive-name>`
- Commit messages: imperative mood, e.g., "Add queue management endpoint"
- Run `pytest`, `ruff check .`, `black .`, and `mypy src/` before committing
- Pre-commit hooks enforce code quality checks automatically

## Important Notes

- This project is in early development; the README describes the target architecture
- GPU resources use exclusive locking - tasks requiring the same GPU are serialized
- Redis is optional; without it, resource locking is process-local only
- The LLM integration supports both OpenAI and Anthropic providers via LangChain
- Configuration supports environment variable substitution for secrets
