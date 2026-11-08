"""Jira REST requests and document formatting with no network access."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests

from adapters.jira.client import JiraClient


@pytest.fixture
def jira_http(monkeypatch):
    response = Mock(content=b"{}")
    response.json.return_value = {}
    request = Mock(return_value=response)
    monkeypatch.setattr(requests, "request", request)
    return JiraClient("synthetic-access"), request, response


def test_projects_use_bearer_token_and_atlassian_gateway(jira_http):
    client, request, _ = jira_http
    cloud_id = str(uuid4())
    client.list_projects(cloud_id, 50)
    request.assert_called_once_with(
        "GET", f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/project/search",
        headers={"Authorization": "Bearer synthetic-access", "Accept": "application/json"},
        params={"startAt": 50, "maxResults": 50, "action": "create"}, json=None, timeout=10,
    )


def test_site_discovery_filters_non_jira_resources(jira_http):
    client, _, response = jira_http
    jira_site = {"id": "jira", "scopes": ["read:jira-work", "write:jira-work"]}
    response.json.return_value = [jira_site, {"id": "confluence", "scopes": ["read:confluence-content.all"]}]
    assert client.list_sites() == [jira_site]


def test_marker_lookup_uses_enhanced_search_and_detects_duplicates(jira_http):
    client, request, response = jira_http
    cloud_id = str(uuid4())
    marker = f"backlogger-story-{uuid4()}"
    response.json.return_value = {"issues": [{"key": "TEST-1"}]}
    assert client.find_import_by_key(cloud_id, "10001", marker) == {"key": "TEST-1"}
    assert request.call_args.args[1].endswith("/search/jql")
    assert request.call_args.kwargs["json"]["jql"] == f'project = 10001 AND labels = "{marker}"'
    response.json.return_value = {"issues": [{"key": "TEST-1"}, {"key": "TEST-2"}]}
    with pytest.raises(ValueError, match="Multiple"):
        client.find_import_by_key(cloud_id, "10001", marker)


def test_import_reference_cannot_inject_jql(jira_http):
    client, request, _ = jira_http
    with pytest.raises(ValueError):
        client.find_import_by_key(str(uuid4()), "10001 OR project=2", "bad-marker")
    request.assert_not_called()


def test_issue_creation_sends_adf_and_marker_labels(jira_http, pipeline_results):
    client, request, response = jira_http
    response.json.return_value = {"key": "TEST-1", "id": "10000"}
    description = client.format_story(pipeline_results[1].stories[0])
    assert description["type"] == "doc" and description["version"] == 1
    assert "Acceptance criteria" in str(description)
    lists = [node for node in description["content"] if node["type"] == "bulletList"]
    assert len(lists) == 2
    assert [item["content"][0]["content"][0]["text"] for item in lists[0]["content"]] == [
        f"☐ {criterion}" for criterion in pipeline_results[1].stories[0].acceptance_criteria
    ]
    assert "Solution tasks (proposed)" in str(description)
    quote = pipeline_results[1].stories[0].source.quote
    assert f'Source Quote: "{quote}" (0:02)' in str(description)
    assert "Meeting evidence" not in str(description) and "link" not in str(description)
    fields = {"summary": "Test", "description": description, "labels": ["ai-draft"]}
    assert client.create_issue(str(uuid4()), fields)["key"] == "TEST-1"
    assert request.call_args.kwargs["json"] == {"fields": fields}


def test_creation_timeout_is_not_retried(jira_http):
    client, request, _ = jira_http
    request.side_effect = requests.Timeout()
    with pytest.raises(requests.Timeout):
        client.create_issue(str(uuid4()), {})
    request.assert_called_once()
