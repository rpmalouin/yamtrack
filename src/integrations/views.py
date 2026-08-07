"""Contains views for importing and exporting media data from various sources."""

import base64
import json
import logging
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

import users
from app import helpers as app_helpers
from integrations import exports, tasks
from integrations.imports import anilist, helpers, simkl, trakt
from integrations.webhooks import emby, jellyfin, plex

logger = logging.getLogger(__name__)


@require_POST
def trakt_oauth(request):
    """View for initiating Trakt OAuth2 authorization flow."""
    redirect_uri = app_helpers.build_absolute_app_url(
        request,
        reverse("import_trakt_private"),
    )
    url = "https://trakt.tv/oauth/authorize"
    state = {
        "mode": request.POST["mode"],
        "frequency": request.POST["frequency"],
        "time": request.POST["time"],
        "redirect_uri": redirect_uri,
    }
    state_token = secrets.token_urlsafe(32)
    request.session[state_token] = state
    return redirect(
        f"{url}?{
            urlencode(
                {
                    'client_id': settings.TRAKT_API,
                    'redirect_uri': redirect_uri,
                    'response_type': 'code',
                    'state': state_token,
                }
            )
        }",
    )


@require_GET
def import_trakt_private(request):
    """View for handling Trakt OAuth2 callback and scheduling private import."""
    state_token = request.GET.get("state")
    state = request.session.get(state_token)
    if not state:
        messages.error(request, "Invalid or expired Trakt authorization request.")
        return redirect("import_data")

    if not request.GET.get("code"):
        messages.error(request, "Trakt authorization failed.")
        return redirect("import_data")

    redirect_uri = state.get("redirect_uri") or app_helpers.build_absolute_app_url(
        request,
        reverse("import_trakt_private"),
    )
    oauth_callback = trakt.handle_oauth_callback(request, redirect_uri=redirect_uri)
    enc_token = helpers.encrypt(oauth_callback["refresh_token"])

    frequency = state["frequency"]
    mode = state["mode"]
    import_time = state["time"]

    if frequency == "once":
        tasks.import_trakt.delay(
            token=enc_token,
            user_id=request.user.id,
            mode=mode,
            username=oauth_callback["username"],
            redirect_uri=redirect_uri,
        )
        messages.info(request, "The task to import media from Trakt has been queued.")
    else:
        helpers.create_import_schedule(
            oauth_callback["username"],
            request,
            mode,
            frequency,
            import_time,
            "Trakt",
            token=enc_token,
            task_kwargs={"redirect_uri": redirect_uri},
        )
    request.session.pop(state_token, None)
    return redirect("import_data")


@require_POST
def import_trakt_public(request):
    """View for importing Trakt data using public username."""
    username = request.POST.get("user")
    if not username:
        messages.error(request, "Trakt username is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]
    import_time = request.POST["time"]

    if frequency == "once":
        tasks.import_trakt.delay(
            user_id=request.user.id,
            mode=mode,
            username=username,
        )
        messages.info(request, "The task to import media from Trakt has been queued.")
    else:
        helpers.create_import_schedule(
            username=username,
            request=request,
            mode=mode,
            frequency=frequency,
            import_time=import_time,
            source="Trakt",
        )
    return redirect("import_data")


@require_POST
def simkl_oauth(request):
    """View for initiating the SIMKL OAuth2 authorization flow."""
    redirect_uri = app_helpers.build_absolute_app_url(
        request,
        reverse("import_simkl_private"),
    )
    url = "https://simkl.com/oauth/authorize"

    state = {
        "mode": request.POST["mode"],
        "frequency": request.POST["frequency"],
        "time": request.POST["time"],
    }
    state_token = secrets.token_urlsafe(32)
    request.session[state_token] = state

    return redirect(
        f"{url}?{
            urlencode(
                {
                    'client_id': settings.SIMKL_ID,
                    'redirect_uri': redirect_uri,
                    'response_type': 'code',
                    'state': state_token,
                }
            )
        }",
    )


