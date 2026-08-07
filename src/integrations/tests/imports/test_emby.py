from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from requests import Response
from requests.exceptions import HTTPError

from app.models import Episode, MediaTypes, Status
from integrations.imports import emby, helpers

AUTH_RESPONSE = {
    "User": {"Id": "user1", "Name": "emby-user"},
    "AccessToken": "access-token",
    "ServerId": "server1",
}

ITEMS_RESPONSE = {
    "Items": [
        {
            "Type": "Movie",
            "Name": "The Matrix",
            "ProviderIds": {"Tmdb": "603"},
            "UserData": {"Played": True},
            "DatePlayed": "2024-01-01T00:00:00.0000000Z",
        },
        {
            "Type": "Movie",
            "Name": "Unwatched Movie",
            "ProviderIds": {"Tmdb": "999"},
            "UserData": {"Played": False},
        },
        {
            "Type": "Series",
            "Name": "Breaking Bad",
            "ProviderIds": {"Tmdb": "1396"},
            "UserData": {"PlayedPercentage": 100},
            "DatePlayed": "2024-01-01T00:00:00.0000000Z",
        },
        {
            "Type": "Series",
            "Name": "Half Watched",
            "ProviderIds": {"Tmdb": "100"},
            "UserData": {"PlayedPercentage": 50},
        },
    ],
    "TotalRecordCount": 4,
}


class ImportEmby(TestCase):
    """Test importing completed media from an Emby server."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.token = helpers.encrypt("emby-password")

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
    @patch("integrations.imports.emby.services.api_request")
    def test_import_completed_media(
        self,
        mock_api_request,
        mock_movie,
        mock_tv_with_seasons,
        mock_tv,
    ):
        """Test importing completed movies and fully-watched shows."""
        mock_api_request.side_effect = [AUTH_RESPONSE, ITEMS_RESPONSE]
        mock_movie.side_effect = self._resolve
        mock_tv.side_effect = self._resolve
        mock_tv_with_seasons.side_effect = self._tv_with_seasons

        imported_counts, warnings = emby.importer(
            "http://localhost:8096",
            self.user,
            "new",
            emby_username="emby-user",
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

    @patch("integrations.imports.emby.services.api_request")
    def test_import_strips_and_authenticates(self, mock_api_request):
        """Test the server URL is normalized and auth precedes item fetch."""
        # Empty items response means no TMDB processing, so the api_request
        # mock only handles the auth call and the (single) items fetch.
        mock_api_request.side_effect = [AUTH_RESPONSE, {"Items": [], "TotalRecordCount": 0}]

        emby.importer(
            "http://localhost:8096/",
            self.user,
            "new",
            emby_username="emby-user",
            token=self.token,
        )

        auth_call, items_call = mock_api_request.call_args_list
        # Auth uses POST to /Users/AuthenticateByName
        self.assertEqual(auth_call.args[1], "POST")
        self.assertTrue(auth_call.args[2].endswith("Users/AuthenticateByName"))
        self.assertIn("X-Emby-Authorization", auth_call.kwargs["headers"])
        # Item fetch is a GET scoped to the authenticated user id
        self.assertEqual(items_call.args[1], "GET")
        self.assertIn("/Users/user1/Items", items_call.args[2])
        self.assertEqual(items_call.kwargs["headers"]["X-Emby-Token"], "access-token")

    @patch("integrations.imports.emby.services.api_request")
    def test_import_invalid_credentials(self, mock_api_request):
        """Test that invalid credentials raise a friendly error."""
        response = Response()
        response.status_code = 401
        mock_api_request.side_effect = HTTPError(response=response)

        with self.assertRaises(helpers.MediaImportError) as context:
            emby.importer(
                "http://localhost:8096",
                self.user,
                "new",
                emby_username="emby-user",
                token=self.token,
            )

        self.assertIn("Invalid Emby server URL or credentials", str(context.exception))

    def test_is_progress_complete(self):
        """Test the fully-watched series detection helper."""
        self.assertTrue(emby._is_progress_complete({"PlayedPercentage": 100}))
        self.assertFalse(emby._is_progress_complete({"PlayedPercentage": 99.9}))
        self.assertFalse(emby._is_progress_complete({"PlayedPercentage": 50}))
        self.assertFalse(emby._is_progress_complete({}))
        self.assertFalse(emby._is_progress_complete({"PlayedPercentage": "x"}))


class ImportEmbyViewTests(TestCase):
    """Test the Emby import view."""

    def setUp(self):
        """Log in a user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.url = reverse("import_emby")

    @patch("integrations.views.tasks.import_emby.delay")
    def test_import_emby_once(self, mock_delay):
        """Test a one-time import queues the Emby import task."""
        response = self.client.post(
            self.url,
            {
                "server_url": "http://localhost:8096",
                "username": "emby-user",
                "password": "secret-password",
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
            "http://localhost:8096",
        )
        self.assertEqual(
            mock_delay.call_args.kwargs["emby_username"],
            "emby-user",
        )
        self.assertIn("password", mock_delay.call_args.kwargs)

    def test_import_emby_missing_fields(self):
        """Test that missing fields are rejected."""
        response = self.client.post(
            self.url,
            {},
        )
        self.assertRedirects(response, reverse("import_data"))

    @patch("integrations.views.helpers.create_import_schedule")
    def test_import_emby_periodic(self, mock_schedule):
        """Test a periodic import schedules the Emby import task."""
        response = self.client.post(
            self.url,
            {
                "server_url": "http://localhost:8096",
                "username": "emby-user",
                "password": "secret-password",
                "mode": "new",
                "frequency": "daily",
                "time": "09:00",
            },
        )
        self.assertRedirects(response, reverse("import_data"))
        mock_schedule.assert_called_once()
        self.assertEqual(mock_schedule.call_args.args[5], "Emby")
        self.assertEqual(
            mock_schedule.call_args.kwargs["task_kwargs"],
            {"emby_username": "emby-user"},
        )
        self.assertIn("token", mock_schedule.call_args.kwargs)
