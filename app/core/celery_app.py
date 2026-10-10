"""Celery Distributed Task Queue Application Setup."""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "bulk_certificate_generator",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.certificate_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # acks_late ensures that if a worker crashes midway, the task is redelivered
    task_acks_late=True,
    # worker_prefetch_multiplier=1 prevents a single worker from hoarding tasks
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=False,
    broker_connection_max_retries=1,
    broker_connection_timeout=1.0,
)
