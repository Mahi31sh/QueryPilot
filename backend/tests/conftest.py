"""
Test configuration. Dummy environment values are set *before* any app module
is imported, because app.config builds its Settings object at import time.
No database, Groq key, or embedding model is needed to run these tests.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-production")
os.environ.setdefault("GROQ_API_KEY", "test-key")
