"""Import watched media from a Plex server via its HTTP API."""

import logging
import warnings
from collections import defaultdict
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.utils import timezone
from requests.packages.urllib3.exceptions import InsecureRequestWarning

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(server_url, user, mode, token):
    """Import completed movies and TV shows from a Plex server."""
    plex_importer = PlexImporter(server_url, user, mode, token)
    return plex_importer.import_data()


class PlexImporter:
    """Class to handle importing completed media from a Plex server."""

    def __init__(self, server_url, user, mode, token):
        """Initialize the importer.

        Args:
            server_url (str): Base URL of the Plex server (e.g. http://host:32400)
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
            token (str): Fluctuating/API token, symmetrically encrypted
        """
        self.base_url = server_url.strip().rstrip("/")
        self.headers = {"X-Plex-Token": helpers.decrypt(token)}
        self.user = user
        self.mode = mode
        self.warnings = []

        self.existing_media = helpers.get_existing_media(user)
        self.to_delete = defaultdict(lambda: defaultdict(set))
        self.bulk_media = defaultdict(list)

        logger.info(
            "Initialized Plex importer for user %s (server %s) with mode %s",
            user.username,
            self.base_url,
            mode,
        )

    def _api(self, path):
        """Perform a Plex API request and return the root XML element."""
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        try:
            # Plex servers typically use self-signed certs that don't match
            # the LAN address; skip TLS verification for this user-supplied URL.
            warnings.simplefilter("ignore", InsecureRequestWarning)
            return services.api_request(
                "PLEX",
                "GET",
                url,
                headers=self.headers,
                response_format="xml",
                verify=False,
            )
        except requests.exceptions.HTTPError as error:
            has_response = error.response is not None
            status = error.response.status_code if has_response else "unknown"
            if status in (requests.codes.unauthorized, requests.codes.forbidden):
                msg = "Invalid Plex server URL or token."
                raise MediaImportError(msg) from error
            msg = f"Plex API error: {status}"
            raise MediaImportUnexpectedError(msg) from error

    def import_data(self):
        """Import all completed media from the Plex library."""
        sections = self._fetch_sections()

        movie_sections = [s for s in sections if s["type"] == "movie"]
        show_sections = [s for s in sections if s["type"] == "show"]

        for section in movie_sections:
            self._process_movie_section(section)
        for section in show_sections:
            self._process_show_section(section)

        helpers.cleanup_existing_media(self.to_delete, self.user)
        helpers.bulk_create_media(self.bulk_media, self.user)

        imported_counts = {
            media_type: len(media_list)
            for media_type, media_list in self.bulk_media.items()
        }

        deduplicated_messages = "\n".join(dict.fromkeys(self.warnings))
        return imported_counts, deduplicated_messages

    def _fetch_sections(self):
        """Return the library sections of the Plex server."""
        root = self._api("/library/sections")
        sections = []
        for element in root:
            if "type" not in element.attrib:
                continue
            sections.append(
                {
                    "key": element.get("key"),
                    "type": element.get("type"),
                    "title": element.get("title"),
                }
            )
        return sections

    def _process_movie_section(self, section):
        """Import all watched movies in a movie library section."""
        key = section["key"]
        root = self._api(f"/library/sections/{key}/all?includeGuids=1")
        for item in _iter_items(root):
            if not _is_watched(item):
                continue

            tmdb_id = _get_tmdb_id(item)
            imdb_id = _get_imdb_id(item)

            if not tmdb_id and imdb_id:
                tmdb_id = _find_movie_tmdb_by_imdb(imdb_id)

            if not tmdb_id:
                self.warnings.append(f"{_item_title(item)}: No TMDB ID found")
                continue

            self._process_movie(tmdb_id, item)

    def _process_show_section(self, section):
        """Import all fully watched TV shows in a show library section."""
        key = section["key"]
        root = self._api(f"/library/sections/{key}/all?includeGuids=1")
        for item in _iter_items(root):
            if not _is_fully_watched(item):
                continue

            tmdb_id = _get_tmdb_id(item)
            imdb_id = _get_imdb_id(item)

            if not tmdb_id and imdb_id:
                tmdb_id = _find_tv_tmdb_by_imdb(imdb_id)

            if not tmdb_id:
                self.warnings.append(f"{_item_title(item)}: No TMDB ID found")
                continue

            self._process_show(tmdb_id, item)

    def _process_movie(self, tmdb_id, item):
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
            self._handle_metadata_error(item, tmdb_id, error)
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

        last_viewed = _as_datetime(item.get("lastViewedAt"))
        movie_instance = app.models.Movie(
            item=movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=last_viewed,
            end_date=last_viewed,
            notes="Imported from Plex",
        )
        movie_instance._history_date = last_viewed or timezone.now()
        self.bulk_media[MediaTypes.MOVIE.value].append(movie_instance)

    def _process_show(self, tmdb_id, item):
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
            self._handle_metadata_error(item, tmdb_id, error)
            return

        season_numbers = [
            season["season_number"]
            for season in tv_metadata.get("related", {}).get("seasons", [])
            if season.get("season_number", 0) > 0
        ]

        try:
            metadata = app.providers.tmdb.tv_with_seasons(tmdb_id, season_numbers)
        except services.ProviderAPIError as error:
            self._handle_metadata_error(item, tmdb_id, error)
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

        last_viewed = _as_datetime(item.get("lastViewedAt"))
        tv_instance = app.models.TV(
            item=tv_item,
            user=self.user,
            status=Status.COMPLETED.value,
            notes="Imported from Plex",
        )
        tv_instance._history_date = last_viewed or timezone.now()
        self.bulk_media[MediaTypes.TV.value].append(tv_instance)

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
                    "image": _episode_image(episode),
                },
            )

            episode_instance = app.models.Episode(
                item=episode_item,
                related_season=season_instance,
                end_date=last_viewed or timezone.now(),
            )
            episode_instance._history_date = last_viewed or timezone.now()
            self.bulk_media[MediaTypes.EPISODE.value].append(episode_instance)

    def _handle_metadata_error(self, item, tmdb_id, error):
        """Record a helpful warning when TMDB metadata can't be resolved."""
        if getattr(error, "status_code", None) == requests.codes.not_found:
            title = _item_title(item)
            self.warnings.append(
                f"{title}: not found in {Sources.TMDB.label} with ID {tmdb_id}."
            )
            return
        msg = f"TMDB lookup failed for {tmdb_id}"
        raise MediaImportUnexpectedError(msg) from error


