"""Import watched media from an Emby server via its HTTP API."""

import logging
import warnings
from urllib.parse import urljoin

import requests
from django.utils.dateparse import parse_datetime
from requests.packages.urllib3.exceptions import InsecureRequestWarning

import app
from app.providers import services
from integrations.imports import helpers
from integrations.imports.base import BaseImporter
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)

_CLIENT_NAME = "Yamtrack"
_DEVICE_NAME = "Yamtrack Importer"
_DEVICE_ID = "yamtrack-import"
_CLIENT_VERSION = "1.0"
FULLY_WATCHED_PERCENTAGE = 100


def importer(server_url, user, mode, emby_username, token):
    """Import completed movies and fully-watched shows from an Emby server."""
    emby_importer = EmbyImporter(server_url, user, mode, emby_username, token)
    return emby_importer.import_data()


class EmbyImporter(BaseImporter):
    """Class to handle importing completed media from an Emby server."""

    notes = "Imported from Emby"

    def __init__(self, server_url, user, mode, emby_username, token):
        """Initialize the importer.

        Args:
            server_url (str): Base URL of the Emby server (e.g. http://host:8096)
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
            emby_username (str): Emby username to authenticate and import for
            token (str): Emby password, symmetrically encrypted
        """
        super().__init__(user, mode)
        self.base_url = server_url.strip().rstrip("/")
        self.emby_username = emby_username
        self.password = helpers.decrypt(token)
        self.user_id = None

        logger.info(
            "Initialized Emby importer for user %s (server %s) with mode %s",
            user.username,
            self.base_url,
            mode,
        )

    def _auth_header(self, token=None):
        """Build the X-Emby-Authorization header required by the Emby API."""
        value = (
            f'MediaBrowser Client="{_CLIENT_NAME}", Device="{_DEVICE_NAME}", '
            f'DeviceId="{_DEVICE_ID}", Version="{_CLIENT_VERSION}"'
        )
        if token:
            value += f', Token="{token}"'
        return {"X-Emby-Authorization": value}

    def _api(self, path, params=None, headers=None):
        """Perform an Emby API request and return the parsed JSON."""
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        try:
            # Self-hosted Emby servers often use self-signed certs that don't
            # match the LAN address; skip TLS verification for this user URL
            # (mirrors the Plex importer).
            return services.api_request(
                "EMBY",
                "GET",
                url,
                params=params,
                headers=headers,
                verify=False,
            )
        except requests.exceptions.HTTPError as error:
            has_response = error.response is not None
            status = error.response.status_code if has_response else "unknown"
            if status in (requests.codes.unauthorized, requests.codes.forbidden):
                msg = "Invalid Emby server URL or credentials."
                raise MediaImportError(msg) from error
            msg = f"Emby API error: {status}"
            raise MediaImportUnexpectedError(msg) from error

    def _authenticate(self):
        """Authenticate against the Emby server and cache the user id."""
        warnings.simplefilter("ignore", InsecureRequestWarning)
        try:
            response = services.api_request(
                "EMBY",
                "POST",
                urljoin(f"{self.base_url}/", "Users/AuthenticateByName"),
                params={
                    "Username": self.emby_username,
                    "Pw": self.password,
                },
                headers=self._auth_header(),
                verify=False,
            )
        except requests.exceptions.HTTPError as error:
            has_response = error.response is not None
            status = error.response.status_code if has_response else "unknown"
            if status in (requests.codes.unauthorized, requests.codes.forbidden):
                msg = "Invalid Emby server URL or credentials."
                raise MediaImportError(msg) from error
            msg = f"Emby API error: {status}"
            raise MediaImportUnexpectedError(msg) from error

        if not response.get("AccessToken"):
            msg = "Invalid Emby server URL or credentials."
            raise MediaImportError(msg)

        self.access_token = response["AccessToken"]
        self.user_id = response.get("User", {}).get("Id")

        if not self.user_id:
            msg = "Unable to determine the Emby user."
            raise MediaImportError(msg)

    def _import_all(self):
        """Import all completed media for the authenticated Emby user."""
        self._authenticate()
        headers = {"X-Emby-Token": self.access_token}

        limit = 500
        start_index = 0
        processed = 0

        while True:
            response = self._api(
                f"/Users/{self.user_id}/Items",
                params={
                    "Recursive": "true",
                    "Fields": "ProviderIds",
                    "Filters": "IsPlayed",
                    "IncludeItemTypes": "Movie,Series",
                    "SortBy": "DatePlayed",
                    "SortOrder": "Descending",
                    "Limit": limit,
                    "StartIndex": start_index,
                },
                headers=headers,
            )

            items = response.get("Items", [])
            for item in items:
                self._process_item(item)

            total = response.get("TotalRecordCount", 0)
            processed += len(items)
            if processed >= total or not items:
                break
            start_index += limit

    def _process_item(self, item):
        """Import a single movie or series item if fully watched."""
        item_type = item.get("Type")
        user_data = item.get("UserData", {})

        if item_type == "Movie" and user_data.get("Played") is True:
            self._process_movie_item(item)
        elif item_type == "Series" and _is_progress_complete(user_data):
            self._process_series_item(item)

    def _process_movie_item(self, item):
        """Import a completed movie using its ProviderIds."""
        tmdb_id = self._resolve_tmdb_id(item, found=_find_movie_tmdb_by_imdb)
        if tmdb_id is None:
            self.warnings.append(f"{_item_title(item)}: No TMDB ID found")
            return

        last_viewed = _parse_date(item.get("DatePlayed"))
        self._process_movie(tmdb_id, _item_title(item), last_viewed)

    def _process_series_item(self, item):
        """Import a fully watched series using its ProviderIds."""
        tmdb_id = self._resolve_tmdb_id(item, found=_find_tv_tmdb_by_imdb)
        if tmdb_id is None:
            self.warnings.append(f"{_item_title(item)}: No TMDB ID found")
            return

        last_viewed = _parse_date(item.get("DatePlayed"))
        self._process_show(tmdb_id, _item_title(item), last_viewed)

    def _resolve_tmdb_id(self, item, found):
        """Resolve a TMDB id from an Emby item's ProviderIds."""
        provider_ids = item.get("ProviderIds", {})
        tmdb_id = provider_ids.get("Tmdb")
        if tmdb_id:
            return tmdb_id

        imdb_id = provider_ids.get("Imdb")
        if imdb_id:
            return found(imdb_id)

        tvdb_id = provider_ids.get("Tvdb")
        if tvdb_id:
            response = app.providers.tmdb.find(tvdb_id, "tvdb_id")
            if response.get("tv_results"):
                return response["tv_results"][0]["id"]

        return None


def _is_progress_complete(user_data):
    """Return True if the series' watched progress is 100%."""
    played_percentage = user_data.get("PlayedPercentage")
    if played_percentage is None:
        return False
    try:
        return float(played_percentage) >= FULLY_WATCHED_PERCENTAGE
    except (TypeError, ValueError):
        return False


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
    return item.get("Name") or "Unknown"


def _parse_date(value):
    """Parse an Emby ISO datetime string into an aware datetime, or None."""
    if not value:
        return None
    return parse_datetime(str(value))
