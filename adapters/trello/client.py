import threading
import time
from collections import deque

import requests
from services.worker.objects import DraftStory
from services.worker.timecode import format_clock

# Trello rejects a token that sends more than 100 requests in 10 seconds. Stay well under that. The
# budget is shared by every client using the same token, so imports can run side by side without
# any of them being turned away (a rejected card creation can't safely be retried).
RATE_LIMIT_REQUESTS = 80
RATE_LIMIT_WINDOW_SECONDS = 10.0
_sent: dict[str, deque] = {}
_sent_lock = threading.Lock()


def _wait_for_request_slot(token: str) -> None:
    while True:
        with _sent_lock:
            window = _sent.setdefault(token, deque())
            now = time.monotonic()
            while window and now - window[0] >= RATE_LIMIT_WINDOW_SECONDS:
                window.popleft()
            if len(window) < RATE_LIMIT_REQUESTS:
                window.append(now)
                return
            delay = RATE_LIMIT_WINDOW_SECONDS - (now - window[0])
        time.sleep(delay)


class TrelloClient:
    """Talk to Trello; database records and export decisions live in the service layer."""

    def __init__(self, api_key: str, token: str):
        self.api_key = api_key
        self.token = token
        # One connection reused for every call, instead of a new TLS handshake each time.
        # A client belongs to one thread: each export builds its own.
        self._session = requests.Session()

    def _request(self, method: str, path: str, *, params=None, body=None):
        """Send one request with authentication, a timeout, and HTTP error checks."""
        query = dict(params or {})
        query.update(key=self.api_key, token=self.token)
        _wait_for_request_slot(self.token)
        response = self._session.request(
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

    def list_context_cards(self, board_id: str):
        """Include archived work so it can still be recognized as existing scope."""
        return self._request(
            "GET", f"boards/{board_id}/cards/all",
            params={"fields": "id,name,desc,closed,labels", "limit": 501},
        )

    def list_context_checklists(self, board_id: str):
        return self._request("GET", f"boards/{board_id}/checklists")

    def create_label(self, board_id: str, name: str, color: str = "blue"):
        """Create a board label, for example an 'AI Draft' label."""
        return self._request(
            "POST", "labels", params={"idBoard": board_id, "name": name, "color": color},
        )

    def add_label(self, card_id: str, label_id: str):
        """Attach an existing label to a card."""
        return self._request("POST", f"cards/{card_id}/idLabels", params={"value": label_id})

    def list_checklists(self, card_id: str):
        """Include all items so retries can preserve completed checks."""
        return self._request("GET", f"cards/{card_id}/checklists", params={"checkItems": "all"})

    def create_checklist(self, card_id: str, name: str):
        return self._request("POST", f"cards/{card_id}/checklists", params={"name": name})

    def add_checklist_item(self, checklist_id: str, name: str):
        return self._request(
            "POST", f"checklists/{checklist_id}/checkItems", params={"name": name, "checked": "false"},
        )

    def create_card(self, list_id: str, title: str, description: str):
        """Create a card. The description is exactly what the person should read."""
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
        # Only when the quote begins, like a video player shows it: (2:14)
        description += f'\nSource Quote: "{story.source.quote}" ({format_clock(story.source.start_s)})'
        return description

    def find_existing_card(
        self,
        board_id: str,
        title: str,
        description: str,
        *,
        exclude_ids: frozenset[str] | set[str] = frozenset(),
        legacy_key: str | None = None,
    ):
        """Return a card we already created for this story, or None.

        Nothing is stored on the card to find it by. The title and description are generated from
        the accepted story (source quote, speaker and times included) and a pending export blocks
        edits, so an exact match is our card. Cards in `exclude_ids` already belong to another
        import. Archived cards count too.

        `legacy_key` also finds cards made before this change, which ended with a
        "Backlogger-Import-Key: <key>" line.
        """
        params = {
            "filter": "all",
            "fields": "id,name,desc,url",
        }
        cards = self._request("GET", f"boards/{board_id}/cards", params=params)

        def normalized(text: str) -> str:
            return text.replace("\r\n", "\n").rstrip()

        wanted = normalized(description)
        legacy_marker = f"Backlogger-Import-Key: {legacy_key}" if legacy_key else None
        for card in cards:
            if card["id"] in exclude_ids:
                continue
            found = normalized(card.get("desc", ""))
            if card.get("name") == title and found in (wanted, f"{wanted}\n\n{legacy_marker}"):
                return card
            if legacy_marker and legacy_marker in found.splitlines():
                return card
        return None
