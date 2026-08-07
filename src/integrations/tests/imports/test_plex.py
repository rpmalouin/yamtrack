from unittest.mock import patch

from defusedxml import ElementTree
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from requests import Response
from requests.exceptions import HTTPError

from app.models import Episode, MediaTypes, Status
from integrations.imports import helpers, plex

SECTIONS_XML = """
<MediaContainer>
  <Directory key="1" type="movie" title="Movies"/>
  <Directory key="2" type="show" title="TV Shows"/>
</MediaContainer>
"""

MOVIES_XML = """
<MediaContainer>
  <Video ratingKey="1" title="The Matrix" type="movie" viewCount="1"
         lastViewedAt="1704067200">
    <Guid id="tmdb://603"/>
    <Guid id="imdb://tt0133093"/>
  </Video>
  <Video ratingKey="2" title="Unwatched Movie" type="movie" viewCount="0">
    <Guid id="tmdb://999"/>
  </Video>
</MediaContainer>
"""

SHOWS_XML = """
<MediaContainer>
  <Directory ratingKey="3" title="Breaking Bad" type="show"
             leafCount="62" viewedLeafCount="62" lastViewedAt="1704067200">
    <Guid id="tmdb://1396"/>
  </Directory>
  <Directory ratingKey="4" title="Half Watched" type="show"
             leafCount="62" viewedLeafCount="30">
    <Guid id="tmdb://100"/>
  </Directory>
</MediaContainer>
"""


