"""Clock times like 2:14 for talking about moments in a recording."""


def format_clock(seconds: float) -> str:
    """134.9 -> "2:14", 3725 -> "1:02:05". Fractions of a second are dropped, like a video player."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_clock(text: str) -> float:
    """"02:14" -> 134.0, "1:02:05" -> 3725.0, "2:14.5" -> 134.5. Raises ValueError otherwise."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(parts):
        raise ValueError(f"Not a clock time: {text!r}")
    seconds = 0.0
    for part in parts:
        value = float(part)  # also raises ValueError
        if value < 0 or value != value or value == float("inf"):
            raise ValueError(f"Not a clock time: {text!r}")
        seconds = seconds * 60 + value
    return seconds
