"""Jira Cloud REST calls using a browser account's OAuth access token."""

from urllib.parse import quote
from uuid import UUID

import requests

from services.worker.objects import DraftStory


class JiraClient:
    def __init__(self, access_token: str):
        self.access_token = access_token

    def _request(self, method: str, path: str, *, params=None, body=None):
        response = requests.request(
            method, f"https://api.atlassian.com/{path}",
            headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"},
            params=params, json=body, timeout=10,
        )
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
    def format_story(story: DraftStory, evidence_url: str) -> dict:
        lines = [
            f"As a {story.as_a}, I want {story.i_want} so that {story.so_that}",
            f"Priority: {story.priority}",
            f"Source: {story.source.speaker}, {story.source.start_s}–{story.source.end_s}s",
            story.source.quote,
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
        content.append({"type": "paragraph", "content": [{
            "type": "text", "text": "Meeting evidence",
            "marks": [{"type": "link", "attrs": {"href": evidence_url}}],
        }]})
        return {"type": "doc", "version": 1, "content": content}

    def create_issue(self, cloud_id: str, fields: dict):
        return self._request("POST", self._site_path(cloud_id, "issue"), body={"fields": fields})
