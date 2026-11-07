"""Unit tests for the SQL guardrail layer (app/services/sql_validator.py)."""
import pytest

from app.config import settings
from app.exceptions import DangerousSQLError
from app.services.sql_validator import validate_sql

TABLES = {"customers", "orders", "order_items", "products", "categories"}


def test_simple_select_passes_and_gets_limit():
    sql = validate_sql("SELECT * FROM customers", known_tables=TABLES)
    assert sql.upper().startswith("SELECT")
    assert f"LIMIT {settings.max_query_rows}" in sql


def test_markdown_code_fence_and_semicolon_are_stripped():
    sql = validate_sql("```sql\nSELECT city FROM customers;\n```", known_tables=TABLES)
    assert "```" not in sql
    assert not sql.rstrip().endswith(";")


def test_existing_small_limit_is_kept():
    sql = validate_sql("SELECT * FROM customers LIMIT 5", known_tables=TABLES)
    assert "LIMIT 5" in sql.upper()


def test_oversized_limit_is_clamped():
    sql = validate_sql("SELECT * FROM customers LIMIT 999999", known_tables=TABLES)
    assert f"LIMIT {settings.max_query_rows}" in sql
    assert "999999" not in sql


@pytest.mark.parametrize(
    "bad_sql",
    [
        "DROP TABLE customers",
        "DELETE FROM customers",
        "UPDATE customers SET city = 'X'",
        "INSERT INTO customers (full_name) VALUES ('x')",
        "TRUNCATE customers",
        "ALTER TABLE customers ADD COLUMN x int",
    ],
)
def test_non_select_statements_are_blocked(bad_sql):
    with pytest.raises(DangerousSQLError):
        validate_sql(bad_sql, known_tables=TABLES)


def test_statement_chaining_is_blocked():
    with pytest.raises(DangerousSQLError):
        validate_sql("SELECT * FROM customers; DROP TABLE customers", known_tables=TABLES)


def test_empty_sql_is_blocked():
    with pytest.raises(DangerousSQLError):
        validate_sql("   ", known_tables=TABLES)


def test_unknown_table_is_blocked():
    with pytest.raises(DangerousSQLError):
        validate_sql("SELECT * FROM users", known_tables=TABLES)


def test_join_across_known_tables_passes():
    sql = validate_sql(
        "SELECT c.full_name, COUNT(o.order_id) AS n "
        "FROM customers c JOIN orders o ON o.customer_id = c.customer_id "
        "GROUP BY c.full_name",
        known_tables=TABLES,
    )
    assert "JOIN" in sql.upper()


def test_column_names_containing_keywords_are_not_false_positives():
    # 'created_at' / 'updated_at' contain CREATE / UPDATE as substrings only.
    sql = validate_sql("SELECT created_at, updated_at FROM customers", known_tables={"customers"})
    assert "created_at" in sql


# ---- regressions found while hardening the validator ----

def test_cte_query_is_allowed():
    sql = validate_sql(
        "WITH t AS (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) "
        "SELECT * FROM t",
        known_tables=TABLES,
    )
    assert sql.upper().startswith("WITH")


def test_cte_cannot_hide_an_unknown_table():
    with pytest.raises(DangerousSQLError):
        validate_sql(
            "WITH t AS (SELECT * FROM users) SELECT * FROM t",
            known_tables=TABLES,
        )


def test_keyword_inside_string_literal_is_not_blocked():
    sql = validate_sql("SELECT * FROM customers WHERE city = 'Delete'", known_tables=TABLES)
    assert "'Delete'" in sql


def test_limit_text_inside_string_literal_does_not_skip_limit():
    sql = validate_sql("SELECT * FROM customers WHERE city = 'limit 5'", known_tables=TABLES)
    assert f"LIMIT {settings.max_query_rows}" in sql


@pytest.mark.parametrize(
    "bad_sql",
    [
        "SELECT pg_sleep(30)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * INTO newtbl FROM customers",
    ],
)
def test_dangerous_functions_and_select_into_are_blocked(bad_sql):
    with pytest.raises(DangerousSQLError):
        validate_sql(bad_sql, known_tables=TABLES)


# ---- table-allowlist bypasses found by probing the original extractor ----

@pytest.mark.parametrize(
    "bad_sql",
    [
        # unknown table hidden inside a subquery in WHERE
        "SELECT * FROM customers c WHERE EXISTS (SELECT 1 FROM secret_table s WHERE s.id = c.customer_id)",
        # unknown table inside a scalar subquery in the select list
        "SELECT (SELECT COUNT(*) FROM secret_table) AS n FROM customers",
        # the application's own tables, in every position
        "SELECT * FROM users",
        "SELECT * FROM public.users",
        "SELECT * FROM customers c JOIN users u ON u.id = c.customer_id",
        "SELECT (SELECT hashed_password FROM users LIMIT 1) FROM customers",
        "WITH x AS (SELECT * FROM query_history) SELECT * FROM x",
    ],
)
def test_hidden_or_internal_tables_are_blocked(bad_sql):
    with pytest.raises(DangerousSQLError):
        validate_sql(bad_sql, known_tables=TABLES)


def test_extract_from_is_not_mistaken_for_a_table():
    sql = validate_sql(
        "SELECT EXTRACT(MONTH FROM order_date) AS m, COUNT(*) FROM orders GROUP BY m",
        known_tables=TABLES,
    )
    assert "EXTRACT" in sql.upper()


def test_subquery_over_known_tables_still_passes():
    validate_sql(
        "SELECT * FROM customers WHERE customer_id IN (SELECT customer_id FROM orders)",
        known_tables=TABLES,
    )
