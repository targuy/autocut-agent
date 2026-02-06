"""Tests for monitoring components."""

from __future__ import annotations

import logging

from agent.core.config import LoggingConfig
from agent.monitoring.logger import setup_logging
from agent.monitoring import metrics


class TestSetupLogging:
    def test_setup_json_logging(self) -> None:
        config = LoggingConfig(format="json", level="DEBUG")
        setup_logging(config)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_setup_text_logging(self) -> None:
        config = LoggingConfig(format="text", level="INFO")
        setup_logging(config)
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_setup_default(self) -> None:
        setup_logging(None)
        root = logging.getLogger()
        assert root.level == logging.INFO


class TestMetrics:
    def test_counter_increment(self) -> None:
        metrics.pipelines_total.labels(status="completed").inc()
        metrics.steps_total.labels(command_type="python", status="completed").inc()

    def test_gauge_set(self) -> None:
        metrics.queue_depth.labels(queue_name="default").set(5)
        metrics.pipelines_active.set(2)
        metrics.executor_active.set(1)

    def test_histogram_observe(self) -> None:
        metrics.pipeline_duration_seconds.observe(12.5)
        metrics.step_duration_seconds.labels(command_type="ffmpeg").observe(3.2)

    def test_resource_metrics(self) -> None:
        metrics.resource_locked.labels(resource_id="gpu:0", resource_type="gpu").set(1)
        metrics.resource_acquisitions.labels(resource_id="gpu:0").inc()
