"""Import watched media from a Plex server via its HTTP API."""

import logging
import warnings
from urllib.parse import urljoin

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

from app.models import Status
from app.providers import services
from integrations.imports import helpers
from integrations.imports.base import BaseImporter, as_datetime
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError

logger = logging.getLogger(__name__)


def importer(server_url, user, mode, token):
    """Import completed movies and TV shows from a Plex server."""
    plex_importer = PlexImporter(server_url, user, mode, token)
    return plex_importer.import_data()


def unwatched_importer(server_url, user, mode, token):
    """Import unwatched/partially-watched movies and shows as Unwatched."""
    plex_importer = PlexImporter(
        server_url,
        user,
        mode,
        token,
        watch_state="unwatched",
    )
    return plex_importer.import_data()


class PlexImporter(BaseImporter):
    """Class to handle importing completed media from a Plex server."""

    notes = "Imported from Plex"

    def __init__(self, server_url, user, mode, token, watch_state="completed"):
        """Initialize the importer.

        Args:
            server_url (str): Base URL of the Plex server (e.g. http://host:32400)
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
            token (str): Fluctuating/API token, symmetrically encrypted
            watch_state (str): "completed" (default) or "unwatched"
        """
        super().__init__(user, mode)
        self.base_url = server_url.strip().rstrip("/")
        self.headers = {"X-Plex-Token": helpers.decrypt(token)}
        if watch_state == "unwatched":
            self.target_status = Status.UNWATCHED.value
            self.notes = "Synced from Plex (Unwatched)"

        logger.info(
            "Initialized Plex importer for user %s (server %s) with mode %s "
            "and watch_state %s",
            user.username,
            self.base_url,
            mode,
            watch_state,
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

    def _import_all(self):
        """Import all completed media from the Plex library."""
        sections = self._fetch_sections()

        movie_sections = [s for s in sections if s["type"] == "movie"]
        show_sections = [s for s in sections if s["type"] == "show"]

        for section in movie_sections:
            self._process_movie_section(section)
        for section in show_sections:
            self._process_show_section(section)

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
        """Import all watched (or unwatched) movies in a movie library section."""
        key = section["key"]
        root = self._api(f"/library/sections/{key}/all?includeGuids=1")
        predicate = _is_watched if self.completed else _is_unwatched
        for item in _iter_items(root):
            if not predicate(item):
                continue

            tmdb_id = _get_tmdb_id(item)
            imdb_id = _get_imdb_id(item)

            if not tmdb_id and imdb_id:
                tmdb_id = self._find_movie_tmdb_by_imdb(imdb_id)

            if not tmdb_id:
                self.warnings.append(f"{_item_title(item)}: No TMDB ID found")
                continue

            last_viewed = as_datetime(item.get("lastViewedAt"))
            self._process_movie(tmdb_id, _item_title(item), last_viewed)

    def _process_show_section(self, section):
        """Import all fully watched (or not fully watched) shows."""
        key = section["key"]
        root = self._api(f"/library/sections/{key}/all?includeGuids=1")
        predicate = _is_fully_watched if self.completed else _is_not_fully_watched
        for item in _iter_items(root):
            if not predicate(item):
                continue

            tmdb_id = _get_tmdb_id(item)
            imdb_id = _get_imdb_id(item)

            if not tmdb_id and imdb_id:
                tmdb_id = self._find_tv_tmdb_by_imdb(imdb_id)

            if not tmdb_id:
                self.warnings.append(f"{_item_title(item)}: No TMDB ID found")
                continue

            last_viewed = as_datetime(item.get("lastViewedAt"))
            self._process_show(tmdb_id, _item_title(item), last_viewed)


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


def _is_unwatched(item):
    try:
        return int(item.get("viewCount", "0")) < 1
    except ValueError:
        return False


def _is_fully_watched(item):
    try:
        leaf_count = int(item.get("leafCount", "0"))
        viewed_leaf_count = int(item.get("viewedLeafCount", "0"))
    except ValueError:
        return False
    return leaf_count > 0 and viewed_leaf_count >= leaf_count


def _is_not_fully_watched(item):
    try:
        leaf_count = int(item.get("leafCount", "0"))
        viewed_leaf_count = int(item.get("viewedLeafCount", "0"))
    except ValueError:
        return False
    return leaf_count > 0 and viewed_leaf_count < leaf_count


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


def _item_title(item):
    return item.get("title") or item.get("name") or "Unknown"
