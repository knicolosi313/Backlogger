import requests
from services.worker.objects import DraftStory

class TrelloClient:
    """Talk to Trello; database records and export decisions live in the service layer."""

    def __init__(self, api_key: str, token: str):
        self.api_key = api_key
        self.token = token

    def _request(self, method: str, path: str, *, params=None, body=None):
        """Send one request with authentication, a timeout, and HTTP error checks."""
        query = dict(params or {})
        query.update(key=self.api_key, token=self.token)
        response = requests.request(
            method,
            f"https://api.trello.com/1/{path}",
            params=query,
            json=body,
            timeout=10,
        )
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    def list_boards(self):
        """Return the boards accessible to this Trello account."""
        return self._request("GET", "members/me/boards")

    def get_current_member(self):
        """Validate the connected token and identify its Trello account."""
        return self._request("GET", "members/me", params={"fields": "id,username"})

    def list_lists(self, board_id: str):
        """Return the open lists on a Trello board."""
        return self._request("GET", f"boards/{board_id}/lists", params={"filter": "open"})

    def list_labels(self, board_id: str):
        """Return up to 1,000 labels from the board."""
        return self._request(
            "GET", f"boards/{board_id}/labels",
            params={"fields": "id,name,color", "limit": 1000},
        )

    def create_label(self, board_id: str, name: str, color: str = "blue"):
        """Create a board label, for example an 'AI Draft' label."""
        return self._request(
            "POST", "labels", params={"idBoard": board_id, "name": name, "color": color},
        )

    def add_label(self, card_id: str, label_id: str):
        """Attach an existing label to a card."""
        return self._request("POST", f"cards/{card_id}/idLabels", params={"value": label_id})

    def attach_evidence_link(self, card_id: str, url: str, name: str = "Meeting evidence"):
        """Attach a link to the meeting or story review page."""
        return self._request(
            "POST", f"cards/{card_id}/attachments", params={"url": url, "name": name},
        )

    def list_attachments(self, card_id: str):
        """Read existing evidence links before retrying an attachment request."""
        return self._request("GET", f"cards/{card_id}/attachments")

    def list_checklists(self, card_id: str):
        """Include all items so retries can preserve completed checks."""
        return self._request("GET", f"cards/{card_id}/checklists", params={"checkItems": "all"})

    def create_checklist(self, card_id: str, name: str):
        return self._request("POST", f"cards/{card_id}/checklists", params={"name": name})

    def add_checklist_item(self, checklist_id: str, name: str):
        return self._request(
            "POST", f"checklists/{checklist_id}/checkItems", params={"name": name, "checked": "false"},
        )

    def create_card(
        self,
        list_id: str,
        title: str,
        description: str,
        *,
        idempotency_key: str | None = None,
    ):
        """Create a card, optionally marking it for later import lookups."""
        if idempotency_key is not None:
            if not idempotency_key.strip() or "\n" in idempotency_key or "\r" in idempotency_key:
                raise ValueError("Import key must be a non-empty single line")
            description = f"{description.rstrip()}\n\nBacklogger-Import-Key: {idempotency_key}"

        body = {
            "idList": list_id,
            "name": title,
            "desc": description,
        }
        return self._request("POST", "cards", body=body)

    @staticmethod
    def format_story(story: DraftStory) -> str:
        """Format story context; the export service creates the actual checklists."""
        description = (
            f"As a {story.as_a}, I want {story.i_want} so that {story.so_that}\n\n"
            "Acceptance criteria are in the Acceptance Criteria checklist.\n"
        )
        if story.solution_tasks:
            description += "Proposed work steps are in the Solution Tasks (proposed) checklist.\n"
        if story.epic:
            description += f"\nEpic: {story.epic}\n"
        description += f"\nPriority: {story.priority}\n"
        if story.estimate is not None:
            description += f"Estimate: {story.estimate} points\n"
        if story.needs_clarification:
            description += "Needs Clarification: Yes\n"
        else:
            description += "Needs Clarification: No\n"
        description += (
            f"\nSource Quote:\n\"{story.source.quote}\"\n"
            f"Start Time: {story.source.start_s:.2f}s, "
            f"End Time: {story.source.end_s:.2f}s, "
            f"Speaker: {story.source.speaker}"
        )
        return description

    def find_import_by_key(self, board_id: str, idempotency_key: str):
        """Return the matching card, or None if it doesn't exist."""
        params = {
            "filter": "all",
            "fields": "id,name,desc,url",
        }
        cards = self._request("GET", f"boards/{board_id}/cards", params=params)
        marker = f"Backlogger-Import-Key: {idempotency_key}"
        for card in cards:
            if marker in card.get("desc", "").splitlines():
                return card
        return None
