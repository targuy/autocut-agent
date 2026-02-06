"""Prometheus metrics export."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info


# ---------------------------------------------------------------------------
# Agent-level
# ---------------------------------------------------------------------------

agent_info = Info("autocut_agent", "Agent metadata")

# ---------------------------------------------------------------------------
# Pipeline metrics
# ---------------------------------------------------------------------------

pipelines_total = Counter(
    "autocut_pipelines_total",
    "Total pipelines created",
    ["status"],
)

pipelines_active = Gauge(
    "autocut_pipelines_active",
    "Currently running pipelines",
)

pipeline_duration_seconds = Histogram(
    "autocut_pipeline_duration_seconds",
    "Pipeline execution duration",
    buckets=[1, 5, 10, 30, 60, 300, 600, 1800, 3600],
)

# ---------------------------------------------------------------------------
# Step metrics
# ---------------------------------------------------------------------------

steps_total = Counter(
    "autocut_steps_total",
    "Total steps executed",
    ["command_type", "status"],
)

step_duration_seconds = Histogram(
    "autocut_step_duration_seconds",
    "Step execution duration",
    ["command_type"],
    buckets=[0.5, 1, 5, 10, 30, 60, 300, 600, 1800],
)

# ---------------------------------------------------------------------------
# Queue metrics
# ---------------------------------------------------------------------------

queue_depth = Gauge(
    "autocut_queue_depth",
    "Number of tasks in queue",
    ["queue_name"],
)

queue_tasks_dispatched = Counter(
    "autocut_queue_tasks_dispatched_total",
    "Tasks dispatched from queue",
    ["queue_name"],
)

# ---------------------------------------------------------------------------
# Resource metrics
# ---------------------------------------------------------------------------

resource_locked = Gauge(
    "autocut_resource_locked",
    "Whether a resource is currently locked",
    ["resource_id", "resource_type"],
)

resource_acquisitions = Counter(
    "autocut_resource_acquisitions_total",
    "Resource lock acquisitions",
    ["resource_id"],
)

# ---------------------------------------------------------------------------
# Executor metrics
# ---------------------------------------------------------------------------

executor_active = Gauge(
    "autocut_executor_active",
    "Currently running executor tasks",
)

executor_total = Counter(
    "autocut_executor_total",
    "Total executor invocations",
    ["command_type", "status"],
)
