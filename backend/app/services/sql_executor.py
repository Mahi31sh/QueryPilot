"""
Executes validated SQL against the target analytics database, with the
full self-correction retry loop: generate -> validate -> execute -> on
failure, feed the error back to the LLM and retry, up to a configured limit.
"""
import logging
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.exceptions import SQLGenerationError
from app.services.sql_generator import generate_sql, correct_sql
from app.services.sql_validator import validate_sql

logger = logging.getLogger(__name__)

# Prefer a dedicated analytics connection when configured; otherwise fall back
# to the app DB connection. The execution path still runs in a read-only
# transaction so a missed validator rule cannot write to the database.
_target_engine: Engine = create_engine(
    settings.analytics_database_url or settings.database_url,
    pool_pre_ping=True,
)


def _execute_raw(sql: str) -> tuple[list[str], list[dict]]:
    with _target_engine.begin() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        # SET LOCAL lasts only for this transaction; int() guards the f-string.
        conn.execute(text(f"SET LOCAL statement_timeout = {int(settings.statement_timeout_ms)}"))
        result = conn.execute(text(sql))
        columns = list(result.keys())
        # Hard row cap, independent of whatever LIMIT the SQL text contains.
        rows = [dict(zip(columns, row)) for row in result.fetchmany(settings.max_query_rows)]
        return columns, rows


def run_query_with_self_correction(
    question: str, schema_context: str, known_tables: set[str]
) -> dict:
    """
    Full pipeline: generate -> validate -> execute, retrying with
    LLM self-correction on failure up to settings.sql_retry_limit times.
    Returns a dict with sql, columns, rows, retry_count, execution_time_ms.
    """
    start = time.perf_counter()
    sql = generate_sql(question, schema_context)
    last_error = None

    for attempt in range(settings.sql_retry_limit + 1):
        try:
            validated_sql = validate_sql(sql, known_tables=known_tables)
            columns, rows = _execute_raw(validated_sql)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "sql": validated_sql,
                "columns": columns,
                "rows": rows,
                "retry_count": attempt,
                "execution_time_ms": elapsed_ms,
            }
        except SQLAlchemyError as exc:
            last_error = str(exc.orig) if hasattr(exc, "orig") else str(exc)
            logger.warning(f"Execution failed on attempt {attempt}: {last_error}")
        except Exception as exc:  # covers DangerousSQLError bubbling from validate_sql
            last_error = str(exc)
            logger.warning(f"Validation failed on attempt {attempt}: {last_error}")

        if attempt < settings.sql_retry_limit:
            sql = correct_sql(question, schema_context, sql, last_error)

    raise SQLGenerationError(
        f"Could not produce a working query after {settings.sql_retry_limit + 1} attempts. "
        f"Last error: {last_error}"
    )