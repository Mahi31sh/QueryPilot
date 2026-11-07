
from sqlalchemy import text
from app.services.sql_executor import _target_engine
from app.services.sql_validator import validate_sql

passed = 0
failed = 0

def check(name, condition):
    global passed, failed
    if condition:
        print(f"PASS: {name}")
        passed += 1
    else:
        print(f"FAIL: {name}")
        failed += 1

# 1. Confirm restricted database account
with _target_engine.connect() as conn:
    check("Connected as querypilot_ro",
          conn.execute(text("SELECT current_user")).scalar() == "querypilot_ro")

    check("Products table readable",
          conn.execute(text("SELECT COUNT(*) FROM products")).scalar() >= 0)

    check("Users table inaccessible",
          not conn.execute(text(
              "SELECT has_table_privilege(current_user, 'public.users', 'SELECT')"
          )).scalar())

    check("Query history inaccessible",
          not conn.execute(text(
              "SELECT has_table_privilege(current_user, 'public.query_history', 'SELECT')"
          )).scalar())

# 2. SQL validator checks
def is_blocked(query):
    try:
        validate_sql(query)
        return False
    except Exception:
        return True

check("DELETE rejected", is_blocked("DELETE FROM products"))
check("pg_sleep rejected", is_blocked("SELECT pg_sleep(30)"))
check("Protected users table rejected",
      is_blocked("SELECT * FROM users"))
check("Protected query_history rejected",
      is_blocked("SELECT * FROM query_history"))
check("Normal SELECT accepted",
      not is_blocked("SELECT * FROM products"))

print(f"\nResults: {passed}/9 passed, {failed}/9 failed")
