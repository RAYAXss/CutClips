# core/uploader.py
"""
Upload engine — handles posting videos to TikTok, YouTube Shorts,
Instagram Reels, and Reddit using their official APIs.

Each platform authenticator reads credentials from environment variables
(loaded from .env by main.py at startup).
"""

import os
import json
import time
import datetime
import mimetypes
from pathlib import Path


# ══════════════════════════════════════════════════════
#  TEMPLATE RENDERING
# ══════════════════════════════════════════════════════

def render_template(template: str, platform: str, video_path: str) -> str:
    """Replace template variables with real values."""
    now = datetime.datetime.now()
    name = Path(video_path).stem
    return (template
        .replace("{{date}}", now.strftime("%Y-%m-%d"))
        .replace("{{time}}", now.strftime("%H:%M"))
        .replace("{{weekday}}", now.strftime("%A"))
        .replace("{{platform}}", platform)
        .replace("{{filename}}", name))


# ══════════════════════════════════════════════════════
#  TIKTOK
# ══════════════════════════════════════════════════════

def upload_tiktok(video_path: str, title: str, log) -> bool:
    """
    Send a video to TikTok as a draft (inbox) with the account linked in
    Comptes (core/tiktok.py). Unattended jobs never publish directly: TikTok
    requires the user to choose privacy and confirm in the UI for that.
    """
    from core import tiktok
    return tiktok.upload(video_path, log, mode="inbox") is not None


# ══════════════════════════════════════════════════════
#  YOUTUBE SHORTS
# ══════════════════════════════════════════════════════

def upload_youtube(video_path: str, title: str, description: str, log) -> bool:
    """
    Upload a video to YouTube as a Short using the YouTube Data API v3.

    Required env vars:
        YOUTUBE_CLIENT_ID
        YOUTUBE_CLIENT_SECRET
        YOUTUBE_REFRESH_TOKEN  ← obtained via OAuth flow (see /schedule/setup)

    Docs: https://developers.google.com/youtube/v3/guides/uploading_a_video
    """
    import requests

    client_id     = os.getenv("YOUTUBE_CLIENT_ID", "")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN", "")

    if not all([client_id, client_secret, refresh_token]):
        log("YouTube: Missing credentials in .env (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)", "error")
        return False

    try:
        # Step 1 — Refresh access token
        log("YouTube: Refreshing access token...", "info")
        token_resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            },
            timeout=10
        )
        if token_resp.status_code != 200:
            log(f"YouTube: Token refresh failed — {token_resp.text[:200]}", "error")
            return False

        access_token = token_resp.json().get("access_token", "")

        # Step 2 — Resumable upload init
        log("YouTube: Initiating upload...", "info")
        file_size = os.path.getsize(video_path)

        metadata = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": ["shorts", "clips"],
                "categoryId": "24"  # Entertainment
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }

        init_resp = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos"
            "?uploadType=resumable&part=snippet,status",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(file_size)
            },
            json=metadata,
            timeout=15
        )

        if init_resp.status_code != 200:
            log(f"YouTube: Init failed [{init_resp.status_code}] {init_resp.text[:200]}", "error")
            return False

        upload_url = init_resp.headers.get("Location", "")
        if not upload_url:
            log("YouTube: No upload URL returned", "error")
            return False

        # Step 3 — Upload file
        log("YouTube: Uploading video...", "info")
        with open(video_path, "rb") as f:
            upload_resp = requests.put(
                upload_url,
                headers={
                    "Content-Length": str(file_size),
                    "Content-Type": "video/mp4"
                },
                data=f,
                timeout=300
            )

        if upload_resp.status_code in (200, 201):
            video_id = upload_resp.json().get("id", "?")
            log(f"YouTube: Published! https://youtube.com/shorts/{video_id}", "success")
            return True

        log(f"YouTube: Upload failed [{upload_resp.status_code}]", "error")
        return False

    except Exception as e:
        log(f"YouTube upload error: {e}", "error")
        return False


# ══════════════════════════════════════════════════════
#  INSTAGRAM REELS
# ══════════════════════════════════════════════════════

