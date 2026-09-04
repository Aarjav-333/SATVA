"""Celery application and beat schedule.

Three periodic jobs:

* **clustering** -- recompute hotspots. Runs often enough that a new corroborating
  reading turns into a visible cluster within the hour, which is the cadence the
  officer worklist is useful at.
* **merkle anchoring** -- publish the daily root over custody events. Runs once a
  day, just after midnight IST, covering the previous whole day.
* **retention sweep** -- delete evidence images past their retention deadline
  (spec 15.3).
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()

celery_app = Celery(
    "satva",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=540,
    worker_max_tasks_per_child=200,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
)

celery_app.conf.beat_schedule = {
    "recompute-hotspot-clusters": {
        "task": "satva.watch.recompute_clusters",
        "schedule": crontab(minute="*/30"),
    },
    "publish-daily-merkle-root": {
        "task": "satva.trace.publish_merkle_root",
        # 00:15 IST, so the previous day is complete before it is anchored.
        "schedule": crontab(hour=0, minute=15),
    },
    "expire-evidence-images": {
        "task": "satva.retention.sweep_expired_images",
        "schedule": crontab(hour=3, minute=0),
    },
    "refresh-shelf-predictions": {
        "task": "satva.shelf.refresh_predictions",
        "schedule": crontab(hour="*/6", minute=5),
    },
}
