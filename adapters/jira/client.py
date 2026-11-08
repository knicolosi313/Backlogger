"""Jira Cloud REST calls using a browser account's OAuth access token."""

import hashlib
import math
import threading
import time
from urllib.parse import quote
from uuid import UUID

import requests

from services.worker.objects import DraftStory
from services.worker.timecode import format_clock


# Separate export clients share rate-limit feedback within this backend process.
# Store a digest rather than keeping another copy of an access token.
_cooldowns: dict[str, float] = {}
_cooldown_lock = threading.Lock()


def _wait_for_cooldown(account_key: str) -> None:
    while True:
        with _cooldown_lock:
            delay = _cooldowns.get(account_key, 0) - time.monotonic()
            if delay <= 0:
                _cooldowns.pop(account_key, None)
                return
        time.sleep(delay)


def _remember_cooldown(account_key: str, retry_after) -> None:
    try:
        delay = float(retry_after)
        if not math.isfinite(delay) or delay < 0:
            raise ValueError()
    except (TypeError, ValueError):
        delay = 1.0
    with _cooldown_lock:
        _cooldowns[account_key] = max(_cooldowns.get(account_key, 0), time.monotonic() + delay)


class JiraClient:
    def __init__(self, access_token: str):
        self.access_token = access_token
        # Each export owns its client; reuse HTTP connections within that thread.
        self._session = requests.Session()
        self._account_key = hashlib.sha256(access_token.encode()).hexdigest()

    def _request(self, method: str, path: str, *, params=None, body=None):
        _wait_for_cooldown(self._account_key)
        response = self._session.request(
            method, f"https://api.atlassian.com/{path}",
            headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"},
            params=params, json=body, timeout=10,
        )
        if response.status_code == 429:
            _remember_cooldown(self._account_key, response.headers.get("Retry-After"))
        # Never automatically replay issue creation after a timeout or rate limit.
        response.raise_for_status()
        return response.json() if response.content else None

    def get_current_member(self):
        return self._request("GET", "me")

    def list_sites(self):
        sites = self._request("GET", "oauth/token/accessible-resources")
        return [site for site in sites if "read:jira-work" in site.get("scopes", [])]

    def _site_path(self, cloud_id: str, path: str) -> str:
        # A fixed Atlassian host and UUID prevent user-supplied site URLs/paths.
        return f"ex/jira/{UUID(cloud_id)}/rest/api/3/{path}"

    def list_projects(self, cloud_id: str, start_at: int = 0):
        return self._request(
            "GET", self._site_path(cloud_id, "project/search"),
            params={"startAt": start_at, "maxResults": 50, "action": "create"},
        )

    def get_project(self, cloud_id: str, project_id: str):
        return self._request("GET", self._site_path(cloud_id, f"project/{quote(project_id, safe='')}"))

    def list_context_issues(self, cloud_id: str, project_id: str, next_page: str | None = None):
        if not project_id.isascii() or not project_id.isdigit():
            raise ValueError("Expected a numeric Jira project ID")
        body = {
            "jql": f"project = {project_id} ORDER BY updated DESC, key ASC",
            "fields": ["summary", "description", "status", "issuetype", "parent"],
            "maxResults": 100,
        }
        if next_page:
            body["nextPageToken"] = next_page
        return self._request("POST", self._site_path(cloud_id, "search/jql"), body=body)

    def list_issue_types(self, cloud_id: str, project_id: str, start_at: int = 0):
        path = f"issue/createmeta/{quote(project_id, safe='')}/issuetypes"
        return self._request("GET", self._site_path(cloud_id, path), params={"startAt": start_at, "maxResults": 50})

    def get_create_fields(self, cloud_id: str, project_id: str, issue_type_id: str, start_at: int = 0):
        path = f"issue/createmeta/{quote(project_id, safe='')}/issuetypes/{quote(issue_type_id, safe='')}"
        return self._request("GET", self._site_path(cloud_id, path), params={"startAt": start_at, "maxResults": 50})

    def find_import_by_key(self, cloud_id: str, project_id: str, marker: str):
        if not project_id.isdigit() or not marker.startswith("backlogger-story-"):
            raise ValueError("Invalid Jira import reference")
        UUID(marker.removeprefix("backlogger-story-"))
        result = self._request("POST", self._site_path(cloud_id, "search/jql"), body={
            "jql": f'project = {project_id} AND labels = "{marker}"',
            "maxResults": 2, "fields": ["summary"],
        })
        issues = result["issues"]
        if len(issues) > 1:
            raise ValueError("Multiple Jira issues have this import marker")
        return issues[0] if issues else None

    @staticmethod
    def format_story(story: DraftStory) -> dict:
        lines = [
            f"As a {story.as_a}, I want {story.i_want} so that {story.so_that}",
            f"Priority: {story.priority}",
            # Only when the quote begins, like a video player shows it: (2:14)
            f'Source Quote: "{story.source.quote}" ({format_clock(story.source.start_s)})',
        ]
        if story.estimate is not None:
            lines.append(f"Estimate: {story.estimate} points")
        content = [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in lines if line]
        # Standard ADF lists with visual boxes, not interactive Jira controls.
        for title, items in [
            ("Acceptance criteria", story.acceptance_criteria),
            ("Solution tasks (proposed)", story.solution_tasks),
        ]:
            if not items:
                continue
            content.append({"type": "paragraph", "content": [{"type": "text", "text": title}]})
            content.append({"type": "bulletList", "content": [
                {"type": "listItem", "content": [{"type": "paragraph", "content": [
                    {"type": "text", "text": f"☐ {item}"},
                ]}]} for item in items
            ]})
        return {"type": "doc", "version": 1, "content": content}

    def create_issue(self, cloud_id: str, fields: dict):
        return self._request("POST", self._site_path(cloud_id, "issue"), body={"fields": fields})
