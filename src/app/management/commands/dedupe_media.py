"""Deduplicate media rows so each user has at most one row per item.

Re-adding or re-saving media previously created duplicate rows, each able to
hold its own status, so the same title could appear under multiple status
sections (e.g. Planning and Completed). This command keeps one row per
(user, item), merging the most useful fields, and deletes the duplicates.
"""

import logging
from collections import defaultdict

from django.apps import apps
from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import MediaTypes, Status

logger = logging.getLogger(__name__)

# Higher rank is kept when statuses conflict (Completed is kept over Planning).
_STATUS_RANK = {
    Status.COMPLETED.value: 100,
    Status.IN_PROGRESS.value: 90,
    Status.DROPPED.value: 40,
    Status.PAUSED.value: 30,
    Status.PLANNING.value: 20,
    Status.UNWATCHED.value: 10,
}

# Top-level owned media. Season/Episode are child records tied to a TV show and
# are skipped to avoid cascading deletes; TV rows are handled with a guard for
# their seasons.
TOP_LEVEL_MODELS = [
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.BOARDGAME.value,
]

MERGE_FIELDS = ["score", "notes", "start_date", "end_date", "progress"]


def _keep_rank(media):
    return _STATUS_RANK.get(media.status, 0)


def _merge_into(keeper, row):
    """Adopt useful fields from `row` onto `keeper` in memory."""
    if not keeper.score and row.score:
        keeper.score = row.score
    if not keeper.notes and row.notes:
        keeper.notes = row.notes

    if row.start_date and (not keeper.start_date or row.start_date < keeper.start_date):
        keeper.start_date = row.start_date

    if row.end_date and (not keeper.end_date or row.end_date > keeper.end_date):
        keeper.end_date = row.end_date

    if row.progress and row.progress > keeper.progress:
        keeper.progress = row.progress


def _pick_keeper(rows, model):
    """Choose which row to keep from a duplicate group."""
    # For TV, if exactly one row owns the seasons, keep it to avoid cascading.
    if model.__name__ == "TV":
        with_seasons = [r for r in rows if r.seasons.exists()]
        if len(with_seasons) == 1:
            return with_seasons[0]

    return max(
        rows,
        key=lambda r: (
            _keep_rank(r),
            r.score is not None,
            r.notes != "",
            r.created_at or timezone.now(),
        ),
    )


class Command(BaseCommand):
    """Deduplicate media rows per user and item."""

    help = "Deduplicate media rows so each (user, item) has a single row."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be deleted without changing anything.",
        )
        parser.add_argument(
            "--user",
            action="store",
            help="Only process this username.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        username = options.get("user")

        user_filter = {"user__username": username} if username else {}

        processed = 0
        deleted = 0

        for media_type in TOP_LEVEL_MODELS:
            model = apps.get_model(app_label="app", model_name=media_type)
            rows = model.objects.select_related("item", "user").filter(**user_filter)

            groups = defaultdict(list)
            for row in rows:
                groups[(row.user_id, row.item_id)].append(row)

            for (user_id, item_id), group in groups.items():
                if len(group) < 2:
                    continue

                keeper = _pick_keeper(group, model)
                to_delete = [r for r in group if r.id != keeper.id]

                for row in to_delete:
                    _merge_into(keeper, row)
                    deleted += 1
                    self.stdout.write(
                        f"{'[dry]' if dry_run else '[del]'} {row.user.username}: "
                        f"DELETE {model.__name__} #{row.id} "
                        f"({row.item.title!r}, {row.status!r}) -> keep #{keeper.id}"
                    )
                processed += len(group)

                if not dry_run and to_delete:
                    model.objects.filter(pk=keeper.id).update(
                        score=keeper.score,
                        notes=keeper.notes,
                        start_date=keeper.start_date,
                        end_date=keeper.end_date,
                        progress=keeper.progress,
                    )
                    row_ids = [row.id for row in to_delete]
                    model.objects.filter(pk__in=row_ids).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Would delete' if dry_run else 'Deleted'} {deleted} duplicate "
                f"row(s) across {processed} rows examined."
            )
        )
