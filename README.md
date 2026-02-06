# AutoCut-Agent

> Pipeline-oriented task orchestration system for executing multi-step workflows on media files and Python programs, with LLM-powered natural language pipeline creation.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

## Overview

AutoCut-Agent lets you describe complex processing workflows in natural language. The LLM compiles your description into an executable pipeline — a directed acyclic graph (DAG) of steps with dependency tracking, fan-out/fan-in parallelism, conditional branching, and full resumability.

### Example

> "Create a queue of videos editing that first change if needed the resolution of the video into 720p using ffmpeg, then find the cuts in the converted video using ffmpeg and remember the timecodes of each clip, then launch facedetection.py on each clip and filter by criteria, then assemble the final video with ffmpeg using the remaining clip timecodes."

This is compiled into:

```
Pipeline: "Video editing workflow"
├── Step 1: ffmpeg resize (conditional: if resolution != 720p)
├── Step 2: ffmpeg scene detect → [timecodes]
├── Step 3: facedetection.py (fan-out on each clip) → [filtered timecodes]
├── Step 4: other filters (fan-out) → [final timecodes]
└── Step 5: ffmpeg assembly (fan-in) → final video
```

### Key Features

- **LLM Pipeline Compiler** — Describe workflows in natural language, get executable DAGs
- **Pipeline Templates** — Save generic workflows, clone with new inputs, version and edit
- **Resumable Execution** — Interrupted pipelines skip completed steps, reuse cached artifacts
- **Fan-out/Fan-in** — Process N clips in parallel, wait for all to complete, then assemble
- **Conditional Branching** — Steps skipped when conditions are false (compiled, not runtime LLM)
- **Mixed Executors** — Run Python scripts, ffmpeg commands, or shell commands in any step
- **GPU Resource Locking** — Exclusive CUDA device access prevents contention
- **Web GUI** — Visual pipeline builder, real-time monitoring, template manager, LLM chat

## Quick Start

### Prerequisites

- Python 3.10 or higher
- Redis (optional, for distributed locking)

### Installation

```bash
git clone https://github.com/targuy/autocut-agent.git
cd autocut-agent

# Using Poetry (recommended)
poetry install && poetry shell

# Or using pip
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .

# Configure
cp .env.template .env
# Edit .env with your API keys

# Run
autocut-agent start --config configs/default.yaml
```

### Docker

```bash
docker-compose up -d        # Start agent + PostgreSQL + Redis
docker-compose logs -f agent # View logs
```

## Usage

### Create a Pipeline via LLM Chat

Open `http://localhost:8080` and use the LLM chat to describe your workflow in natural language.

### Create a Pipeline via API

```bash
# Compile natural language into a pipeline
curl -X POST http://localhost:8080/api/v1/pipelines/compile \
  -H "Content-Type: application/json" \
  -d '{"description": "Resize video to 720p then detect scenes"}'

# Or clone a template with new inputs
curl -X POST http://localhost:8080/api/v1/templates/{id}/clone \
  -H "Content-Type: application/json" \
  -d '{"inputs": {"video_path": "/videos/input.mp4"}}'

# Check pipeline status
curl http://localhost:8080/api/v1/pipelines/{id}

# Resume an interrupted pipeline
curl -X POST http://localhost:8080/api/v1/pipelines/{id}/resume
```

## Architecture

```
LLM Pipeline Compiler (natural language → Pipeline DAG)
        |
Trigger Layer (Scheduler | File Watcher | API | GUI)
        |
  Pipeline Engine (DAG execution, fan-out/fan-in, conditions, resume)
        |
    Queue Manager → Resource Manager → Executor Pool
        |
 Monitoring & Logging → API Layer → Web GUI
```

See [CLAUDE.md](CLAUDE.md) for comprehensive architecture documentation.

## Development

```bash
poetry install --with dev      # Install dev dependencies
pytest                         # Run tests
ruff check .                   # Lint
black .                        # Format
mypy src/                      # Type check
```

## License

MIT License — see [LICENSE](LICENSE) for details.
