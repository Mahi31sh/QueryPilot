
import re

DESTRUCTIVE_PATTERNS = [
    r"\bdelete\b",
    r"\bremove\b",
    r"\bdrop\b",
    r"\btruncate\b",
    r"\bupdate\b",
    r"\binsert\b",
    r"\balter\b",
    r"\bcreate\s+(?:table|database|index|view)\b",
    r"\bmodify\b",
    r"\boverwrite\b",
    r"\bwipe\b",
    r"\bdestroy\b",
]

def check_query_intent(question: str) -> None:
    """Reject natural-language requests for database mutations."""
    normalized = question.strip().lower()

    for pattern in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, normalized):
            raise ValueError(
                "Destructive database operations are not allowed. "
                "QueryPilot supports read-only queries only."
            )