class ImportPlex(TestCase):
    """Test importing completed media from a Plex server."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.token = helpers.encrypt("plex-token")

    def _tree(self, xml):
        return ElementTree.fromstring(xml)

    def _resolve(self, media_id):
        if media_id == "603":
            return {"title": "The Matrix", "image": "http://example.com/matrix.jpg"}
        if media_id == "1396":
            return {
                "title": "Breaking Bad",
                "image": "http://example.com/bb.jpg",
                "related": {
                    "seasons": [
                        {"media_id": "1396", "season_number": 1},
                        {"media_id": "1396", "season_number": 2},
                    ],
                },
            }
        msg = f"Unexpected TMDB ID: {media_id}"
        raise AssertionError(msg)

    def _tv_with_seasons(self, media_id, _season_numbers):
        if media_id == "1396":
            return {
                "title": "Breaking Bad",
                "image": "http://example.com/bb.jpg",
                "season/1": {
                    "image": "http://example.com/s1.jpg",
                    "episodes": [
                        {"episode_number": 1, "still_path": None},
                        {"episode_number": 2, "still_path": None},
                    ],
                },
                "season/2": {
                    "image": "http://example.com/s2.jpg",
                    "episodes": [
                        {"episode_number": 1, "still_path": None},
                    ],
                },
            }
        msg = f"Unexpected TV with seasons ID: {media_id}"
        raise AssertionError(msg)

    @patch("integrations.imports.base.app.providers.tmdb.tv")
    @patch("integrations.imports.base.app.providers.tmdb.tv_with_seasons")
    @patch("integrations.imports.base.app.providers.tmdb.movie")
    @patch("integrations.imports.plex.services.api_request")
    def test_import_completed_media(
        self,
        mock_api_request,
        mock_movie,
        mock_tv_with_seasons,
        mock_tv,
    ):
        """Test importing completed movies and shows."""
        mock_api_request.side_effect = [
            self._tree(SECTIONS_XML),
            self._tree(MOVIES_XML),
            self._tree(SHOWS_XML),
        ]
        mock_movie.side_effect = self._resolve
        mock_tv.side_effect = self._resolve
        mock_tv_with_seasons.side_effect = self._tv_with_seasons
        # Ensure the sections are fetched with includeGuids so TMDB ids resolve
        urls = [call.args[2] for call in mock_api_request.call_args_list]
        self.assertTrue(
            all("includeGuids=1" in url for url in urls[1:]),
        )

        imported_counts, warnings = plex.importer(
            "http://localhost:32400",
            self.user,
            "new",
            token=self.token,
        )

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 1)
        self.assertEqual(imported_counts[MediaTypes.TV.value], 1)
        self.assertEqual(imported_counts[MediaTypes.SEASON.value], 2)
        self.assertEqual(imported_counts[MediaTypes.EPISODE.value], 3)

        movie = self.user.movie_set.get(item__media_id="603")
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)

        tv = self.user.tv_set.get(item__media_id="1396")
        self.assertEqual(tv.status, Status.COMPLETED.value)

        seasons = self.user.season_set.filter(related_tv=tv)
        self.assertEqual(seasons.count(), 2)
        self.assertEqual(
            seasons.filter(status=Status.COMPLETED.value).count(),
            2,
        )

        episodes = Episode.objects.filter(related_season__related_tv=tv).count()
        self.assertEqual(episodes, 3)

        self.assertFalse(self.user.movie_set.filter(item__media_id="999").exists())
        self.assertFalse(self.user.tv_set.filter(item__media_id="100").exists())
        self.assertEqual(warnings, "")

    @patch("integrations.imports.plex.services.api_request")
    def test_import_invalid_credentials(self, mock_api_request):
        """Test that an invalid token raises a friendly error."""
        response = Response()
        response.status_code = 401
        mock_api_request.side_effect = HTTPError(response=response)

        with self.assertRaises(helpers.MediaImportError) as context:
            plex.importer(
                "http://localhost:32400",
                self.user,
                "new",
                token=self.token,
            )

        self.assertIn("Invalid Plex server URL or token", str(context.exception))

    UNWATCHED_MOVIES_XML = """
    <MediaContainer>
      <Video ratingKey="1" title="Unwatched Movie" type="movie" viewCount="0">
        <Guid id="tmdb://603"/>
      </Video>
      <Video ratingKey="2" title="Watched Movie" type="movie" viewCount="1">
        <Guid id="tmdb://999"/>
      </Video>
    </MediaContainer>
    """

    PARTIAL_SHOWS_XML = """
    <MediaContainer>
      <Directory ratingKey="3" title="Partial Show" type="show"
                 leafCount="62" viewedLeafCount="30">
        <Guid id="tmdb://1396"/>
      </Directory>
      <Directory ratingKey="4" title="Fully Watched Show" type="show"
                 leafCount="62" viewedLeafCount="62">
        <Guid id="tmdb://100"/>
      </Directory>
    </MediaContainer>
    """

    @patch("integrations.imports.base.app.providers.tmdb.tv")
    @patch("integrations.imports.base.app.providers.tmdb.movie")
    @patch("integrations.imports.plex.services.api_request")
    def test_import_unwatched_media(
        self,
        mock_api_request,
        mock_movie,
        mock_tv,
    ):
        """Test importing unwatched movies and not-fully-watched shows."""
        mock_api_request.side_effect = [
            self._tree(SECTIONS_XML),
            self._tree(self.UNWATCHED_MOVIES_XML),
            self._tree(self.PARTIAL_SHOWS_XML),
        ]
        mock_movie.side_effect = self._resolve
        mock_tv.side_effect = self._resolve

        imported_counts, warnings = plex.unwatched_importer(
            "http://localhost:32400",
            self.user,
            "new",
            token=self.token,
        )

        self.assertEqual(imported_counts[MediaTypes.MOVIE.value], 1)
        self.assertEqual(imported_counts[MediaTypes.TV.value], 1)
        self.assertEqual(imported_counts.get(MediaTypes.SEASON.value, 0), 0)
        self.assertEqual(imported_counts.get(MediaTypes.EPISODE.value, 0), 0)

        movie = self.user.movie_set.get(item__media_id="603")
        self.assertEqual(movie.status, Status.UNWATCHED.value)
        self.assertEqual(movie.progress, 0)

        tv = self.user.tv_set.get(item__media_id="1396")
        self.assertEqual(tv.status, Status.UNWATCHED.value)
        self.assertFalse(self.user.season_set.filter(related_tv=tv).exists())

        self.assertFalse(self.user.movie_set.filter(item__media_id="999").exists())
        self.assertFalse(self.user.tv_set.filter(item__media_id="100").exists())
        self.assertEqual(warnings, "")

    def test_helper_is_watched_and_fully_watched(self):
        """Test the watched/completed detection helpers."""
        movie = ElementTree.fromstring(
            '<Video ratingKey="1" viewCount="1"><Guid id="tmdb://603"/></Video>'
        )
        self.assertTrue(plex._is_watched(movie))

        unwatched = ElementTree.fromstring('<Video ratingKey="2" viewCount="0"/>')
        self.assertFalse(plex._is_watched(unwatched))

        show = ElementTree.fromstring(
            '<Directory ratingKey="3" leafCount="10" viewedLeafCount="10"/>'
        )
        self.assertTrue(plex._is_fully_watched(show))

        half = ElementTree.fromstring(
            '<Directory ratingKey="4" leafCount="10" viewedLeafCount="5"/>'
        )
        self.assertFalse(plex._is_fully_watched(half))

        self.assertEqual(plex._get_tmdb_id(movie), "603")


class ImportPlexViewTests(TestCase):
    """Test the Plex import view."""

    def setUp(self):
        """Log in a user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.url = reverse("import_plex")

    @patch("integrations.views.tasks.import_plex.delay")
    def test_import_plex_once(self, mock_delay):
        """Test a one-time import queues the Plex import task."""
        response = self.client.post(
            self.url,
            {
                "server_url": "http://localhost:32400",
                "token": "secret-token",
                "mode": "new",
                "frequency": "once",
                "time": "14:30",
            },
        )
        self.assertRedirects(response, reverse("import_data"))
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.kwargs["mode"], "new")
        self.assertEqual(
            mock_delay.call_args.kwargs["username"],
            "http://localhost:32400",
        )

    def test_import_plex_missing_fields(self):
        """Test that missing server URL or token is rejected."""
        response = self.client.post(
            self.url,
            {"server_url": "", "token": "", "mode": "new", "frequency": "once"},
        )
        self.assertRedirects(response, reverse("import_data"))

    @patch("integrations.views.helpers.create_import_schedule")
    def test_import_plex_periodic(self, mock_schedule):
        """Test a periodic import schedules the Plex import task."""
        response = self.client.post(
            self.url,
            {
                "server_url": "http://localhost:32400",
                "token": "secret-token",
                "mode": "new",
                "frequency": "daily",
                "time": "09:00",
            },
        )
        self.assertRedirects(response, reverse("import_data"))
        mock_schedule.assert_called_once()
        self.assertEqual(mock_schedule.call_args.args[5], "Plex")
        self.assertIn("token", mock_schedule.call_args.kwargs)