def upload_instagram(video_path: str, caption: str, log) -> bool:
    """
    Upload a Reel to Instagram using the Instagram Graph API.

    Required env vars:
        INSTAGRAM_USER_ID       ← your Instagram Business/Creator account ID
        INSTAGRAM_ACCESS_TOKEN  ← long-lived token from Meta Developer dashboard

    Note: Video must be publicly accessible via URL for Meta to fetch it.
    We upload to a temp host first (or you can use your own server).

    Docs: https://developers.facebook.com/docs/instagram-api/guides/reels
    """
    import requests

    user_id      = os.getenv("INSTAGRAM_USER_ID", "")
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")

    if not user_id or not access_token:
        log("Instagram: Missing INSTAGRAM_USER_ID or INSTAGRAM_ACCESS_TOKEN in .env", "error")
        return False

    video_url = os.getenv("INSTAGRAM_VIDEO_HOST_URL", "")
    if not video_url:
        log("Instagram: INSTAGRAM_VIDEO_HOST_URL not set — Instagram requires a public URL to fetch the video.", "warn")
        log("Instagram: Set up a simple file host or use a CDN and set INSTAGRAM_VIDEO_HOST_URL in .env", "warn")
        return False

    # Build the full URL to this specific file
    filename  = Path(video_path).name
    full_url  = f"{video_url.rstrip('/')}/{filename}"

    try:
        # Step 1 — Create media container
        log("Instagram: Creating media container...", "info")
        container_resp = requests.post(
            f"https://graph.facebook.com/v19.0/{user_id}/media",
            params={
                "media_type": "REELS",
                "video_url": full_url,
                "caption": caption[:2200],
                "access_token": access_token
            },
            timeout=30
        )

        if container_resp.status_code != 200:
            log(f"Instagram: Container creation failed — {container_resp.text[:200]}", "error")
            return False

        container_id = container_resp.json().get("id", "")

        # Step 2 — Poll until ready
        log("Instagram: Waiting for video processing...", "info")
        for _ in range(20):
            time.sleep(10)
            status_resp = requests.get(
                f"https://graph.facebook.com/v19.0/{container_id}",
                params={
                    "fields": "status_code",
                    "access_token": access_token
                },
                timeout=10
            )
            status = status_resp.json().get("status_code", "")
            if status == "FINISHED":
                break
            if status == "ERROR":
                log("Instagram: Video processing failed", "error")
                return False

        # Step 3 — Publish
        log("Instagram: Publishing Reel...", "info")
        pub_resp = requests.post(
            f"https://graph.facebook.com/v19.0/{user_id}/media_publish",
            params={
                "creation_id": container_id,
                "access_token": access_token
            },
            timeout=15
        )

        if pub_resp.status_code == 200:
            log(f"Instagram: Reel published successfully!", "success")
            return True

        log(f"Instagram: Publish failed — {pub_resp.text[:200]}", "error")
        return False

    except Exception as e:
        log(f"Instagram upload error: {e}", "error")
        return False


# ══════════════════════════════════════════════════════
#  REDDIT
# ══════════════════════════════════════════════════════

