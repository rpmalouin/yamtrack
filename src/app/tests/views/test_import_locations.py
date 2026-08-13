import json
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from app.management.commands.import_locations import _normalize, load_clz_locations
from app.models import TV, Item, MediaTypes, Movie, Sources, Status


def _mkjson(entries):
    """Write movie entries to a temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"movies": entries}, handle)
    return path


class ImportLocationsTests(TestCase):
    """Test the import_locations management command."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def _media(self, media_type, media_id, title, model):
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=media_type,
            defaults={"title": title, "image": "http://example.com/i.jpg"},
        )
        return model.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
        )

    def _movie(self, media_id, title):
        return self._media(
            MediaTypes.MOVIE.value, media_id, title, Movie
        )

    def _tv(self, media_id, title):
        return self._media(MediaTypes.TV.value, media_id, title, TV)

    def test_matches_by_tmdb_id(self):
        """A movie whose CLZ entry carries the tmdb id gets the location."""
        self._movie("100", "Dune")
        path = _mkjson(
            [
                {
                    "title": "Dune",
                    "location": "A-132",
                    "links": [{"url": "http://www.themoviedb.org/movie/100"}],
                }
            ]
        )
        call_command("import_locations", path)

        self.assertEqual(Movie.objects.get(user=self.user).location, "A-132")

    def test_joins_multiple_copies(self):
        """Multiple CLZ entries resolving to one movie are joined."""
        self._movie("100", "Dune")
        links = [{"url": "http://www.themoviedb.org/movie/100"}]
        path = _mkjson(
            [
                {"title": "Dune", "location": "A-132", "links": links},
                {"title": "Dune", "location": "A-131", "links": links},
                {"title": "Dune", "location": "A-111", "links": links},
            ]
        )
        call_command("import_locations", path)

        self.assertEqual(
            Movie.objects.get(user=self.user).location, "A-111, A-131, A-132"
        )

    def test_title_fallback_when_no_tmdb_link(self):
        """An item without a TMDB link is located by normalized title."""
        self._movie("100", "Mists of Avalon")
        path = _mkjson(
            [
                {
                    "title": "Mists Of Avalon, The",
                    "sorttitle": "Mists Of Avalon",
                    "location": "A-146",
                }
            ]
        )
        call_command("import_locations", path)

        self.assertEqual(Movie.objects.get(user=self.user).location, "A-146")

    def test_skip_title_flag_requires_tmdb(self):
        """With --skip-title, title-only entries produce no location."""
        self._movie("100", "Mists of Avalon")
        path = _mkjson(
            [
                {
                    "title": "Mists Of Avalon, The",
                    "sorttitle": "Mists Of Avalon",
                    "location": "A-146",
                }
            ]
        )
        call_command("import_locations", path, skip_title=True)

        self.assertEqual(Movie.objects.get(user=self.user).location, "")

    def test_series_match_tv_not_movie(self):
        """A CLZ series entry locates the TV row, not a Movie row."""
        self._tv("9156", "Children of Dune")
        path = _mkjson(
            [
                {
                    "title": "Children Of Dune",
                    "is_tv_series": True,
                    "location": "A-273, A-274",
                    "links": [{"url": "http://www.themoviedb.org/tv/9156"}],
                }
            ]
        )
        call_command("import_locations", path)

        self.assertEqual(TV.objects.get(user=self.user).location, "A-273, A-274")
        self.assertFalse(Movie.objects.filter(user=self.user).exists())

    def test_unmatched_item_gets_no_location(self):
        """An item absent from the export keeps an empty location."""
        self._movie("999", "Citizen Kane")
        path = _mkjson(
            [{"title": "Something Else", "location": "A-001"}]
        )
        call_command("import_locations", path)

        self.assertEqual(Movie.objects.get(user=self.user).location, "")

    def test_dry_run_makes_no_changes(self):
        """--dry-run reports the write without persisting it."""
        self._movie("100", "Dune")
        path = _mkjson(
            [
                {
                    "title": "Dune",
                    "location": "A-132",
                    "links": [{"url": "http://www.themoviedb.org/movie/100"}],
                }
            ]
        )
        call_command("import_locations", path, dry_run=True)

        self.assertEqual(Movie.objects.get(user=self.user).location, "")

    def test_normalize_strips_articles_and_punctuation(self):
        """Normalization aligns 'The Mists of Avalon' with 'Mists Of Avalon'."""
        self.assertEqual(_normalize("The Mists of Avalon"), "mists of avalon")
        self.assertEqual(_normalize("Mists Of Avalon, The"), "mists of avalon the")

    def test_load_clz_locations_indexes_series_separately(self):
        """Movies and series are indexed under distinct keys, deduped by set."""
        tmdb, title = load_clz_locations(
            _mkjson(
                [
                    {
                        "title": "Dune",
                        "location": "A-132",
                        "links": [{"url": "http://www.themoviedb.org/movie/100"}],
                    },
                    {
                        "title": "Caprica",
                        "is_tv_series": True,
                        "location": "A-111",
                        "links": [{"url": "http://www.themoviedb.org/tv/200"}],
                    },
                ]
            )
        )
        self.assertEqual(tmdb["movie"]["100"], {"A-132"})
        self.assertEqual(tmdb["tv"]["200"], {"A-111"})
        self.assertEqual(
            title["movie"][_normalize("Dune")], {"A-132"}
        )