@require_GET
def import_simkl_private(request):
    """View for getting the SIMKL OAuth2 token."""
    oauth_callback = simkl.get_token(request)
    enc_token = helpers.encrypt(oauth_callback["access_token"])
    state_token = request.GET["state"]

    frequency = request.session[state_token]["frequency"]
    mode = request.session[state_token]["mode"]
    import_time = request.session[state_token]["time"]

    if frequency == "once":
        tasks.import_simkl.delay(token=enc_token, user_id=request.user.id, mode=mode)
        messages.info(request, "The task to import media from Simkl has been queued.")
    else:
        helpers.create_import_schedule(
            oauth_callback["username"],
            request,
            mode,
            frequency,
            import_time,
            "SIMKL",
            token=enc_token,
        )

    return redirect("import_data")


@require_POST
def import_mal(request):
    """View for importing anime and manga data from MyAnimeList."""
    username = request.POST.get("user")
    if not username:
        messages.error(request, "MyAnimeList username is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]

    if frequency == "once":
        tasks.import_mal.delay(username=username, user_id=request.user.id, mode=mode)
        messages.info(
            request,
            "The task to import media from MyAnimeList has been queued.",
        )
    else:
        import_time = request.POST["time"]
        helpers.create_import_schedule(
            username,
            request,
            mode,
            frequency,
            import_time,
            "MyAnimeList",
        )
    return redirect("import_data")


@require_POST
def anilist_oauth(request):
    """Initiate AniList OAuth flow."""
    redirect_uri = app_helpers.build_absolute_app_url(
        request,
        reverse("import_anilist_private"),
    )
    url = "https://anilist.co/api/v2/oauth/authorize"
    state = {
        "mode": request.POST["mode"],
        "frequency": request.POST["frequency"],
        "time": request.POST["time"],
    }

    state_token = secrets.token_urlsafe(32)
    request.session[state_token] = state

    return redirect(
        f"{url}?{
            urlencode(
                {
                    'client_id': settings.ANILIST_ID,
                    'redirect_uri': redirect_uri,
                    'response_type': 'code',
                    'state': state_token,
                }
            )
        }",
    )


@require_GET
def import_anilist_private(request):
    """View for getting the AniList OAuth2 token."""
    oauth_callback = anilist.get_token(request)
    enc_token = helpers.encrypt(oauth_callback["access_token"])
    state_token = request.GET["state"]
    username = oauth_callback["username"]

    if not username:
        messages.error(request, "AniList username is required.")
        return redirect("import_data")

    frequency = request.session[state_token]["frequency"]
    mode = request.session[state_token]["mode"]
    import_time = request.session[state_token]["time"]

    if frequency == "once":
        tasks.import_anilist.delay(
            user_id=request.user.id,
            mode=mode,
            username=username,
            token=enc_token,
        )
        messages.info(request, "AniList import queued.")
    else:
        helpers.create_import_schedule(
            username=username,
            request=request,
            mode=mode,
            frequency=frequency,
            import_time=import_time,
            source="AniList",
            token=enc_token,
        )
    return redirect("import_data")


