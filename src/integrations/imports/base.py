"""Shared logic for importing completed media via self-hosted server APIs."""

import logging
from collections import defaultdict

import requests
from django.conf import settings
from django.utils import timezone

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportUnexpectedError

logger = logging.getLogger(__name__)


class BaseImporter:
    """Base class for importing completed media from a media server.

    Subclasses implement ``_import_all`` (fetching the watched items from the
    server API) and reuse ``_process_movie`` / ``_process_show`` to create the
    corresponding Completed media (and completed seasons/episodes) in Yamtrack.
    """

    notes = "Imported"

    def __init__(self, user, mode):
        """Initialize the importer.

        Args:
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.user = user
        self.mode = mode
        self.warnings = []
        # Importers can set this to Status.UNWATCHED to create "Unwatched"
        # items instead of Completed media.
        self.target_status = Status.COMPLETED.value

        self.existing_media = helpers.get_existing_media(user)
        self.to_delete = defaultdict(lambda: defaultdict(set))
        self.bulk_media = defaultdict(list)

    @property
    def completed(self):
        """Return True when this importer creates Completed media."""
        return self.target_status == Status.COMPLETED.value

    def import_data(self):
        """Import all completed media and return counts plus warning messages."""
        self._import_all()

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _import_all(self):
        """Fetch and process all completed media from the server."""
        raise NotImplementedError

    def _process_movie(self, tmdb_id, title, last_viewed):
        """Create a completed movie instance."""
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.MOVIE.value,
            Sources.TMDB.value,
            str(tmdb_id),
            self.mode,
        ):
            return

        try:
            metadata = app.providers.tmdb.movie(tmdb_id)
        except services.ProviderAPIError as error:
            self._handle_metadata_error(tmdb_id, title, error)
            return

        movie_item, _ = app.models.Item.objects.get_or_create(
            media_id=str(tmdb_id),
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )

        movie_instance = app.models.Movie(
            item=movie_item,
            user=self.user,
            status=self.target_status,
            progress=1 if self.completed else 0,
            start_date=last_viewed if self.completed else None,
            end_date=last_viewed if self.completed else None,
            notes=self.notes,
        )
        movie_instance._history_date = last_viewed or timezone.now()
        self.bulk_media[MediaTypes.MOVIE.value].append(movie_instance)

    def _process_show(self, tmdb_id, title, last_viewed):
        """Create a completed TV show instance with completed seasons/episodes."""
        if not helpers.should_process_media(
            self.existing_media,
            self.to_delete,
            MediaTypes.TV.value,
            Sources.TMDB.value,
            str(tmdb_id),
            self.mode,
        ):
            return

        try:
            tv_metadata = app.providers.tmdb.tv(tmdb_id)
        except services.ProviderAPIError as error:
            self._handle_metadata_error(tmdb_id, title, error)
            return

        season_numbers = []
        metadata = {}

        if self.completed:
            season_numbers = [
                season["season_number"]
                for season in tv_metadata.get("related", {}).get("seasons", [])
                if season.get("season_number", 0) > 0
            ]
            try:
                metadata = app.providers.tmdb.tv_with_seasons(tmdb_id, season_numbers)
            except services.ProviderAPIError as error:
                self._handle_metadata_error(tmdb_id, title, error)
                return

        tv_item, _ = app.models.Item.objects.get_or_create(
            media_id=str(tmdb_id),
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            defaults={
                "title": tv_metadata["title"],
                "image": tv_metadata["image"],
            },
        )

        tv_instance = app.models.TV(
            item=tv_item,
            user=self.user,
            status=self.target_status,
            notes=self.notes,
        )
        tv_instance._history_date = last_viewed or timezone.now()
        self.bulk_media[MediaTypes.TV.value].append(tv_instance)

        if not self.completed:
            return

        for season_number in season_numbers:
            self._process_completed_season(
                tmdb_id,
                season_number,
                tv_instance,
                metadata,
                last_viewed,
            )

    def _process_completed_season(
        self,
        tmdb_id,
        season_number,
        tv_instance,
        metadata,
        last_viewed,
    ):
        """Create a completed season and its episodes."""
        season_metadata = metadata.get(f"season/{season_number}")
        if not season_metadata:
            return

        season_item, _ = app.models.Item.objects.get_or_create(
            media_id=str(tmdb_id),
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": season_metadata["image"],
            },
        )

        season_instance = app.models.Season(
            item=season_item,
            user=self.user,
            related_tv=tv_instance,
            status=Status.COMPLETED.value,
        )
        season_instance._history_date = last_viewed or timezone.now()
        self.bulk_media[MediaTypes.SEASON.value].append(season_instance)

        for episode in season_metadata.get("episodes", []):
            episode_item, _ = app.models.Item.objects.get_or_create(
                media_id=str(tmdb_id),
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=episode["episode_number"],
                defaults={
                    "title": metadata["title"],
                    "image": episode_image(episode.get("still_path")),
                },
            )

            episode_instance = app.models.Episode(
                item=episode_item,
                related_season=season_instance,
                end_date=last_viewed or timezone.now(),
            )
            episode_instance._history_date = last_viewed or timezone.now()
            self.bulk_media[MediaTypes.EPISODE.value].append(episode_instance)

    def _handle_metadata_error(self, tmdb_id, title, error):
        """Record a helpful warning when TMDB metadata can't be resolved."""
        if getattr(error, "status_code", None) == requests.codes.not_found:
            self.warnings.append(
                f"{title}: not found in {Sources.TMDB.label} with ID {tmdb_id}."
            )
            return
        msg = f"TMDB lookup failed for {tmdb_id}"
        raise MediaImportUnexpectedError(msg) from error

    @staticmethod
    def _find_movie_tmdb_by_imdb(imdb_id):
        response = app.providers.tmdb.find(imdb_id, "imdb_id")
        if response.get("movie_results"):
            return response["movie_results"][0]["id"]
        return None

    @staticmethod
    def _find_tv_tmdb_by_imdb(imdb_id):
        response = app.providers.tmdb.find(imdb_id, "imdb_id")
        if response.get("tv_results"):
            return response["tv_results"][0]["id"]
        return None


def as_datetime(timestamp):
    """Convert a Unix timestamp to a timezone-aware datetime."""
    if not timestamp:
        return None
    try:
        return timezone.datetime.fromtimestamp(
            int(timestamp),
            tz=timezone.get_current_timezone(),
        ).replace(second=0, microsecond=0)
    except (ValueError, OverflowError, OSError):
        return None


def episode_image(still_path):
    """Build a TMDB image URL from a still path, or the placeholder image."""
    if still_path:
        return f"https://image.tmdb.org/t/p/w500{still_path}"
    return settings.IMG_NONE
