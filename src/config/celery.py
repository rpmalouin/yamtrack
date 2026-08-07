import os

from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("yamtrack")

app.config_from_object("django.conf:settings", namespace="CELERY")

# Use a dedicated queue instead of the shared default "celery" queue, which is
# also used by other apps on the same Redis broker (e.g. paperless-ngx).
app.conf.task_default_queue = "yamtrack"

# Load task modules from all registered Django apps.
app.autodiscover_tasks()
