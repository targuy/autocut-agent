# AutoCut-Agent

> Intelligent task orchestration system for executing Python programs with multi-trigger support, resource management, and LLM integration

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

## Overview

AutoCut-Agent is a sophisticated Python-based agent system designed to orchestrate and execute Python programs through multiple trigger mechanisms. It provides intelligent queue management with resource locking, comprehensive monitoring, and an intuitive web-based administration interface.

### Key Features

- 🚀 **Multi-Trigger Execution**: Schedule-based, event-driven, API-initiated, GUI-controlled, or LLM-commanded
- ⚡ **Intelligent Resource Management**: GPU/CUDA resource locking with parallel queue execution
- 📊 **Comprehensive Monitoring**: Real-time status tracking, logging, alerting, and reporting
- 🎨 **Web GUI**: Visual administration interface for configuration, queue management, and output browsing
- 🤖 **LLM Integration**: Natural language queue management via chat interface
- 🔒 **Resource Coordination**: Handle exclusive resource access with queue synchronization
- 🌐 **Cross-Platform**: Windows, Linux, macOS, and containerized deployment

## Quick Start

### Prerequisites

- Python 3.10 or higher
- Redis (optional, for distributed locking)
- Docker (optional, for containerized deployment)

### Installation

#### Using Poetry (Recommended)

```bash
# Clone the repository
git clone https://github.com/targuy/autocut-agent.git
cd autocut-agent

# Install dependencies with Poetry
poetry install

# Activate virtual environment
poetry shell

# Run the agent
autocut-agent start --config configs/default.yaml
```

#### Using pip

```bash
# Clone the repository
git clone https://github.com/targuy/autocut-agent.git
cd autocut-agent

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install in development mode
pip install -e .

# Run the agent
autocut-agent start --config configs/default.yaml
```

#### Using Docker

```bash
# Build the image
docker build -t autocut-agent .

# Run the container
docker run -d \
  --name autocut-agent \
  -p 8080:8080 \
  -v $(pwd)/configs:/app/configs \
  -v $(pwd)/data:/app/data \
  --gpus all \
  autocut-agent
```

#### Using Docker Compose

```bash
# Start all services (agent, redis, database)
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## Configuration

Create a configuration file (e.g., `config.yaml`):

```yaml
# Agent Configuration
agent:
  name: "AutoCut Production Agent"
  workers: 4
  log_level: INFO

# Database Configuration
database:
  url: "sqlite:///agent.db"
  # url: "postgresql://user:pass@localhost:5432/autocut"

# Redis Configuration (optional, for distributed locking)
redis:
  url: "redis://localhost:6379/0"
  enabled: true

# Resource Configuration
resources:
  gpu:
    - id: cuda:0
      exclusive: true
      max_concurrent: 1
    - id: cuda:1
      exclusive: true
      max_concurrent: 1
  cpu:
    - id: cpu
      exclusive: false
      max_concurrent: 10

# Queue Configuration
queues:
  - name: video_processing
    type: priority
    workers: 2
    resource_requirements:
      - gpu: cuda:0

  - name: data_processing
    type: fifo
    workers: 4

# Program Configuration
programs:
  - id: video_analyzer
    path: /path/to/video_script.py
    venv: /path/to/venv  # Optional
    timeout: 3600
    retry_attempts: 3

# Trigger Configuration
triggers:
  # Schedule-based trigger
  - type: schedule
    cron: "0 2 * * *"  # 2 AM daily
    program: video_analyzer
    queue: video_processing

  # File watcher trigger
  - type: file_watcher
    path: /uploads
    pattern: "*.mp4"
    program: video_analyzer
    queue: video_processing
    
  # API trigger (always enabled)
  - type: api
    enabled: true

# Monitoring Configuration
monitoring:
  metrics:
    enabled: true
    prometheus_port: 9090
  
  logging:
    format: json
    level: INFO
    file: logs/agent.log
  
  alerts:
    - type: email
      smtp_server: smtp.example.com
      smtp_port: 587
      username: alerts@example.com
      password: ${SMTP_PASSWORD}  # From environment
      recipients:
        - admin@example.com
      on_events:
        - error
        - completion

# API Configuration
api:
  host: 0.0.0.0
  port: 8080
  secret_key: ${API_SECRET_KEY}  # From environment
  cors_origins:
    - http://localhost:3000
    - https://app.example.com

# LLM Configuration
llm:
  provider: openai  # or anthropic
  model: gpt-4  # or claude-3-opus-20240229
  api_key: ${OPENAI_API_KEY}  # From environment
  temperature: 0.7
```

## Usage

### Starting the Agent

```bash
# Start with default configuration
autocut-agent start

# Start with custom configuration
autocut-agent start --config /path/to/config.yaml

# Start in development mode (auto-reload)
autocut-agent start --dev

# Start with specific log level
autocut-agent start --log-level DEBUG
```

### Web GUI

Open your browser and navigate to: `http://localhost:8080`

The web GUI provides:
- **Dashboard**: Overview of system status and active queues
- **Queue Management**: Create, pause, resume, delete queues
- **Task Monitor**: Real-time task execution status
- **Configuration Editor**: Edit YAML configuration with validation
- **Log Viewer**: Search and filter execution logs
- **Output Browser**: Preview and download program outputs (images, videos, files)
- **LLM Chat**: Natural language queue management

### API Usage

#### Create a Queue

