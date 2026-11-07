
"""
The core endpoint: natural language question in, SQL + results + chart out.
This is where every service module comes together.
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import schemas, models
from app.auth import get_current_user
from app.database import get_db
from app.services.rag import retrieve_schema_context, get_known_table_names
from app.services.sql_executor import run_query_with_self_correction
from app.services.sql_generator import explain_sql
from app.services.chart_generator import build_chart_spec
from app.services.history_service import log_query
from app.exceptions import QueryPilotException
from app.services.intent_guard import check_query_intent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/query", tags=["query"])


@router.post("", response_model=schemas.QueryResponse)
def run_query(
    request: schemas.QueryRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    question = request.question.strip()

    # Block destructive requests before schema retrieval or LLM generation.
    try:
        check_query_intent(question)
    except ValueError as exc:
        log_query(
            db, current_user.id, question, generated_sql="", explanation="",
            success=False, error_message=str(exc), retry_count=0,
            row_count=None, chart_type=None,
        )
        raise QueryPilotException(str(exc))

    schema_context = retrieve_schema_context(question)
    known_tables = get_known_table_names(question)

    try:
        result = run_query_with_self_correction(
            question, schema_context, known_tables
        )
    except QueryPilotException as exc:
        log_query(
            db, current_user.id, question, generated_sql="", explanation="",
            success=False, error_message=exc.message, retry_count=0,
            row_count=None, chart_type=None,
        )
        raise

    explanation = explain_sql(result["sql"])
    chart_spec = build_chart_spec(question, result["columns"], result["rows"])

    log_query(
        db, current_user.id, question,
        generated_sql=result["sql"], explanation=explanation,
        success=True, error_message=None, retry_count=result["retry_count"],
        row_count=len(result["rows"]), chart_type=chart_spec["chart_type"],
    )

    return schemas.QueryResponse(
        question=question,
        generated_sql=result["sql"],
        explanation=explanation,
        columns=result["columns"],
        rows=result["rows"],
        row_count=len(result["rows"]),
        chart=schemas.ChartSpec(**chart_spec),
        retry_count=result["retry_count"],
        execution_time_ms=result["execution_time_ms"],
    )
