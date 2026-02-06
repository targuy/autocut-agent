# AutoCut-Agent Usage Guide

Comprehensive reference for every feature, interface, and configuration option.

**Interfaces:** REST API, Web GUI, CLI, Jupyter Notebook SDK

---

## Table of Contents

- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [CLI](#cli)
- [REST API](#rest-api)
  - [Pipelines](#pipelines-api)
  - [Templates](#templates-api)
  - [Programs (Registry & Scoring)](#programs-api)
  - [Queues](#queues-api)
  - [Tasks](#tasks-api)
  - [Storage](#storage-api)
  - [System](#system-api)
- [Web GUI](#web-gui)
- [Jupyter Notebook SDK](#jupyter-notebook-sdk)
  - [Session](#session)
  - [Registry Helper](#registry-helper)
  - [Scoring Helper](#scoring-helper)
  - [Condition Helper](#condition-helper)
  - [Compiler Helper](#compiler-helper)
  - [Pipeline Helper](#pipeline-helper)
  - [Export Helper](#export-helper)
- [LLM Providers](#llm-providers)
- [Pipeline Concepts](#pipeline-concepts)
- [Program Registry & Scoring](#program-registry--scoring)
- [Media Storage](#media-storage)
- [Deployment](#deployment)
- [Environment Variables](#environment-variables)

---

## Quick Start

```bash
# 1. Install
pip install -e .
cp .env.template .env          # edit with your API keys

# 2. Start the server
autocut-agent start

# 3. Open the GUI
open http://localhost:8080

# 4. Compile your first pipeline (via curl)
curl -X POST http://localhost:8080/api/v1/pipelines/compile \
  -H "Content-Type: application/json" \
  -d '{"description": "Resize video to 720p then detect scene cuts",
       "inputs": {"video_path": "/data/input.mp4"}}'
```

Or use the Jupyter notebook:

```python
from agent.notebook import Session
s = Session()
pipeline, steps = s.compiler.from_steps([
    {"name": "resize", "command_type": "ffmpeg",
     "command_template": "ffmpeg -i {video} -vf scale=1280:720 {out}"},
], inputs={"video": "/data/input.mp4"})
s.pipelines.inspect(pipeline, steps)
```

---

## Installation

### From source (recommended)

```bash
git clone <repository-url>
cd autocut-agent

# Option A: pip
pip install -e .

# Option B: Poetry
poetry install
poetry shell
```

### Dependencies for LLM providers

Install only the providers you need:

```bash
pip install langchain-openai        # OpenAI and LMStudio
pip install langchain-anthropic     # Anthropic Claude
pip install langchain-google-genai  # Google Gemini
```

### For Jupyter notebooks

```bash
pip install jupyter nest_asyncio
```

---

## Configuration

Configuration is loaded from YAML files with `${VAR_NAME}` environment variable substitution.

### File locations

| File | Purpose |
|------|---------|
| `configs/default.yaml` | Production defaults |
| `configs/development.yaml` | Dev mode (debug logging, text format) |
| `.env` | Environment variables (copy from `.env.template`) |

### Full configuration reference

```yaml
# ── Agent ──────────────────────────────────────────────
agent:
  name: "AutoCut Agent"       # Display name
  workers: 4                  # Worker count (1–32)
  log_level: INFO             # DEBUG | INFO | WARNING | ERROR | CRITICAL

# ── Database ───────────────────────────────────────────
database:
  url: "sqlite+aiosqlite:///agent.db"   # SQLAlchemy async URL
  pool_size: 5                          # Connection pool (1–100)
  # PostgreSQL example:
  # url: "postgresql+asyncpg://user:pass@localhost/autocut"

# ── Redis (optional) ──────────────────────────────────
redis:
  url: "redis://localhost:6379/0"
  enabled: false              # Enable for distributed locking

# ── Resources ─────────────────────────────────────────
resources:
  - id: "cpu"
    type: "cpu"               # gpu | cpu | custom
    exclusive: false
    max_concurrent: 10
  - id: "cuda:0"
    type: "gpu"
    exclusive: true           # One task at a time
    max_concurrent: 1

# ── Queues ────────────────────────────────────────────
queues:
  - name: "default"
    type: "priority"          # fifo | priority | parallel
    workers: 2
    priority: 0

# ── Programs (legacy config-based) ───────────────────
programs:
  - id: "facedetection"
    path: "scripts/facedetection.py"
    venv: ".venv"             # Optional virtual environment
    timeout: 3600
    retry_attempts: 0

# ── Triggers ──────────────────────────────────────────
triggers:
  - type: "api"               # schedule | file_watcher | api | llm
    enabled: true
  - type: "schedule"
    enabled: true
    cron: "0 */6 * * *"       # Every 6 hours
    program: "cleanup"
    queue: "default"
  - type: "file_watcher"
    enabled: true
    path: "/data/incoming"
    pattern: "*.mp4"
    program: "ingest"
    queue: "default"

# ── Monitoring ────────────────────────────────────────
monitoring:
  metrics:
    enabled: true
    prometheus_port: 9090
  logging:
    format: "json"            # json | text
    level: "INFO"
    file: "logs/agent.log"    # Optional file output
  alerts:
    - type: "webhook"         # email | webhook | slack | discord
      webhook_url: "https://hooks.slack.com/..."
      on_events: ["pipeline_failed", "step_timeout"]

# ── API ───────────────────────────────────────────────
api:
  host: "0.0.0.0"
  port: 8080
  cors_origins:
    - "http://localhost:3000"
    - "http://localhost:8080"
  secret_key: "${API_SECRET_KEY}"

# ── LLM ───────────────────────────────────────────────
llm:
  provider: "openai"          # openai | anthropic | gemini | lmstudio
  model: "gpt-4"
  api_key: "${OPENAI_API_KEY}"
  base_url: null              # Required for lmstudio
  temperature: 0.3            # 0.0 (deterministic) – 2.0 (creative)
```

---

## CLI

```
autocut-agent [OPTIONS] COMMAND
```

### `start` — Launch the agent

```bash
autocut-agent start [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--config` | path | `configs/default.yaml` | YAML configuration file |
| `--dev` | flag | — | Development mode (auto-reload, debug logging) |
| `--log-level` | choice | from config | Override: DEBUG, INFO, WARNING, ERROR, CRITICAL |

**Examples:**

```bash
# Production
autocut-agent start

# Development with verbose logging
autocut-agent start --dev --log-level DEBUG

# Custom config
autocut-agent start --config configs/production.yaml

# Or run the API directly with uvicorn
uvicorn agent.api.main:app --reload --host 0.0.0.0 --port 8080
```

---

## REST API

Base URL: `http://localhost:8080/api/v1`

Interactive docs: `http://localhost:8080/docs` (Swagger) or `http://localhost:8080/redoc`

### Pipelines API

#### Compile from natural language

```
POST /pipelines/compile
```

The LLM translates a description into an executable pipeline DAG.

```json
{
  "description": "Resize video to 720p, detect scene cuts, run face detection on each clip, assemble final video",
  "inputs": {
    "video_path": "/data/input.mp4",
    "output_dir": "/data/output"
  }
}
```

Response:

```json
{
  "id": "550e8400-...",
  "name": "Video Processing Pipeline",
  "status": "pending",
  "steps": [
    {"order": 0, "name": "resize", "command_type": "ffmpeg", "status": "pending"},
    {"order": 1, "name": "scene_detect", "command_type": "python", "status": "pending"},
    {"order": 2, "name": "face_detection", "command_type": "python", "status": "pending"},
    {"order": 3, "name": "assemble", "command_type": "ffmpeg", "status": "pending"}
  ]
}
```

#### Create from template

```
POST /pipelines
```

```json
{
  "template_id": "550e8400-...",
  "inputs": {"video_path": "/data/other.mp4"},
  "start": true
}
```

#### List pipelines

```
GET /pipelines
```

Returns: `[{id, name, status, created_at}, ...]`

#### Get pipeline details

```
GET /pipelines/{pipeline_id}
```

Returns full pipeline with steps, inputs, context, timestamps.

#### Resume interrupted pipeline

```
POST /pipelines/{pipeline_id}/resume
```

Skips completed steps, reuses cached artifacts, continues from the interruption point.

#### Cancel running pipeline

```
POST /pipelines/{pipeline_id}/cancel
```

#### Get pipeline artifacts

```
GET /pipelines/{pipeline_id}/artifacts
```

Returns: `[{id, step_id, key, value, file_path, created_at}, ...]`

#### Skip a failed step

```
POST /pipelines/{pipeline_id}/steps/{step_id}/skip
```

Marks the step as skipped so downstream steps can proceed.

---

### Templates API

#### Save template

```
POST /templates
```

```json
{
  "name": "Video Editing Workflow",
  "description": "Resize, detect, filter, assemble",
  "steps": [
    {"name": "resize", "command_type": "ffmpeg",
     "command_template": "ffmpeg -i {video} -vf scale=1280:720 {out}"}
  ]
}
```

#### List templates

```
GET /templates
```

#### Get template

```
GET /templates/{template_id}
```

#### Clone template into pipeline

```
POST /templates/{template_id}/clone
```

```json
{
  "inputs": {"video_path": "/data/new_video.mp4"},
  "start": true
}
```

#### Update template

```
PUT /templates/{template_id}
```

Increments the version number automatically.

```json
{"name": "Updated Name", "description": "New description"}
```

#### Delete template

```
DELETE /templates/{template_id}
```

---

### Programs API

The program registry stores knowledge about executables used in pipelines — their commands, parameters, I/O contracts, and execution history.

#### Register program

```
POST /programs
```

```json
{
  "name": "facedetection",
  "description": "Detects faces in video frames",
  "purpose": "Filter clips containing people",
  "command_type": "python",
  "command_template": "python facedetection.py --input {input_path} --threshold {threshold}",
  "required_inputs": ["clip_path"],
  "expected_outputs": ["filtered_clips", "face_count"],
  "tags": ["filter", "face", "gpu"],
  "parameters": [
    {"name": "threshold", "type": "float", "default": 0.5,
     "description": "Confidence threshold", "min_value": 0.0, "max_value": 1.0},
    {"name": "min_size", "type": "int", "default": 30,
     "description": "Minimum face size in pixels", "min_value": 1}
  ]
}
```

**Parameter types:** `string`, `int`, `float`, `bool`, `enum`, `path`

**Parameter constraints:** `min_value`, `max_value`, `allowed_values` (for enum), `required`

#### List programs

```
GET /programs?active_only=true
```

#### Get program details

```
GET /programs/{program_name}
```

#### Update program metadata

```
PUT /programs/{program_name}
```

```json
{"description": "Updated description", "tags": ["face", "gpu", "v2"]}
```

#### Deactivate program (soft delete)

```
DELETE /programs/{program_name}
```

#### Update parameter values

```
PUT /programs/{program_name}/parameters
```

```json
{"parameters": {"threshold": 0.3, "min_size": 50}}
```

Validates against constraints. Returns errors for invalid values.

#### Add parameter

```
POST /programs/{program_name}/parameters
```

```json
{
  "name": "model",
  "type": "enum",
  "default": "yolov8",
  "description": "Detection model",
  "allowed_values": ["yolov5", "yolov8", "retinaface"]
}
```

#### Remove parameter

```
DELETE /programs/{program_name}/parameters/{param_name}
```

#### Get execution statistics

```
GET /programs/{program_name}/stats
```

Response:

```json
{
  "program_name": "facedetection",
  "total_runs": 44,
  "successes": 25,
  "failures": 3,
  "zero_outputs": 14,
  "timeouts": 2,
  "success_rate": 0.568,
  "zero_output_rate": 0.318,
  "failure_rate": 0.068,
  "avg_duration_seconds": 2.3
}
```

#### Per-parameter-set stats

```
GET /programs/{program_name}/param-sets
```

Response:

```json
[
  {"parameters_hash": "abc123", "parameters": {"threshold": 0.3},
   "total_runs": 17, "successes": 15, "success_rate": 0.882},
  {"parameters_hash": "def456", "parameters": {"threshold": 0.9},
   "total_runs": 14, "successes": 2, "success_rate": 0.143}
]
```

#### Get advisories

```
GET /programs/{program_name}/advisories
```

Response:

```json
[
  {"severity": "critical", "title": "Consistently producing zero output",
   "message": "'facedetection' produced zero output in 12/14 runs (86%)...",
   "suggested_changes": {"problematic_parameter_sets": [{"threshold": 0.9}]},
   "confidence": 0.9}
]
```

#### Get all advisories

```
GET /advisories
```

Returns advisories grouped by program name.

#### Inject execution score

```
POST /programs/{program_name}/scores
```

```json
{
  "outcome": "success",
  "parameters_used": {"threshold": 0.3, "min_size": 30},
  "duration_seconds": 2.5,
  "output_size": 500,
  "error_message": ""
}
```

**Outcome values:** `success`, `failure`, `zero_output`, `timeout`, `skipped`

#### Batch inject scores

```
POST /programs/{program_name}/scores/batch
```

```json
[
  {"outcome": "success", "parameters_used": {"threshold": 0.3}, "duration_seconds": 2.0},
  {"outcome": "success", "parameters_used": {"threshold": 0.3}, "duration_seconds": 2.5},
  {"outcome": "zero_output", "parameters_used": {"threshold": 0.9}, "duration_seconds": 3.0}
]
```

#### Clear all scores

```
DELETE /programs/{program_name}/scores
```

#### Export all programs

```
GET /programs/export
```

Returns JSON array of all programs for backup/transfer.

#### Bulk import programs

```
POST /programs/import
```

Body: JSON array of program definitions (same format as register).

---

### Queues API

#### Create queue

```
POST /queues
```

```json
{"name": "gpu_tasks", "type": "priority", "workers": 1, "priority": 10}
```

Queue types: `fifo`, `priority`, `parallel`

#### List queues

```
GET /queues
```

#### Get queue details

```
GET /queues/{name}
```

#### Pause / Resume / Delete

```
POST /queues/{name}/pause
POST /queues/{name}/resume
DELETE /queues/{name}
```

---

### Tasks API

Standalone task submission (outside of pipelines).

#### Submit task

```
POST /tasks
```

```json
{
  "queue_name": "default",
  "command": "python process.py --input /data/file.mp4",
  "command_type": "python",
  "priority": 5,
  "timeout": 3600,
  "resource_requirements": ["gpu:cuda:0"]
}
```

#### List tasks

```
GET /tasks?queue_name=default&status=running
```

#### Get task details

```
GET /tasks/{task_id}
```

#### Cancel task

```
DELETE /tasks/{task_id}
```

---

### Storage API

Managed file storage for pipeline inputs, outputs, and artifacts.

#### Ingest file

```
POST /storage/ingest
```

```json
{
  "path": "/data/incoming/video.mp4",
  "category": "input",
  "pipeline_id": "550e8400-...",
  "tags": ["interview", "2024"],
  "metadata": {"source": "camera_a", "duration": 3600}
}
```

Categories: `input`, `output`, `artifact`, `intermediate`

#### Search files

```
GET /storage/files?query=interview&category=input&pipeline_id=550e...&limit=50
```

Filter by:
- `query` — filename search
- `category` — file category
- `pipeline_id` — associated pipeline
- `mime_type` — MIME type filter
- `tag` — tag match (repeatable)
- `limit` / `offset` — pagination

#### Get file metadata

```
GET /storage/files/{file_id}
```

#### Download file

```
GET /storage/files/{file_id}/download
```

Returns binary file stream.

#### Delete file

```
DELETE /storage/files/{file_id}
```

Removes from both database and disk.

#### Export file

```
POST /storage/files/{file_id}/export
```

```json
{"dest_path": "/data/exports/video.mp4"}
```

#### Storage statistics

```
GET /storage/stats
```

Response:

```json
{
  "total_files": 142,
  "total_size_bytes": 15728640000,
  "by_category": {"input": 30, "output": 50, "artifact": 62},
  "by_type": {".mp4": 80, ".json": 40, ".png": 22}
}
```

#### Pipeline files

```
GET /storage/pipelines/{pipeline_id}/files
```

---

### System API

#### System status

```
GET /status
```

Response:

```json
{
  "status": "running",
  "agent_name": "AutoCut Agent",
  "uptime_seconds": 3600,
  "workers": 4
}
```

#### Health check

```
GET /health
```

#### Prometheus metrics

```
GET /metrics
```

Returns Prometheus text exposition format with pipeline, step, task, and queue metrics.

---

## Web GUI

Access at `http://localhost:8080` after starting the agent.

Dark-themed single-page application with sidebar navigation.

### Dashboard

System overview: health status, pipeline counts (total/running/completed/failed), active advisories. Recent pipelines table with quick links. Advisories summary with severity badges.

### Pipelines

Browse all pipelines with status indicators. Click to view details: step list with status colors, dependency visualization, command templates. Actions: compile new pipeline, resume interrupted, cancel running, skip failed steps.

### Templates

List saved pipeline templates with version numbers. Clone a template with custom inputs to create new pipeline instances. Delete outdated templates.

### Programs

**Three tabs:**

1. **Program list** — All registered programs. Click to view details, edit parameters, view stats.

2. **Register form** — Register new programs with:
   - Name, command type (python/ffmpeg/shell), description, purpose
   - Command template with `{variable}` placeholders
   - Required inputs and expected outputs
   - Tags for search matching
   - Parameters (one per line: `name:type:default:description`)

3. **Advisories** — All active advisories across programs with severity levels and suggested changes.

**Program detail view:**
- Basic info and resolved command
- Execution stats: success/failure/zero-output rates, averages
- Parameter editor: view defaults, set current values, add/remove parameters
- Score injection: manually record outcomes for training the advisory engine
- Clear scores: reset history for fresh training

### Storage

Browse and search managed files. Filter by category (input/output/artifact/intermediate) and text query. Ingest new files into managed storage. Download files. View file metadata (size, MIME type, hash, associated pipeline). Delete files.

### Queues

Create, pause, resume, and delete queues. View queue type (priority/fifo/parallel), worker count, and status.

### LLM Compile

Natural language pipeline creation. Enter a workflow description and input variables (JSON), submit to the LLM compiler, view the compiled pipeline with steps.

### Monitoring

System health, uptime, and state. Raw system status JSON.

### Settings

LLM provider configuration: select provider (OpenAI/Anthropic/Gemini/LMStudio), set model, API key, base URL. Import/export program registry data as JSON.

---

## Jupyter Notebook SDK

Interactive pipeline development in Jupyter notebooks. All changes persist to the database — the GUI, CLI, and API see them immediately.

### Installation

```bash
pip install jupyter nest_asyncio
```

### Session

```python
from agent.notebook import Session

# Default database
s = Session()

# Custom database
s = Session("sqlite+aiosqlite:///my_project.db")

# PostgreSQL
s = Session("postgresql+asyncpg://user:pass@localhost/autocut")

# Access helpers
s.registry     # Program registry operations
s.scoring      # Execution scoring & training
s.conditions   # Condition testing
s.compiler     # Pipeline compilation
s.pipelines    # Pipeline inspection & templates
s.export       # File and Docker export

# Clean up
s.close()
```

---

### Registry Helper

View and edit program definitions. All changes write to the database.

#### List programs

```python
for prog in s.registry.list():
    print(f"{prog.name} [{prog.command_type.value}] tags={prog.tags}")

# Include inactive
s.registry.list(active_only=False)
```

#### Get a program

```python
prog = s.registry.get("facedetection")
if prog:
    print(prog.name, prog.description)
    print(prog.resolve_command())  # command with parameters substituted
```

#### Register a new program

```python
prog = s.registry.register(
    name="scene_detect",
    command_type="python",       # "python" | "ffmpeg" | "shell"
    command_template="python scene_detect.py --input {video_path} --threshold {scene_threshold}",
    description="Detects scene cuts in a video",
    purpose="Split video into clips at scene boundaries",
    required_inputs=["video_path"],
    expected_outputs=["timecodes", "clips"],
    tags=["scene", "detection"],
    parameters=[
        "scene_threshold:float:0.3:Scene change sensitivity (lower = more sensitive)",
        "min_scene_length:int:10:Minimum scene length in frames",
    ],
)
```

**Parameter spec format:** `name:type:default:description`

Types: `string`, `int`, `float`, `bool`, `enum`, `path`

#### Update program metadata

```python
s.registry.update("scene_detect",
    description="Updated description",
    tags=["scene", "detection", "v2"],
    command_template="python scene_detect_v2.py --input {video_path}",
)
```

#### Set parameter value

```python
# Validates against constraints (min/max/allowed_values)
s.registry.set_param("facedetection", "threshold", 0.3)
```

#### Add a parameter

```python
s.registry.add_param("facedetection", "model:enum:yolov8:Detection model")
```

#### Remove a parameter

```python
s.registry.remove_param("facedetection", "model")
```

#### Search by description

```python
matches = s.registry.search("detect faces in video clips")
# Matches programs whose name or tags appear in the description
```

#### Pretty-print

```python
s.registry.show("facedetection")
# Prints: name, type, description, command, inputs, outputs, parameters
```

#### Deactivate (soft delete)

```python
s.registry.deactivate("old_program")
```

---

### Scoring Helper

Inject execution scores to train the advisory engine. View stats and recommendations.

#### Inject a single score

```python
s.scoring.inject(
    "facedetection",
    "success",                              # outcome
    parameters={"threshold": 0.3},          # parameters used
    duration=2.5,                           # seconds
    output_size=500,                        # bytes or item count
    error_message="",                       # empty for success
)
```

**Outcomes:** `"success"`, `"failure"`, `"zero_output"`, `"timeout"`, `"skipped"`

#### Batch inject for training

```python
# Simulate 20 successful runs
s.scoring.inject_batch("facedetection", "success",
    parameters={"threshold": 0.3, "min_size": 30},
    count=20, duration=2.5, output_size=500)

# Simulate 10 zero-output runs with bad parameters
s.scoring.inject_batch("facedetection", "zero_output",
    parameters={"threshold": 0.9, "min_size": 50},
    count=10, duration=3.0)
```

#### View statistics

```python
stats = s.scoring.stats("facedetection")
print(f"Total runs: {stats.total_runs}")
print(f"Success rate: {stats.success_rate:.0%}")
print(f"Zero output rate: {stats.zero_output_rate:.0%}")
print(f"Failure rate: {stats.failure_rate:.0%}")
print(f"Avg duration: {stats.avg_duration_seconds:.1f}s")
```

#### Compare parameter sets

```python
for ps in s.scoring.param_sets("facedetection"):
    print(f"  {ps.parameters} -> {ps.success_rate:.0%} ({ps.total_runs} runs)")
```

#### View advisories

```python
for advisory in s.scoring.advisories("facedetection"):
    print(f"[{advisory.severity.value}] {advisory.title}")
    print(f"  {advisory.message}")
    if advisory.suggested_changes:
        print(f"  Suggested: {advisory.suggested_changes}")
```

**Advisory thresholds:**
- Zero output >= 50% -> WARNING, >= 80% -> CRITICAL
- Failure >= 30% -> WARNING, >= 60% -> CRITICAL
- Timeout >= 20% -> WARNING
- Better param set with >= 30% higher success rate -> INFO suggestion

#### All advisories across programs

```python
all_adv = s.scoring.all_advisories()
for program_name, advisories in all_adv.items():
    for a in advisories:
        print(f"  [{a.severity.value}] {program_name}: {a.title}")
```

#### Pretty-print everything

```python
s.scoring.show("facedetection")
# Prints: stats, parameter set breakdown, advisories
```

#### Clear scores (start fresh)

```python
deleted = s.scoring.clear("facedetection")
print(f"Cleared {deleted} scores")
```

---

### Condition Helper

Test pipeline step conditions against real data before deploying.

#### Supported syntax

| Syntax | Example | Description |
|--------|---------|-------------|
| Comparison | `resolution != 720` | Compare values |
| Greater/less | `duration > 60`, `fps <= 30` | Numeric comparison |
| Existence | `has(timecodes)` | True if key exists and is not None |
| Negation | `not has(error)` | Negate any expression |
| And | `has(clips) and duration > 0` | Both must be true |
| Or | `face_count > 0 or duration < 30` | Either can be true |
| Boolean | `true`, `false` | Literal values |

#### Test a condition

```python
result = s.conditions.test("resolution != 720", {"resolution": 1080})
# True -> step would EXECUTE

result = s.conditions.test("resolution != 720", {"resolution": 720})
# False -> step would be SKIPPED
```

#### Test multiple conditions

```python
results = s.conditions.test_batch(
    ["resolution != 720", "has(timecodes)", "duration > 60"],
    {"resolution": 1080, "timecodes": [1.5, 3.2], "duration": 45},
)
for expr, result in results:
    action = "EXECUTE" if result else "SKIP"
    print(f"  {action:7s}  {expr}")
```

Output:

```
  EXECUTE  resolution != 720
  EXECUTE  has(timecodes)
  SKIP     duration > 60
```

#### Explain a condition

```python
s.conditions.explain("resolution != 720", {"resolution": 720})
```

Output:

```
Condition: 'resolution != 720'
Context:   {'resolution': 720}
Result:    False
Action:    Step would SKIP (condition is False)
Referenced keys: ['resolution']
  resolution = 720
```

#### Scenario matrix testing

```python
condition = "resolution != 720"
scenarios = [
    {"name": "4K",    "ctx": {"resolution": 2160}, "expected": True},
    {"name": "1080p", "ctx": {"resolution": 1080}, "expected": True},
    {"name": "720p",  "ctx": {"resolution": 720},  "expected": False},
]

for sc in scenarios:
    actual = s.conditions.test(condition, sc["ctx"])
    status = "OK" if actual == sc["expected"] else "FAIL"
    print(f"  {status}  {sc['name']}: expected={sc['expected']} got={actual}")
```

---

### Compiler Helper

Build pipelines from step definitions or compile from natural language.

#### Build pipeline from steps

```python
pipeline, steps = s.compiler.from_steps(
    [
        {
            "name": "resize",
            "command_type": "ffmpeg",
            "command_template": "ffmpeg -i {video_path} -vf scale=1280:720 {output_dir}/resized.mp4",
            "condition": "resolution != 720",
        },
        {
            "name": "scene_detect",
            "command_type": "python",
            "command_template": "python scene_detect.py --input {output_dir}/resized.mp4",
            "depends_on_names": ["resize"],
        },
        {
            "name": "face_detection",
            "command_type": "python",
            "command_template": "python facedetection.py --input {clip} --threshold 0.3",
            "depends_on_names": ["scene_detect"],
            "fan_out_on": "clips",
        },
        {
            "name": "assemble",
            "command_type": "ffmpeg",
            "command_template": "ffmpeg -f concat -i {clip_list} -c copy {output_dir}/final.mp4",
            "depends_on_names": ["face_detection"],
            "condition": "has(clips) and not has(error)",
        },
    ],
    name="Video Editing Workflow",
    inputs={"video_path": "/data/input.mp4", "output_dir": "/data/output"},
)
```

**Step definition fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | yes | Unique step name |
| `command_type` | str | yes | `"python"`, `"ffmpeg"`, or `"shell"` |
| `command_template` | str | yes | Command with `{variable}` placeholders |
| `condition` | str | no | Skip step if condition evaluates to False |
| `depends_on_names` | list[str] | no | Step names this depends on |
| `fan_out_on` | str | no | Artifact key to parallelize over |
| `input_mappings` | dict | no | Map local names to upstream artifacts |
| `resource_requirements` | list[str] | no | e.g. `["gpu:cuda:0"]` |
| `timeout` | int | no | Seconds (default: 3600) |
| `retry_max` | int | no | Retry attempts (default: 0) |

#### Compile from natural language

Requires an LLM provider configured:

```python
result = s.compiler.compile(
    "Resize video to 720p, detect scene cuts, run face detection on each clip, assemble final video",
    inputs={"video_path": "/data/input.mp4"},
    provider="openai",
    model="gpt-4",
    api_key="sk-...",
    temperature=0.3,
)

if result.is_complete:
    s.pipelines.inspect(result.pipeline, result.steps)
else:
    print("Unknown programs need registration:")
    for name, questions in result.pending_questions.items():
        for q in questions:
            print(f"  {name}: {q.question}")
```

#### Preview LLM prompt

See what context the compiler sends to the LLM, without making an API call:

```python
prompt = s.compiler.preview_prompt(
    "Resize video then detect faces",
    inputs={"video": "/data/input.mp4"},
)
print(prompt)
```

The prompt includes all matching registry programs with their parameters, I/O contracts, and usage context.

---

### Pipeline Helper

Inspect, edit, and manage pipelines and templates.

#### Inspect a pipeline

```python
s.pipelines.inspect(pipeline, steps)
```

Output:

```
Pipeline: Video Editing Workflow
  ID:       550e8400-...
  Status:   pending
  Inputs:   {'video_path': '/data/input.mp4'}
  Steps (4):
    0. resize [ffmpeg] — pending [if resolution != 720]
       cmd: ffmpeg -i {video_path} -vf scale=1280:720 {output_dir}/resized.mp4
    1. scene_detect [python] — pending (depends: 1)
       cmd: python scene_detect.py --input {output_dir}/resized.mp4
    2. face_detection [python] — pending (depends: 1) (fan-out: clips)
       cmd: python facedetection.py --input {clip} --threshold 0.3
    3. assemble [ffmpeg] — pending (depends: 1) [if has(clips) and not has(error)]
       cmd: ffmpeg -f concat -i {clip_list} -c copy {output_dir}/final.mp4
```

#### Inspect a single step with condition evaluation

```python
s.pipelines.inspect_step(steps[0], context={"resolution": 1080})
```

Output:

```
Step: resize
  Order:    0
  Type:     ffmpeg
  Status:   pending
  Command:  ffmpeg -i {video_path} -vf scale=1280:720 {output_dir}/resized.mp4
  Timeout:  3600s
  Retries:  0/0
  Condition: resolution != 720
  Condition result: True -> EXECUTE
```

#### Edit a step

```python
s.pipelines.edit_step(
    steps[2],
    command_template="python facedetection.py --input {clip} --threshold 0.7 --min-size 50",
    timeout=1200,
    condition="has(clips)",
)
```

#### Save as template (persists to DB)

```python
# From a compilation result
template = s.pipelines.save_template(result)

# From pipeline + steps
template = s.pipelines.save_template(
    pipeline=pipeline, steps=steps, name="My Workflow v1"
)
```

#### List templates

```python
for t in s.pipelines.list_templates():
    print(f"{t.name} v{t.version} ({len(t.steps)} steps) by {t.created_by}")
```

#### Clone template with new inputs

```python
new_pipeline, new_steps = s.pipelines.clone_template(
    template,
    inputs={"video_path": "/data/another_video.mp4"},
)
```

#### Delete template

```python
s.pipelines.delete_template(str(template.id))
```

---

### Export Helper

Export pipelines and registry data to files for git version control or deployment.

#### Export pipeline as JSON

```python
s.export.pipeline(result, "pipelines/my_workflow.json")
# Or from tuple:
s.export.pipeline((pipeline, steps), "pipelines/my_workflow.json")
```

Output file:

```json
{
  "name": "Video Editing Workflow",
  "inputs": {"video_path": "/data/input.mp4"},
  "steps": [
    {"name": "resize", "order": 0, "command_type": "ffmpeg",
     "command_template": "ffmpeg -i {video_path} ...", "condition": "resolution != 720",
     "timeout": 3600, "retry_max": 0}
  ]
}
```

#### Export registry as JSON

```python
s.export.registry("configs/programs.json")
```

Output: JSON array of all programs with parameters, tags, I/O contracts.

Import this file on another instance via the API:

```bash
curl -X POST http://server:8080/api/v1/programs/import \
  -H "Content-Type: application/json" \
  -d @configs/programs.json
```

#### Generate Docker deployment bundle

```python
s.export.docker_bundle(result, "deploy/my_pipeline/", port=8080)
```

Creates:
- `pipeline.json` — pipeline definition
- `Dockerfile` — container build spec
- `docker-compose.yml` — ready to run
- `requirements.txt` — dependencies

Deploy:

```bash
cd deploy/my_pipeline/
docker-compose up --build
```

---

## LLM Providers

Four providers supported for pipeline compilation:

### OpenAI

```yaml
llm:
  provider: "openai"
  model: "gpt-4"                     # or gpt-4-turbo, gpt-3.5-turbo
  api_key: "${OPENAI_API_KEY}"
  temperature: 0.3
```

### Anthropic

```yaml
llm:
  provider: "anthropic"
  model: "claude-3-sonnet-20240229"   # or claude-3-opus, claude-3-haiku
  api_key: "${ANTHROPIC_API_KEY}"
  temperature: 0.3
```

### Google Gemini

```yaml
llm:
  provider: "gemini"
  model: "gemini-pro"                 # or gemini-1.5-pro
  api_key: "${GOOGLE_API_KEY}"
  temperature: 0.3
```

### LMStudio (local models)

```yaml
llm:
  provider: "lmstudio"
  model: "local-model-name"           # model loaded in LMStudio
  base_url: "http://localhost:1234/v1" # LMStudio API endpoint
  temperature: 0.3
  # api_key not required (defaults to "lm-studio")
```

LMStudio uses the OpenAI-compatible API, so any OpenAI-compatible local server works with this provider.

### Notebook override

```python
result = s.compiler.compile(
    "your workflow",
    provider="anthropic",
    model="claude-3-opus-20240229",
    api_key="sk-ant-...",
)
```

---

## Pipeline Concepts

### DAG execution

Pipelines are directed acyclic graphs. Steps declare dependencies via `depends_on_names`. The engine runs steps concurrently when their dependencies are satisfied.

```
Step 1 (resize) ──→ Step 2 (scene detect) ──→ Step 3 (face detection) ──→ Step 4 (assemble)
                                                    ↑ fan-out on clips       ↑ fan-in (waits for all)
```

### Conditions

Steps can have conditions that are evaluated at runtime. If a condition evaluates to False, the step is skipped and downstream steps proceed.

```python
"condition": "resolution != 720"           # comparison
"condition": "has(timecodes) and duration > 0"  # combined
```

### Fan-out / Fan-in

**Fan-out:** A step with `fan_out_on` spawns N parallel executions, one per item in the artifact list.

```python
{"name": "process_clip", "fan_out_on": "clips"}  # runs once per clip
```

**Fan-in:** A step that `depends_on` a fan-out step waits for all parallel branches to complete.

### Artifacts

Steps produce artifacts (structured data or file paths) that flow to downstream steps. Artifacts are stored in the database and reused on pipeline resume.

### Step state machine

```
pending → running → completed
                  → failed_retryable → running (retry)
                  → failed_terminal
                  → skipped (condition false, or manual skip)
                  → interrupted (no result, awaiting user action)
```

### Resume

Interrupted pipelines can be resumed. Completed steps are skipped and their cached artifacts are reused. Only incomplete steps re-execute.

---

## Program Registry & Scoring

### How it works

1. **Register** programs with their command templates, parameters, and I/O contracts
2. **Execute** pipelines — the engine records outcomes automatically
3. **Inject** historical scores to accelerate training (via notebook or API)
4. **Analyze** — the advisory engine detects patterns and recommends changes
5. **Optimize** — update parameters based on recommendations, clear old scores, retrain

### Advisory engine thresholds

| Pattern | Warning | Critical |
|---------|---------|----------|
| Zero output rate | >= 50% | >= 80% |
| Failure rate | >= 30% | >= 60% |
| Timeout rate | >= 20% | — |
| Better param set | 30%+ improvement | — |

Minimum 3 runs required before advisories are generated.

### Training workflow (notebook)

```python
s = Session()

# Register program
s.registry.register(name="myfilter", ...)

# Inject training data for parameter set A
s.scoring.inject_batch("myfilter", "success", {"threshold": 0.3}, count=20)
s.scoring.inject_batch("myfilter", "failure", {"threshold": 0.3}, count=2)

# Inject training data for parameter set B
s.scoring.inject_batch("myfilter", "zero_output", {"threshold": 0.9}, count=15)

# Analyze
s.scoring.show("myfilter")

# Act on recommendations
s.registry.set_param("myfilter", "threshold", 0.3)

# Export for production
s.export.registry("configs/programs.json")
```

---

## Media Storage

### Managed storage layout

```
storage_root/
├── input/          # Pipeline input files
├── output/         # Pipeline output files
├── artifact/       # Intermediate artifacts
└── intermediate/   # Temporary processing files
```

### File tracking

Every ingested file gets:
- Unique ID
- SHA256 hash (deduplication)
- MIME type detection
- Category classification
- Pipeline/step association
- Custom tags and metadata

### Usage via API

```bash
# Ingest a file
curl -X POST http://localhost:8080/api/v1/storage/ingest \
  -H "Content-Type: application/json" \
  -d '{"path": "/data/video.mp4", "category": "input", "tags": ["raw"]}'

# Search
curl "http://localhost:8080/api/v1/storage/files?query=video&category=input"

# Download
curl -O http://localhost:8080/api/v1/storage/files/{id}/download

# Stats
curl http://localhost:8080/api/v1/storage/stats
```

### Usage via notebook

```python
# Storage is managed through the API, but exports work directly:
s.export.pipeline(result, "pipelines/workflow.json")
s.export.registry("configs/programs.json")
```

---

## Deployment

### Single node (SQLite)

```bash
autocut-agent start --config configs/default.yaml
```

Uses SQLite for storage and local locks for resource management.

### Docker

```python
# Generate a Docker bundle from notebook
s.export.docker_bundle(result, "deploy/my_pipeline/")

# Then deploy
cd deploy/my_pipeline/
docker-compose up --build
```

### Git workflow

```bash
# Export pipeline and registry to files
# (from notebook or via API export endpoints)

git add pipelines/ configs/programs.json
git commit -m "Add video editing pipeline v2"
git push

# On deployment server:
git pull
curl -X POST http://server:8080/api/v1/programs/import -d @configs/programs.json
```

---

## Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `OPENAI_API_KEY` | OpenAI LLM provider | If using OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic LLM provider | If using Anthropic |
| `GOOGLE_API_KEY` | Google Gemini provider | If using Gemini |
| `LMSTUDIO_BASE_URL` | LMStudio server URL | If using LMStudio (default: `http://localhost:1234/v1`) |
| `API_SECRET_KEY` | JWT authentication secret | Recommended for production |
| `SMTP_PASSWORD` | Email alert SMTP password | If using email alerts |

Set in `.env` file (copy from `.env.template`) or export directly:

```bash
export OPENAI_API_KEY="sk-..."
export API_SECRET_KEY="$(openssl rand -hex 32)"
```
