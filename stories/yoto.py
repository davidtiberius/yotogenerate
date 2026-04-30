import hashlib
import base64
import secrets
import time
import urllib.parse
import urllib.request
import urllib.error
import json
from datetime import timedelta

from django.conf import settings
from django.utils import timezone


YOTO_AUTH_URL = "https://login.yotoplay.com/authorize"
YOTO_TOKEN_URL = "https://login.yotoplay.com/oauth/token"
YOTO_API_BASE = "https://api.yotoplay.com"
YOTO_SCOPES = "user:content:manage"


def generate_pkce():
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_authorize_url(client_id, redirect_uri, state, code_challenge):
    params = {
        "audience": "https://api.yotoplay.com",
        "scope": YOTO_SCOPES,
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{YOTO_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _post_form(url, data):
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def exchange_code(client_id, code, code_verifier, redirect_uri):
    return _post_form(YOTO_TOKEN_URL, {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    })


def refresh_access_token(client_id, refresh_token):
    return _post_form(YOTO_TOKEN_URL, {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    })


class YotoTokenExpired(Exception):
    pass


def save_tokens(user, token_data):
    from .models import YotoAccount
    expires_in = token_data.get("expires_in", 3600)
    expires_at = timezone.now() + timedelta(seconds=expires_in)
    YotoAccount.objects.update_or_create(
        user=user,
        defaults={
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_at": expires_at,
        },
    )


def get_valid_token(yoto_account):
    """Return a valid access token, refreshing if possible or raising YotoTokenExpired."""
    if not yoto_account.is_expired():
        return yoto_account.access_token
    if yoto_account.refresh_token:
        token_data = refresh_access_token(
            settings.YOTO_CLIENT_ID,
            yoto_account.refresh_token,
        )
        save_tokens(yoto_account.user, token_data)
        yoto_account.refresh_from_db()
        return yoto_account.access_token
    raise YotoTokenExpired("Yoto session expired — please reconnect your account.")


def _api_get(token, path, params=None):
    url = f"{YOTO_API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _api_post(token, path, body):
    url = f"{YOTO_API_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _put_file(upload_url, file_bytes, content_type="audio/mpeg"):
    req = urllib.request.Request(
        upload_url,
        data=file_bytes,
        method="PUT",
        headers={"Content-Type": content_type},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status


def get_upload_url(token, sha256, filename):
    return _api_get(token, "/media/transcode/audio/uploadUrl", {
        "sha256": sha256,
        "filename": filename,
    })


def poll_transcoding(token, upload_id, max_attempts=60, interval=2):
    """Poll until transcoding completes. Returns transcode data or raises."""
    for _ in range(max_attempts):
        data = _api_get(
            token,
            f"/media/upload/{upload_id}/transcoded",
            {"loudnorm": "false"},
        )
        transcode = data.get("transcode", {})
        if transcode.get("transcodedSha256"):
            return transcode
        time.sleep(interval)
    raise TimeoutError("Yoto transcoding timed out")


def create_yoto_content(token, title, transcode):
    media_info = transcode.get("transcodedInfo", {})
    duration = media_info.get("duration")
    file_size = media_info.get("fileSize")
    channels = media_info.get("channels")
    fmt = media_info.get("format", "mp3")

    readable_mb = round((file_size / 1024 / 1024) * 10) / 10 if file_size else None

    chapters = [
        {
            "key": "01",
            "title": title,
            "overlayLabel": "1",
            "tracks": [
                {
                    "key": "01",
                    "title": title,
                    "trackUrl": f"yoto:#{transcode['transcodedSha256']}",
                    "duration": duration,
                    "fileSize": file_size,
                    "channels": channels,
                    "format": fmt,
                    "type": "audio",
                    "overlayLabel": "1",
                    "display": {
                        "icon16x16": "yoto:#aUm9i3ex3qqAMYBv-i-O-pYMKuMJGICtR3Vhf289u2Q",
                    },
                }
            ],
        }
    ]

    body = {
        "title": title,
        "content": {"chapters": chapters},
        "metadata": {
            "media": {
                "duration": duration,
                "fileSize": file_size,
                "readableFileSize": readable_mb,
            }
        },
    }
    return _api_post(token, "/content", body)


def push_audio_to_yoto(yoto_account, audio_path, title):
    """Full upload flow: upload MP3 → transcode → create card. Returns card data."""
    token = get_valid_token(yoto_account)

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    sha256 = hashlib.sha256(audio_bytes).hexdigest()
    filename = f"{title}.mp3"

    upload_data = get_upload_url(token, sha256, filename)
    upload = upload_data.get("upload", {})
    upload_id = upload["uploadId"]
    upload_url = upload.get("uploadUrl")

    if upload_url:
        _put_file(upload_url, audio_bytes)

    transcode = poll_transcoding(token, upload_id)
    card = create_yoto_content(token, title, transcode)
    return card
