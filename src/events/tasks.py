import logging

from celery import shared_task
from django.contrib.auth import get_user_model

from app.models import Item
from events import notifications
from events.calendar.main import fetch_releases

logger = logging.getLogger(__name__)


@shared_task(name="Reload calendar")
def reload_calendar(user_id=None, item_ids=None):
    """Refresh the calendar with latest dates for all users.

    Args are intentionally JSON-serializable primitives (user id and item ids)
    rather than ORM instances to keep the task safe with the ``json`` serializer.
    """
    user = None
    if user_id:
        user = get_user_model().objects.filter(id=user_id).first()
        if user:
            logger.info("Reloading calendar for user: %s", user.username)

    items_to_process = None
    if item_ids:
        items_to_process = list(Item.objects.filter(id__in=item_ids))

    if user is None and item_ids is None:
        logger.info("Reloading calendar for all users")

    return fetch_releases(
        user=user,
        items_to_process=items_to_process,
    )


@shared_task(name="Send release notifications")
def send_release_notifications():
    """Send notifications for recently released media."""
    logger.info("Starting recent release notification task")

    return notifications.send_releases()


@shared_task(name="Send daily digest")
def send_daily_digest_notifications():
    """Send daily digest of today's releases."""
    logger.info("Starting daily digest task")

    return notifications.send_daily_digest()
