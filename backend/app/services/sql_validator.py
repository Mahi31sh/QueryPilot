"""
Guardrail layer: inspects generated SQL before it's ever executed.
This is the single most safety-critical file in the backend.
"""
import re
import logging

import sqlparse

from app.config import settings
from app.exceptions import DangerousSQLError
from app.utils import extract_table_names_from_sql

logger = logging.getLogger(__name__)

BLOCKED_KEYWORDS = {
    "DELETE", "UPDATE", "DROP", "ALTER", "TRUNCATE", "INSERT",
    "CREATE", "GRANT", "REVOKE", "EXEC", "EXECUTE", "MERGE",
    "INTO",  # blocks SELECT ... INTO new_table
}

# Functions that are valid inside a SELECT but can stall the server,
# read server files, or open outside connections.
BLOCKED_FUNCTIONS = {
    "PG_SLEEP", "PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_LS_DIR", "PG_STAT_FILE",
    "LO_IMPORT", "LO_EXPORT", "DBLINK", "SET_CONFIG",
    "PG_TERMINATE_BACKEND", "PG_CANCEL_BACKEND",
}

# QueryPilot's own tables (accounts, password hashes, history). They live in the
# application database and must never be reachable through generated SQL,
# whatever position they appear in (subquery, CTE body, join, ...).
PROTECTED_TABLES = {"users", "query_history"}

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_CTE_NAME = re.compile(
    r"(?:\bWITH\s+(?:RECURSIVE\s+)?|,\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(",
    re.IGNORECASE,
)


_FROM_AS_OPERATOR = re.compile(
    r"\bEXTRACT\s*\(\s*\w+\s+FROM\b|\bSUBSTRING\s*\([^()]*?\bFROM\b|\bTRIM\s*\([^()]*?\bFROM\b|\bDISTINCT\s+FROM\b",
    re.IGNORECASE,
)
_FROM_JOIN_TARGET = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)", re.IGNORECASE)


def _regex_table_names(sql: str) -> set[str]:
    """
    Second, deliberately conservative table scan over the *whole* statement text.
    The sqlparse-based extractor only follows top-level FROM/JOIN clauses, so
    tables inside CTE bodies and WHERE/SELECT-list subqueries need this pass.
    FROM used as an operator (EXTRACT(MONTH FROM col)) is blanked out first.
    """
    cleaned = _FROM_AS_OPERATOR.sub(" ", sql)
    return {m.split(".")[-1].lower() for m in _FROM_JOIN_TARGET.findall(cleaned)}


def _strip_string_literals(sql: str) -> str:
    """Replace 'quoted text' with '' so keywords inside values are not scanned."""
    return _STRING_LITERAL.sub("''", sql)


def _strip_code_fences(sql: str) -> str:
    """LLMs sometimes wrap SQL in ```sql ... ``` despite instructions not to."""
    sql = re.sub(r"^```sql\s*|^```\s*|```$", "", sql.strip(), flags=re.IGNORECASE | re.MULTILINE)
    return sql.strip().rstrip(";").strip()


def validate_sql(raw_sql: str, known_tables: set[str] | None = None) -> str:
    """
    Runs the full guardrail pipeline. Returns cleaned, validated SQL,
    or raises DangerousSQLError. This function never executes SQL itself
    — it only inspects the text.
    """
    sql = _strip_code_fences(raw_sql)

    if not sql:
        raise DangerousSQLError("Generated SQL was empty.")

    # 1. Must be a single statement (block statement chaining / SQL injection via ';')
    statements = [s for s in sqlparse.split(sql) if s.strip()]
    if len(statements) != 1:
        raise DangerousSQLError("Only a single SQL statement is allowed.")

    parsed = sqlparse.parse(sql)[0]
    statement_type = parsed.get_type()

    # 2. Must be SELECT only
    if statement_type != "SELECT":
        raise DangerousSQLError(f"Only SELECT statements are allowed, got: {statement_type}")

    # 3. Keyword/function denylist as a second, belt-and-suspenders check.
    #    String literals are removed first so a value like WHERE city = 'Delete'
    #    is not mistaken for the DELETE keyword.
    upper_sql = _strip_string_literals(sql).upper()
    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper_sql):
            raise DangerousSQLError(f"Disallowed keyword detected: {keyword}")
    for table in PROTECTED_TABLES:
        if re.search(rf"\b{table}\b", upper_sql, flags=re.IGNORECASE):
            raise DangerousSQLError(f"Access to internal table '{table}' is not allowed.")
    for function in BLOCKED_FUNCTIONS:
        if re.search(rf"\b{function}\s*\(", upper_sql):
            raise DangerousSQLError(f"Disallowed function detected: {function.lower()}")

    # 4. Enforce LIMIT
    if "LIMIT" not in upper_sql:
        sql = f"{sql}\nLIMIT {settings.max_query_rows}"
    else:
        # if a LIMIT exists but exceeds the configured cap, clamp it
        match = re.search(r"LIMIT\s+(\d+)", upper_sql)
        if match and int(match.group(1)) > settings.max_query_rows:
            sql = re.sub(r"LIMIT\s+\d+", f"LIMIT {settings.max_query_rows}", sql, flags=re.IGNORECASE)

    # 5. Hallucinated table check (only if we were given retrieved tables to check against)
    if known_tables:
        referenced_tables = extract_table_names_from_sql(sql) | _regex_table_names(
            _strip_string_literals(sql)
        )
        cte_names = {name.lower() for name in _CTE_NAME.findall(sql)}
        unknown = referenced_tables - known_tables - cte_names
        if unknown:
            raise DangerousSQLError(
                f"Query references tables not found in schema context: {', '.join(unknown)}"
            )

    logger.info(f"SQL validated successfully: {sql[:120]}...")
    return sql