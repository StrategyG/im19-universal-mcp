"""Typed connector discovery; known does not mean connected or enabled."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Service:
    name: str
    provider: str
    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    implemented: bool = False


SERVICES: dict[str, Service] = {
    "youtube": Service(
        "YouTube", "google",
        ("https://www.googleapis.com/auth/youtube.readonly",),
        ("youtube_get_channel", "youtube_list_videos", "youtube_get_analytics"),
        True,
    ),
    "drive": Service(
        "Google Drive", "google",
        ("https://www.googleapis.com/auth/drive.metadata.readonly",),
        ("drive_search_files", "drive_list_folder", "drive_get_metadata", "drive_read_file",
         "drive_create_folder", "drive_get_write_approval_status"), True,
    ),
    "gmail": Service("Gmail", "google", (), (), False),
    "calendar": Service("Google Calendar", "google", (), (), False),
    "docs": Service("Google Docs", "google", (), (), False),
    "sheets": Service("Google Sheets", "google", (), (), False),
    "instagram": Service("Instagram", "meta", (), (), False),
    "facebook": Service("Facebook", "meta", (), (), False),
    "tiktok": Service("TikTok", "tiktok", (), (), False),
    "x": Service("X", "x", (), (), False),
    "linkedin": Service("LinkedIn", "linkedin", (), (), False),
    "github": Service("GitHub", "github", (), (), False),
    "cloudbrowser": Service(
        "Cloudbrowser A2A", "cloudbrowser", (),
        ("cloudbrowser_get_agent_card", "cloudbrowser_get_task", "cloudbrowser_get_task_summary",
         "cloudbrowser_list_tasks", "cloudbrowser_list_task_summaries",
         "cloudbrowser_get_readiness"), True,
    ),
}


def list_services() -> list[dict]:
    return [
        {"id": key, "name": s.name, "provider": s.provider,
         "implemented": s.implemented, "capabilities": list(s.capabilities)}
        for key, s in SERVICES.items()
    ]
