import logging
import re
from datetime import date

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAPIService:
    """YouTube Data API v3 client for reliable channel/video discovery."""

    def __init__(self):
        self.api_key = settings.YOUTUBE_API_KEY

    async def get_channel_videos(self, channel_id: str) -> list[dict]:
        """Get all videos from a channel using the Data API v3."""
        if not self.api_key:
            raise RuntimeError("YouTube API key not configured")

        videos = []
        # First, get the uploads playlist ID
        uploads_playlist_id = await self._get_uploads_playlist(channel_id)
        if not uploads_playlist_id:
            return []

        # Then paginate through all videos in the playlist
        page_token = None
        while True:
            params = {
                "part": "snippet,contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": 50,
                "key": self.api_key,
            }
            if page_token:
                params["pageToken"] = page_token

            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{YOUTUBE_API_BASE}/playlistItems", params=params)
                resp.raise_for_status()
                data = resp.json()

            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                vid_id = snippet.get("resourceId", {}).get("videoId", "")
                if not vid_id:
                    continue

                # Prefer contentDetails.videoPublishedAt (actual video publish date)
                # over snippet.publishedAt (when added to playlist, can differ for premieres)
                content_details = item.get("contentDetails", {})
                published = (
                    content_details.get("videoPublishedAt")
                    or snippet.get("publishedAt")
                    or ""
                )
                upload_date = published[:10].replace("-", "") if published else None

                videos.append({
                    "id": vid_id,
                    "title": snippet.get("title", "Untitled"),
                    "description": snippet.get("description"),
                    "upload_date": upload_date,
                    "thumbnail": self._best_thumbnail(snippet.get("thumbnails", {})),
                })

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        # Batch-fetch durations so shorts detection works
        video_ids = [v["id"] for v in videos]
        durations = await self._batch_fetch_durations(video_ids)
        for v in videos:
            v["duration"] = durations.get(v["id"])

        logger.info("YouTube API found %d videos for channel %s", len(videos), channel_id)
        return videos

    async def _get_uploads_playlist(self, channel_id: str) -> str | None:
        """Get the uploads playlist ID for a channel."""
        params = {
            "part": "contentDetails",
            "id": channel_id,
            "key": self.api_key,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{YOUTUBE_API_BASE}/channels", params=params)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("items", [])
        if not items:
            return None

        return items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")

    async def _batch_fetch_durations(self, video_ids: list[str]) -> dict[str, int | None]:
        """Batch-fetch video durations in seconds via videos.list."""
        if not self.api_key or not video_ids:
            return {}

        durations: dict[str, int | None] = {}
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            params = {
                "part": "contentDetails",
                "id": ",".join(batch),
                "key": self.api_key,
            }
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(f"{YOUTUBE_API_BASE}/videos", params=params)
                    resp.raise_for_status()
                    data = resp.json()

                for item in data.get("items", []):
                    vid_id = item.get("id", "")
                    iso_duration = item.get("contentDetails", {}).get("duration", "")
                    durations[vid_id] = self._parse_iso8601_duration(iso_duration)
            except Exception as e:
                logger.warning("Failed to batch-fetch durations: %s", e)
                break

        return durations

    @staticmethod
    def _parse_iso8601_duration(iso: str) -> int | None:
        """Parse ISO 8601 duration (e.g., PT5M30S) to seconds."""
        if not iso:
            return None
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
        if not m:
            return None
        hours = int(m.group(1) or 0)
        minutes = int(m.group(2) or 0)
        seconds = int(m.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    async def get_video_dates(self, video_ids: list[str]) -> dict[str, str | None]:
        """Batch-fetch upload dates for specific video IDs. Returns {video_id: "YYYYMMDD" or None}."""
        if not self.api_key or not video_ids:
            return {}

        dates: dict[str, str | None] = {}
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            params = {
                "part": "contentDetails,snippet",
                "id": ",".join(batch),
                "key": self.api_key,
            }
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(f"{YOUTUBE_API_BASE}/videos", params=params)
                    resp.raise_for_status()
                    data = resp.json()

                for item in data.get("items", []):
                    vid_id = item.get("id", "")
                    snippet = item.get("snippet", {})
                    published = snippet.get("publishedAt", "")
                    if published:
                        dates[vid_id] = published[:10].replace("-", "")
                    else:
                        dates[vid_id] = None
            except Exception as e:
                logger.warning("Failed to batch-fetch video dates: %s", e)
                break

        return dates

    async def validate_api_key(self) -> tuple[bool, str]:
        """Check if the API key is valid. Returns (success, message)."""
        if not self.api_key:
            return False, "No API key provided"
        try:
            params = {"part": "snippet", "chart": "mostPopular", "maxResults": 1, "key": self.api_key}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{YOUTUBE_API_BASE}/videos", params=params)
                if resp.status_code == 200:
                    return True, "OK"
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                error_msg = body.get("error", {}).get("message", resp.text[:200])
                logger.warning("YouTube API key validation failed (%d): %s", resp.status_code, error_msg)
                return False, f"YouTube API returned {resp.status_code}: {error_msg}"
        except Exception as e:
            logger.warning("YouTube API key validation error: %s", e)
            return False, f"Connection error: {e}"

    async def get_channel_thumbnail(self, channel_id: str) -> str | None:
        """Fetch the channel avatar/thumbnail URL via the Data API."""
        if not self.api_key:
            return None

        try:
            params = {
                "part": "snippet",
                "id": channel_id,
                "key": self.api_key,
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{YOUTUBE_API_BASE}/channels", params=params)
                resp.raise_for_status()
                data = resp.json()

            items = data.get("items", [])
            if not items:
                return None

            thumbnails = items[0].get("snippet", {}).get("thumbnails", {})
            return self._best_thumbnail(thumbnails)
        except Exception as e:
            logger.warning("Failed to fetch channel thumbnail for %s: %s", channel_id, e)
            return None

    @staticmethod
    def _best_thumbnail(thumbnails: dict) -> str | None:
        """Get the best quality thumbnail URL."""
        for quality in ["maxres", "standard", "high", "medium", "default"]:
            if quality in thumbnails:
                return thumbnails[quality].get("url")
        return None
