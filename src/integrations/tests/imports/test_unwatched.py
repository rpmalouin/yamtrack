from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import TV, Item, MediaTypes, Movie, Sources, Status
from integrations.imports import helpers


def _make_item(media_id, media_type, title="Test Title"):
    return Item.objects.create(
        media_id=media_id,
        source=Sources.TMDB.value,
        media_type=media_type,
        title=title,
        image="http://example.com/img.jpg",
    )


class UnwatchedPageTests(TestCase):
    """Test the Unwatched review page."""

    def setUp(self):
        """Create a user with Unwatched, Completed, and TV items."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.url = reverse("unwatched")

        self.unwatched_movie = Movie.objects.create(
            item=_make_item("1", MediaTypes.MOVIE.value, "Unwatched Movie"),
            user=self.user,
            status=Status.UNWATCHED.value,
        )
        Movie.objects.create(
            item=_make_item("2", MediaTypes.MOVIE.value, "Done Movie"),
            user=self.user,
            status=Status.COMPLETED.value,
        )
        self.unwatched_tv = TV.objects.create(
            item=_make_item("3", MediaTypes.TV.value, "Partial Show"),
            user=self.user,
            status=Status.UNWATCHED.value,
        )

    def test_page_shows_only_unwatched(self):
        """The page lists only Unwatched items."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        rendered = response.content.decode()
        self.assertIn("Unwatched Movie", rendered)
        self.assertIn("Partial Show", rendered)
        self.assertNotIn("Done Movie", rendered)

    def test_kind_filter(self):
        """The kind filter limits results to movies or TV."""
        response = self.client.get(self.url, {"kind": "movie"})
        rendered = response.content.decode()
        self.assertIn("Unwatched Movie", rendered)
        self.assertNotIn("Partial Show", rendered)

    def test_pagination(self):
        """The list paginates instead of loading everything."""
        for i in range(40):
            Movie.objects.create(
                item=_make_item(str(100 + i), MediaTypes.MOVIE.value, f"Item {i}"),
                user=self.user,
                status=Status.UNWATCHED.value,
            )
        response = self.client.get(self.url, {"page": 2})
        self.assertEqual(response.status_code, 200)
        # Item 0 is on page 1, should not appear on page 2
        self.assertNotIn("Item 0", response.content.decode())


class UnwatchedCompleteTests(TestCase):
    """Test marking an Unwatched item as Completed."""

    def setUp(self):
        """Create a user with an Unwatched movie and TV show."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.models.providers.services.get_media_metadata")
    def test_complete_movie_with_rating(self, mock_metadata):
        """Completing a movie sets status, score, and progress."""
        mock_metadata.return_value = {"max_progress": 1, "title": "x", "image": "x"}
        movie = Movie.objects.create(
            item=_make_item("1", MediaTypes.MOVIE.value, "Unwatched Movie"),
            user=self.user,
            status=Status.UNWATCHED.value,
        )
        response = self.client.post(
            reverse("unwatched_complete", args=["movie", movie.id]),
            {"score": "8.5", "kind": "all", "page": "1"},
        )
        self.assertRedirects(response, f"{reverse('unwatched')}?kind=all&page=1")

        movie.refresh_from_db()
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(float(movie.score), 8.5)
        self.assertEqual(movie.progress, 1)

    @patch("app.models.providers.services.get_media_metadata")
    def test_complete_tv_without_rating(self, mock_metadata):
        """TV completion only sets status/score; end_date is a computed property."""
        mock_metadata.return_value = {
            "max_progress": 1,
            "title": "x",
            "image": "x",
            "related": {"seasons": []},
        }
        tv = TV.objects.create(
            item=_make_item("3", MediaTypes.TV.value, "Partial Show"),
            user=self.user,
            status=Status.UNWATCHED.value,
        )
        response = self.client.post(
            reverse("unwatched_complete", args=[MediaTypes.TV.value, tv.id]),
            {"score": "", "kind": "everything", "page": "3"},
        )
        self.assertRedirects(
            response,
            f"{reverse('unwatched')}?kind=everything&page=3",
        )

        tv.refresh_from_db()
        self.assertEqual(tv.status, Status.COMPLETED.value)

    def test_complete_other_users_media_forbidden(self):
        """Completing another user's media is rejected with 404."""
        other = get_user_model().objects.create_user("other", "12345")
        movie = Movie.objects.create(
            item=_make_item("5", MediaTypes.MOVIE.value, "Their Movie"),
            user=other,
            status=Status.UNWATCHED.value,
        )
        response = self.client.post(
            reverse("unwatched_complete", args=["movie", movie.id]),
            {"score": "5"},
        )
        self.assertEqual(response.status_code, 404)

        movie.refresh_from_db()
        self.assertEqual(movie.status, Status.UNWATCHED.value)


class ImportPlexUnwatchedViewTests(TestCase):
    """Test the Plex unwatched sync view."""

    def setUp(self):
        """Log in a user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.url = reverse("import_plex_unwatched")

    @patch("integrations.views.tasks.import_plex_unwatched.delay")
    def test_sync_queues_task(self, mock_delay):
        """A valid sync queues the unwatched import task."""
        response = self.client.post(
            self.url,
            {
                "server_url": "http://localhost:32400",
                "token": "secret-token",
            },
        )
        self.assertRedirects(response, reverse("unwatched"))
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.kwargs["username"], "http://localhost:32400")
        self.assertEqual(mock_delay.call_args.kwargs["mode"], "new")

    def test_sync_missing_fields(self):
        """Missing server URL or token is rejected."""
        response = self.client.post(self.url, {})
        self.assertRedirects(response, reverse("unwatched"))

    @patch("integrations.views.tasks.import_plex_unwatched.delay")
    def test_sync_persists_connection(self, _mock_delay):
        """A valid sync persists the server URL and encrypted token."""
        self.client.post(
            self.url,
            {
                "server_url": "http://172.21.0.1:32421",
                "token": "my-secret-token",
            },
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.plex_server_url, "http://172.21.0.1:32421")
        self.assertNotEqual(self.user.plex_api_token, "my-secret-token")
        saved_url, saved_token = helpers.get_user_plex_connection(self.user)
        self.assertEqual(saved_url, "http://172.21.0.1:32421")
        self.assertEqual(saved_token, "my-secret-token")

    @patch("integrations.views.tasks.import_plex_unwatched.delay")
    def test_sync_reuses_saved_connection(self, mock_delay):
        """Blank fields reuse the saved server URL and token."""
        helpers.save_user_plex_connection(
            self.user,
            "http://saved:32400",
            "saved-token",
        )
        response = self.client.post(self.url, {"server_url": "", "token": ""})
        self.assertRedirects(response, reverse("unwatched"))
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.kwargs["username"], "http://saved:32400")


class PlexConnectionHelperTests(TestCase):
    """Test get/save Plex connection helpers."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def test_round_trip(self):
        """Saved values round-trip through encryption."""
        helpers.save_user_plex_connection(
            self.user,
            "http://172.21.0.1:32421",
            "abc123",
        )
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.plex_api_token, "abc123")
        self.assertEqual(
            helpers.get_user_plex_connection(self.user),
            ("http://172.21.0.1:32421", "abc123"),
        )

    def test_empty_returns_empty(self):
        """No saved connection returns empty values."""
        self.assertEqual(helpers.get_user_plex_connection(self.user), ("", ""))
