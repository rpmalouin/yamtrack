from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from app.models import Item, MediaTypes, Movie, Sources, Status


class DedupeMediaTests(TestCase):
    """Test the dedupe_media management command."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def _movie(self, media_id, title, status):
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={
                "title": title,
                "image": "http://example.com/i.jpg",
            },
        )
        return Movie.objects.create(
            item=item,
            user=self.user,
            status=status,
        )

    def test_dedupe_keeps_highest_status(self):
        """Duplicates collapse to one row, keeping the Completed one."""
        self._movie("1", "Arrival", Status.COMPLETED.value)
        self._movie("1", "Arrival", Status.PLANNING.value)

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 2)
        call_command("dedupe_media", user="test")

        rows = Movie.objects.filter(user=self.user)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().status, Status.COMPLETED.value)

    def test_dedupe_respects_score(self):
        """The surviving row inherits a score present on a duplicate."""
        self._movie("2", "Arctic", Status.PLANNING.value)
        dup = self._movie("2", "Arctic", Status.COMPLETED.value)
        Movie.objects.filter(pk=dup.pk).update(score=8.5)

        call_command("dedupe_media", user="test")

        rows = Movie.objects.filter(user=self.user)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().status, Status.COMPLETED.value)
        self.assertEqual(rows.first().score, 8.5)