```bash
curl -X POST http://localhost:8080/api/v1/queues \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my_queue",
    "workers": 2,
    "priority": 1
  }'
```

#### Submit a Task

```bash
curl -X POST http://localhost:8080/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "queue_name": "my_queue",
    "program_path": "/path/to/script.py",
    "args": ["--input", "file.mp4"],
    "timeout": 3600
  }'
```

#### Check Task Status

```bash
curl http://localhost:8080/api/v1/tasks/{task_id}
```

#### List Queues

```bash
curl http://localhost:8080/api/v1/queues
```

#### Pause a Queue

```bash
curl -X POST http://localhost:8080/api/v1/queues/{queue_name}/pause
```

### LLM Chat Examples

Open the chat interface in the web GUI and try:

- "What's the status of the video processing queue?"
- "Pause all queues"
- "Run the video analyzer on /uploads/video.mp4"
- "Show me the last 5 completed tasks"
- "Create a new queue called 'batch_processing' with 4 workers"
- "What GPU resources are available?"

### Python API

```python
from agent.core.orchestrator import AgentOrchestrator
from agent.core.config import load_config

# Load configuration
config = load_config("config.yaml")

# Create orchestrator
orchestrator = AgentOrchestrator(config)

# Start the agent
await orchestrator.start()

# Submit a task
task_id = await orchestrator.submit_task(
    queue_name="video_processing",
    program_path="/path/to/script.py",
    args=["--input", "video.mp4"]
)

# Check status
status = await orchestrator.get_task_status(task_id)
print(f"Task status: {status}")

# Stop the agent
await orchestrator.stop()
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Trigger Layer                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │Scheduler │ │  Events  │ │   API    │ │   LLM    │       │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘       │
└───────┼───────────┼──────────────┼────────────┼─────────────┘
        │           │              │            │
        └───────────┴──────────────┴────────────┘
                        │
        ┌───────────────▼───────────────────────────────────┐
        │           Agent Core / Orchestrator               │
        └───────────────┬───────────────────────────────────┘
                        │
        ┌───────────────▼───────────────────────────────────┐
        │            Queue Manager                          │
        └───────────────┬───────────────────────────────────┘
                        │
        ┌───────────────▼───────────────────────────────────┐
        │         Resource Manager                          │
        └───────────────┬───────────────────────────────────┘
                        │
        ┌───────────────▼───────────────────────────────────┐
        │         Executor Pool                             │
        └───────────────┬───────────────────────────────────┘
                        │
        ┌───────────────▼───────────────────────────────────┐
        │      Monitoring & Logging Layer                   │
        └───────────────────────────────────────────────────┘
```

See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture documentation.

## Examples

Check the [examples/](examples/) directory for:

- `simple_task.py` - Basic task execution
- `gpu_task.py` - GPU-locked task example
- `mcp_tool.py` - MCP-compatible tool
- `file_watcher_setup.py` - File monitoring configuration
- `llm_integration.py` - LLM-based control

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System architecture and design
- [API Reference](docs/API.md) - Complete API documentation
- [Configuration Guide](docs/CONFIGURATION.md) - Configuration options
- [Deployment Guide](docs/DEPLOYMENT.md) - Production deployment
- [Development Guide](docs/DEVELOPMENT.md) - Contributing guidelines
- [AI Agents Guide](AI-AGENTS-GUIDE.md) - Guide for AI coding assistants

## Development

### Setup Development Environment

```bash
# Install development dependencies
poetry install --with dev

# Install pre-commit hooks
pre-commit install

# Run tests
pytest

# Run with coverage
pytest --cov

# Lint code
ruff check .

# Format code
black .

# Type check
mypy src/
```

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/unit/test_queue_manager.py

# Run with verbose output
pytest -v -s

# Run only failed tests
pytest --lf

# Run with coverage report
pytest --cov --cov-report=html
```

### Project Structure

```
autocut-agent/
├── src/agent/           # Main source code
│   ├── core/            # Core orchestrator
│   ├── triggers/        # Trigger systems
│   ├── queue/           # Queue management
│   ├── resources/       # Resource management
│   ├── executor/        # Program execution
│   ├── monitoring/      # Logging & metrics
│   ├── api/             # REST API
│   └── gui/             # Web frontend
├── tests/               # Test suite
├── docs/                # Documentation
├── examples/            # Usage examples
├── configs/             # Configuration templates
└── scripts/             # Setup/deployment scripts
```

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Roadmap

- [x] Core orchestration engine
- [x] Multi-trigger support
- [x] Resource management with GPU locking
- [x] Web GUI
- [x] LLM integration
- [ ] Advanced scheduling (dependency graphs)
- [ ] Distributed execution (multi-node)
- [ ] Plugin system
- [ ] Mobile app (iOS/Android)
- [ ] Advanced analytics with ML predictions
- [ ] Multi-tenancy support

## Support

- **Documentation**: [https://autocut-agent.readthedocs.io](https://autocut-agent.readthedocs.io)
- **Issues**: [https://github.com/targuy/autocut-agent/issues](https://github.com/targuy/autocut-agent/issues)
- **Discussions**: [https://github.com/targuy/autocut-agent/discussions](https://github.com/targuy/autocut-agent/discussions)

## Acknowledgments

- FastAPI for the excellent web framework
- LangChain for LLM integration capabilities
- The Python community for amazing libraries

## Authors

- AutoCut Team

---

Made with ❤️ by the AutoCut Team
# autocut-agent
