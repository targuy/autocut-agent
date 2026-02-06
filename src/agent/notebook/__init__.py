"""Jupyter notebook SDK for interactive pipeline development.

Provides sync wrappers around the async AutoCut-Agent APIs, designed
for use in Jupyter notebooks.  All changes persist to the database.

Quick start::

    from agent.notebook import Session

    s = Session()                       # connects to default DB
    s = Session("sqlite+aiosqlite:///my.db")  # or custom DB

    # Registry
    s.registry.list()
    s.registry.get("facedetection")
    s.registry.set_param("facedetection", "threshold", 0.7)

    # Scoring
    s.scoring.stats("facedetection")
    s.scoring.inject("facedetection", "success", {"threshold": 0.7})

    # Conditions
    s.conditions.test("resolution != 720", {"resolution": 1080})

    # Pipelines
    result = s.compiler.compile("resize video then detect faces", {"video": "/v.mp4"})
    s.pipelines.inspect(result.pipeline)

    # Export
    s.export.pipeline(result, "pipelines/workflow.json")
    s.export.registry("configs/programs.json")
"""

from agent.notebook.sdk import Session

__all__ = ["Session"]