def upload_reddit(video_path: str, title: str, subreddit: str, log) -> bool:
    """
    Post a video to a subreddit using the Reddit API.

    Required env vars:
        REDDIT_CLIENT_ID
        REDDIT_CLIENT_SECRET
        REDDIT_USERNAME
        REDDIT_PASSWORD
        REDDIT_SUBREDDIT     ← default subreddit (overridable per job)

    Docs: https://www.reddit.com/dev/api
    """
    import requests

    client_id     = os.getenv("REDDIT_CLIENT_ID", "")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
    username      = os.getenv("REDDIT_USERNAME", "")
    password      = os.getenv("REDDIT_PASSWORD", "")
    target_sub    = subreddit or os.getenv("REDDIT_SUBREDDIT", "")

    if not all([client_id, client_secret, username, password, target_sub]):
        log("Reddit: Missing credentials in .env", "error")
        return False

    try:
        auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
        headers = {"User-Agent": "ClipTwist/1.0"}

        # Step 1 — Get access token
        log("Reddit: Authenticating...", "info")
        token_resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=auth,
            headers=headers,
            data={
                "grant_type": "password",
                "username": username,
                "password": password
            },
            timeout=10
        )

        if token_resp.status_code != 200:
            log(f"Reddit: Auth failed — {token_resp.text[:200]}", "error")
            return False

        access_token = token_resp.json().get("access_token", "")
        api_headers  = {**headers, "Authorization": f"bearer {access_token}"}

        # Step 2 — Request upload lease
        log("Reddit: Requesting upload lease...", "info")
        file_size = os.path.getsize(video_path)
        filename  = Path(video_path).name

        lease_resp = requests.post(
            "https://oauth.reddit.com/api/media/asset.json",
            headers=api_headers,
            data={
                "filepath": filename,
                "mimetype": "video/mp4"
            },
            timeout=15
        )

        if lease_resp.status_code != 200:
            log(f"Reddit: Lease failed — {lease_resp.text[:200]}", "error")
            return False

        lease_data  = lease_resp.json()
        upload_url  = "https:" + lease_data["args"]["action"]
        upload_fields = {f["name"]: f["value"] for f in lease_data["args"]["fields"]}
        asset_url   = f"https://i.redd.it/{lease_data['asset']['asset_id']}"

        # Step 3 — Upload to S3
        log("Reddit: Uploading video to Reddit S3...", "info")
        with open(video_path, "rb") as f:
            s3_resp = requests.post(
                upload_url,
                data=upload_fields,
                files={"file": (filename, f, "video/mp4")},
                timeout=120
            )

        if s3_resp.status_code not in (200, 201, 204):
            log(f"Reddit: S3 upload failed [{s3_resp.status_code}]", "error")
            return False

        # Step 4 — Submit post
        log(f"Reddit: Posting to r/{target_sub}...", "info")
        post_resp = requests.post(
            "https://oauth.reddit.com/api/submit",
            headers=api_headers,
            data={
                "sr": target_sub,
                "kind": "video",
                "title": title[:300],
                "url": asset_url,
                "video_poster_url": asset_url,
                "nsfw": False,
                "spoiler": False,
                "resubmit": True
            },
            timeout=15
        )

        result = post_resp.json()
        if post_resp.status_code == 200 and not result.get("json", {}).get("errors"):
            post_url = result.get("json", {}).get("data", {}).get("url", "")
            log(f"Reddit: Posted! {post_url}", "success")
            return True

        errors = result.get("json", {}).get("errors", [])
        log(f"Reddit: Post failed — {errors}", "error")
        return False

    except Exception as e:
        log(f"Reddit upload error: {e}", "error")
        return False


# ══════════════════════════════════════════════════════
#  DISPATCHER
# ══════════════════════════════════════════════════════

def dispatch_upload(job: dict, log) -> bool:
    """
    Route a queue job to the correct platform uploader.

    job keys:
        platform   : "tiktok" | "youtube" | "instagram" | "reddit"
        video_path : absolute path to the video file
        title      : rendered title string
        description: rendered description string
        subreddit  : (reddit only) target subreddit name
    """
    platform   = job.get("platform", "")
    video_path = job.get("video_path", "")
    title      = job.get("title", "ClipTwist")
    description= job.get("description", "")
    subreddit  = job.get("subreddit", "")

    if not os.path.exists(video_path):
        log(f"Upload skipped — file not found: {video_path}", "error")
        return False

    log(f"[{platform.upper()}] Starting upload: {Path(video_path).name}", "info")

    if platform == "tiktok":
        return upload_tiktok(video_path, title, log)
    elif platform == "youtube":
        return upload_youtube(video_path, title, description, log)
    elif platform == "instagram":
        return upload_instagram(video_path, title + "\n\n" + description, log)
    elif platform == "reddit":
        return upload_reddit(video_path, title, subreddit, log)
    else:
        log(f"Unknown platform: {platform}", "error")
        return False