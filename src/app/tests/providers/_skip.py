"""Skip guards for live provider API tests.

Tests in the ``providers`` suite that exercise the real third-party APIs fail
on public CI, where GitHub secrets (and therefore the provider API keys) are
not available. Decorating those tests with ``requires`` skips them when the
relevant key is missing, so public builds stay green while authenticated runs
still exercise the live endpoints.
"""

from unittest import skipIf

from django.conf import settings


def requires(key, provider_label, *, check=None):
    """Return a ``skipIf`` decorator for a live test that needs a provider key.

    Args:
        key: The settings attribute name holding the key (e.g. ``"TMDB_API"``).
        provider_label: Human-readable provider name used in the skip message.
        check: Optional callable receiving ``settings`` and returning whether
            the key is configured; used for providers needing multiple keys.
    """

    def _configured():
        if check is not None:
            return check(settings)
        return bool(getattr(settings, key, ""))

    return skipIf(not _configured(), f"{provider_label} API key not configured")
