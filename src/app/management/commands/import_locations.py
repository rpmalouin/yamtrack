"""Import physical shelf locations from a CLZ Movies JSON export.

Matches entries in a CLZ movies.json (which carry a ``location`` like
"A-376") to media the user already tracks in Yamtrack, and writes that
location onto the matching Movie/TV rows.

Matching is done in two passes:
  1. Primary: exact TMDB id.  The CLZ export's ``links`` contain
     ``themoviedb.org/movie/<id>`` entries; every media item Yamtrack tracks
     from the ``tmdb`` source stores that same id in ``Item.media_id``.
  2. Fallback: normalized title (+ year when available) for items with no
     TMDB link or whose id does not match a tracked item.

Physical copies are handled: when several CLZ entries resolve to the same
tracked item (e.g. Dune on discs 131 and 132, or a show split across
multiple disk entries), their locations are joined into a single field like
``"A-131, A-132"``.

Series entries in the export (``is_tv_series``) are matched against TV rows;
everything else is matched against Movie rows.

Run with ``--dry-run`` first to review what would change.
"""

import json
import logging
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from django.apps import apps
from django.core.management.base import BaseCommand

from app.models import Item, MediaTypes

logger = logging.getLogger(__name__)

_TMDB_LINK = re.compile(r"themoviedb\.org/(?:movie|tv)/(\d+)")

# Normalization: strip articles/punctuation/whitespace case differences.
_ARTICLES = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_PUNCT = re.compile(r"[^a-z0-9]+")


def _normalize(value):
    """Lowercase, strip accents/punctuation and leading articles for matching."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = _PUNCT.sub(" ", text).strip()
    return _ARTICLES.sub("", text).strip()


def _tmdb_ids(entry):
    """Return the set of TMDB ids referenced by a CLZ entry's links."""
    ids = set()
    for link in entry.get("links") or []:
        match = _TMDB_LINK.search(link.get("url", ""))
        if match:
            ids.add(match.group(1))
    return ids


def _entry_title(entry):
    """Use the sort title when present (handles 'Mists Of Avalon, The')."""
    return entry.get("sorttitle") or entry.get("title") or ""


def load_clz_locations(path):
    """Load movies.json and return (locations_by_tmdb, locations_by_title).

    ``locations_by_tmdb`` maps a TMDB id -> set of locations.
    ``locations_by_title`` maps a normalized title -> set of locations.
    Series entries (is_tv_series) are indexed under ``tv``; the rest under
    ``movie``.  The title index is keyed by normalized title alone (not year)
    so items whose TMDB id did not match can still be located by name.
    """
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)

    tmdb_index = {"movie": defaultdict(set), "tv": defaultdict(set)}
    title_index = {"movie": defaultdict(set), "tv": defaultdict(set)}

    for entry in data.get("movies", []):
        location = (entry.get("location") or "").strip()
        if not location:
            continue

        is_series = bool(entry.get("is_tv_series"))
        key = "tv" if is_series else "movie"

        for tmdb_id in _tmdb_ids(entry):
            tmdb_index[key][tmdb_id].add(location)
        title_index[key][_normalize(_entry_title(entry))].add(location)

    return tmdb_index, title_index


class Command(BaseCommand):
    """Backfill ``location`` on tracked media from a CLZ export."""

    help = "Set physical locations on tracked Media from a CLZ movies.json export."

    def add_arguments(self, parser):
        """Define the command-line arguments."""
        parser.add_argument("json", nargs="?", default="movies.json")
        parser.add_argument(
            "--user",
            action="store",
            help="Only update media tracked by this username.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be updated without changing anything.",
        )
        parser.add_argument(
            "--skip-title",
            action="store_true",
            help="Only use exact TMDB id matching (no title fallback).",
        )

    def handle(self, *_args, **options):
        """Run the location import."""
        user_filter = options.get("user")
        dry_run = options["dry_run"]
        skip_title = options["skip_title"]

        tmdb_index, title_index = load_clz_locations(options["json"])

        updated = 0
        unmatched_items = []

        for media_type, index_key in (
            (MediaTypes.MOVIE.value, "movie"),
            (MediaTypes.TV.value, "tv"),
        ):
            model = apps.get_model(app_label="app", model_name=media_type)

            items = Item.objects.filter(source="tmdb", media_type=media_type)

            for item in items:
                media_rows = model.objects.filter(item=item)
                if user_filter:
                    media_rows = media_rows.filter(user__username=user_filter)
                if not media_rows.exists():
                    # Not tracked in the selected scope; nothing to locate.
                    continue

                locations = set(tmdb_index[index_key].get(item.media_id, []))

                if not locations and not skip_title:
                    locations = set(
                        title_index[index_key].get(_normalize(item.title), [])
                    )

                joined = ", ".join(sorted(locations))

                if joined:
                    updated += 1
                    if not dry_run:
                        media_rows.update(location=joined)
                    self.stdout.write(
                        f"{'[dry]' if dry_run else '[upd]'} {media_type}: "
                        f"{item.title!r} (tmdb {item.media_id}) "
                        f"-> {joined!r}"
                    )
                else:
                    unmatched_items.append(f"{item.title} (tmdb {item.media_id})")

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Would update' if dry_run else 'Updated'} {updated} item(s) "
                f"with a matched location."
            )
        )
        if unmatched_items:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(unmatched_items)} tracked item(s) matched no location:"
                )
            )
            for name in unmatched_items:
                self.stdout.write(f"  - {name}")