@require_POST
def import_anilist_public(request):
    """View for importing anime and manga data from AniList."""
    username = request.POST.get("user")
    if not username:
        messages.error(request, "AniList username is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]
    import_time = request.POST["time"]

    if frequency == "once":
        tasks.import_anilist.delay(
            user_id=request.user.id,
            mode=mode,
            username=username,
        )
        messages.info(request, "AniList import queued.")
    else:
        helpers.create_import_schedule(
            username=username,
            request=request,
            mode=mode,
            frequency=frequency,
            import_time=import_time,
            source="AniList",
        )
    return redirect("import_data")


@require_POST
def import_kitsu(request):
    """View for importing anime and manga data from Kitsu by user ID."""
    kitsu_id = request.POST.get("user")
    if not kitsu_id:
        messages.error(request, "Kitsu user ID is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]

    if frequency == "once":
        tasks.import_kitsu.delay(username=kitsu_id, user_id=request.user.id, mode=mode)
        messages.info(request, "The task to import media from Kitsu has been queued.")
    else:
        import_time = request.POST["time"]
        helpers.create_import_schedule(
            kitsu_id,
            request,
            mode,
            frequency,
            import_time,
            "Kitsu",
        )
    return redirect("import_data")


@require_POST
def import_plex(request):
    """View for importing completed media from a Plex server."""
    saved_url, saved_token = helpers.get_user_plex_connection(request.user)
    server_url = request.POST.get("server_url", "").strip() or saved_url
    plex_token = request.POST.get("token", "").strip() or saved_token

    if not server_url or not plex_token:
        messages.error(request, "Plex server URL and token are required.")
        return redirect("import_data")

    # Persist so the user doesn't have to re-enter them next time.
    helpers.save_user_plex_connection(request.user, server_url, plex_token)

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]
    import_time = request.POST["time"]

    enc_token = helpers.encrypt(plex_token)

    if frequency == "once":
        tasks.import_plex.delay(
            user_id=request.user.id,
            mode=mode,
            username=server_url,
            token=enc_token,
        )
        messages.info(request, "The task to import media from Plex has been queued.")
    else:
        helpers.create_import_schedule(
            server_url,
            request,
            mode,
            frequency,
            import_time,
            "Plex",
            token=enc_token,
        )
    return redirect("import_data")


@require_POST
def import_emby(request):
    """View for importing completed media from an Emby server."""
    server_url = request.POST.get("server_url", "").strip()
    emby_username = request.POST.get("username", "").strip()
    emby_password = request.POST.get("password", "").strip()

    if not server_url or not emby_username or not emby_password:
        messages.error(request, "Emby server URL, username and password are required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]
    import_time = request.POST["time"]

    enc_password = helpers.encrypt(emby_password)

    if frequency == "once":
        tasks.import_emby.delay(
            user_id=request.user.id,
            mode=mode,
            username=server_url,
            emby_username=emby_username,
            password=enc_password,
        )
        messages.info(request, "The task to import media from Emby has been queued.")
    else:
        helpers.create_import_schedule(
            server_url,
            request,
            mode,
            frequency,
            import_time,
            "Emby",
            token=enc_password,
            task_kwargs={"emby_username": emby_username},
        )
    return redirect("import_data")


@require_POST
def import_plex_unwatched(request):
    """View for importing unwatched media from a Plex server."""
    saved_url, saved_token = helpers.get_user_plex_connection(request.user)
    server_url = request.POST.get("server_url", "").strip() or saved_url
    plex_token = request.POST.get("token", "").strip() or saved_token

    if not server_url or not plex_token:
        messages.error(request, "Plex server URL and token are required.")
        return redirect("unwatched")

    # Persist so the user doesn't have to re-enter them next time.
    helpers.save_user_plex_connection(request.user, server_url, plex_token)

    enc_token = helpers.encrypt(plex_token)
    tasks.import_plex_unwatched.delay(
        user_id=request.user.id,
        mode="new",
        username=server_url,
        token=enc_token,
    )
    messages.info(request, "The task to import unwatched media from Plex has been queued.")
    return redirect("unwatched")


@require_POST
def import_yamtrack(request):
    """View for importing anime and manga data from Yamtrack CSV."""
    file = request.FILES.get("yamtrack_csv")

    if not file:
        messages.error(request, "Yamtrack CSV file is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    file_content = base64.b64encode(file.read()).decode("ascii")
    tasks.import_yamtrack.delay(
        file_content=file_content,
        user_id=request.user.id,
        mode=mode,
    )
    messages.info(
        request,
        "The task to import media from Yamtrack CSV file has been queued.",
    )
    return redirect("import_data")


@require_POST
def import_hltb(request):
    """View for importing game date from HowLongToBeat."""
    file = request.FILES.get("hltb_csv")

    if not file:
        messages.error(request, "HowLongToBeat CSV file is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    file_content = base64.b64encode(file.read()).decode("ascii")
    tasks.import_hltb.delay(
        file_content=file_content,
        user_id=request.user.id,
        mode=mode,
    )
    messages.info(
        request,
        "The task to import media from HowLongToBeat CSV file has been queued.",
    )
    return redirect("import_data")


@require_POST
def import_steam(request):
    """View for importing game data from Steam."""
    steam_id = request.POST.get("user")
    if not steam_id:
        messages.error(request, "Steam ID is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    frequency = request.POST["frequency"]

    if frequency == "once":
        tasks.import_steam.delay(username=steam_id, user_id=request.user.id, mode=mode)
        messages.info(request, "The task to import media from Steam has been queued.")
    else:
        import_time = request.POST["time"]
        helpers.create_import_schedule(
            steam_id,
            request,
            mode,
            frequency,
            import_time,
            "Steam",
        )
    return redirect("import_data")


@require_POST
def import_imdb(request):
    """View for importing data from IMDB."""
    file = request.FILES.get("imdb_csv")

    if not file:
        messages.error(request, "IMDB CSV file is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    file_content = base64.b64encode(file.read()).decode("ascii")
    tasks.import_imdb.delay(
        file_content=file_content,
        user_id=request.user.id,
        mode=mode,
    )
    messages.info(
        request,
        "The task to import media from IMDB CSV file has been queued.",
    )
    return redirect("import_data")


@require_POST
def import_goodreads(request):
    """View for importing books data from GoodReads CSV."""
    file = request.FILES.get("goodreads_csv")

    if not file:
        messages.error(request, "GoodReads CSV file is required.")
        return redirect("import_data")

    mode = request.POST["mode"]
    file_content = base64.b64encode(file.read()).decode("ascii")
    tasks.import_goodreads.delay(
        file_content=file_content,
        user_id=request.user.id,
        mode=mode,
    )
    messages.info(
        request,
        "The task to import media from GoodReads CSV file has been queued.",
    )
    return redirect("import_data")


@require_GET
def export_csv(request):
    """View for exporting all media data to a CSV file."""
    now = timezone.localtime()
    response = StreamingHttpResponse(
        streaming_content=exports.generate_rows(request.user),
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="yamtrack_{now}.csv"'},
    )
    logger.info("User %s started CSV export", request.user.username)
    return response


@login_not_required
@csrf_exempt
@require_POST
def jellyfin_webhook(request, token):
    """Handle Jellyfin webhook notifications for media playback."""
    try:
        user = users.models.User.objects.get(token=token)
    except ObjectDoesNotExist:
        logger.warning(
            "Could not process Jellyfin webhook: Invalid token: %s",
            token,
        )
        return HttpResponse(status=401)

    # Attach User instance so history_user_id is populated
    request.user = user
    data = request.body
    if not data:
        logger.warning("Missing payload in Jellyfin webhook request")
        return HttpResponse("Missing payload", status=400)

    payload = json.loads(data)
    processor = jellyfin.JellyfinWebhookProcessor()
    processor.process_payload(payload, user)
    return HttpResponse(status=200)


@login_not_required
@csrf_exempt
@require_POST
def plex_webhook(request, token):
    """Handle Plex webhook notifications for media playback."""
    try:
        user = users.models.User.objects.get(token=token)
    except ObjectDoesNotExist:
        logger.warning(
            "Could not process Plex webhook: Invalid token: %s",
            token,
        )
        return HttpResponse(status=401)

    # Attach User instance so history_user_id is populated
    request.user = user

    # https://support.plex.tv/hc/en-us/articles/115002267687-Webhooks
    # As stated above, the payload is sent in JSON format inside a multipart
    # HTTP POST request. For the media.play and media.rate events, a second part of
    # the POST request contains a JPEG thumbnail for the media.

    data = request.POST.get("payload")
    if not data:
        logger.warning("Missing payload in Plex webhook request")
        return HttpResponse("Missing payload", status=400)

    payload = json.loads(data)
    processor = plex.PlexWebhookProcessor()
    processor.process_payload(payload, user)
    return HttpResponse(status=200)


@login_not_required
@csrf_exempt
@require_POST
def emby_webhook(request, token):
    """Handle Emby webhook notifications for media playback."""
    try:
        user = users.models.User.objects.get(token=token)
    except ObjectDoesNotExist:
        logger.warning(
            "Could not process Emby webhook: Invalid token: %s",
            token,
        )
        return HttpResponse(status=401)

    # Attach User instance so history_user_id is populated
    request.user = user

    # The payload is sent in JSON format inside a multipart
    # HTTP POST request.

    data = request.POST.get("data")
    if not data:
        logger.warning("Missing payload in Emby webhook request")
        return HttpResponse("Missing payload", status=400)

    payload = json.loads(data)
    processor = emby.EmbyWebhookProcessor()
    processor.process_payload(payload, user)
    return HttpResponse(status=200)