def _iter_items(root):
    """Yield direct child elements of a MediaContainer that represent media items."""
    for element in root:
        if "ratingKey" not in element.attrib:
            continue
        yield element


def _is_watched(item):
    try:
        return int(item.get("viewCount", "0")) >= 1
    except ValueError:
        return False


def _is_fully_watched(item):
    try:
        leaf_count = int(item.get("leafCount", "0"))
        viewed_leaf_count = int(item.get("viewedLeafCount", "0"))
    except ValueError:
        return False
    return leaf_count > 0 and viewed_leaf_count >= leaf_count


def _get_tmdb_id(item):
    for guid in item.findall("Guid"):
        guid_id = guid.get("id", "")
        if guid_id.startswith("tmdb://"):
            return guid_id.split("tmdb://", 1)[1]
    return None


def _get_imdb_id(item):
    for guid in item.findall("Guid"):
        guid_id = guid.get("id", "")
        if guid_id.startswith("imdb://"):
            return guid_id.split("imdb://", 1)[1]
    return None


def _find_movie_tmdb_by_imdb(imdb_id):
    response = app.providers.tmdb.find(imdb_id, "imdb_id")
    if response.get("movie_results"):
        return response["movie_results"][0]["id"]
    return None


def _find_tv_tmdb_by_imdb(imdb_id):
    response = app.providers.tmdb.find(imdb_id, "imdb_id")
    if response.get("tv_results"):
        return response["tv_results"][0]["id"]
    return None


def _item_title(item):
    return item.get("title") or item.get("name") or "Unknown"

def _as_datetime(timestamp):
    """Convert a Plex Unix timestamp to a timezone-aware datetime."""
    if not timestamp:
        return None
    try:
        return timezone.datetime.fromtimestamp(
            int(timestamp),
            tz=timezone.get_current_timezone(),
        ).replace(second=0, microsecond=0)
    except (ValueError, OverflowError, OSError):
        return None


def _episode_image(episode):
    still_path = episode.get("still_path")
    if still_path:
        return f"https://image.tmdb.org/t/p/w500{still_path}"
    return settings.IMG_NONE
