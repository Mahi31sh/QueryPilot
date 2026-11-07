# QueryPilot

Ask questions about a PostgreSQL database in plain English. QueryPilot retrieves the relevant part of the schema, asks an LLM (Groq API) to write the SQL, checks that SQL against a guardrail layer, runs it read-only, and returns the rows, a plain-English explanation, and a suggested chart.

> Status: runs locally. Not deployed. The demo database is a small e-commerce dataset (5 tables).

[Demo](#demo) · [Screenshots](#screenshots) · [Architecture](#architecture) · [Run locally](#installation)

## Demo

[![Watch the QueryPilot demo ](docs/screenshots/query-interface.png)](https://drive.google.com/file/d/1WiFDdPuK8mWqYeYjtMcccDUVewD44B9o/view?usp=sharing)

*Click to watch the 2 min 50 s walkthrough: asking a question in English, the generated SQL, the chart, and a destructive request being blocked.*

## Screenshots

**Login and registration**

![Login and registration](docs/screenshots/login.png)

**Query interface and SQL results**

![Query interface and SQL results](docs/screenshots/query-interface.png)

**Dashboard**

![Dashboard](docs/screenshots/dashboard.png)
## Problem

Business users cannot query a database without knowing SQL, and handing an LLM unrestricted database access is unsafe: it can generate destructive statements, reference tables that do not exist, or return unbounded results.

## Solution

A FastAPI backend that puts the LLM inside a pipeline of controls:

1. **Retrieve** the schema chunks most relevant to the question (FAISS vector search, with keyword fallback).
2. **Generate** a single SELECT statement with the Groq API, using only that schema context.
3. **Validate** the SQL before it touches the database.
4. **Execute** it in a read-only transaction with a timeout and a row cap.
5. **Self-correct**: if validation or execution fails, the error is sent back to the LLM and it retries (default 2 retries).
6. **Explain and visualise**: the LLM explains the query in plain English and picks a chart type; the React frontend renders it.

## Key features

- Natural-language-to-SQL on PostgreSQL using the Groq API
- Schema retrieval: `sentence-transformers` (`all-MiniLM-L6-v2`) embeddings + FAISS index, with automatic fallback to keyword matching (`RETRIEVAL_MODE=keyword` skips the embedding model entirely)
- SQL guardrails (`backend/app/services/sql_validator.py`):
  - single statement, SELECT/WITH only
  - keyword denylist (DELETE, UPDATE, DROP, ALTER, INSERT, INTO, ...) applied outside string literals
  - blocked server-side functions (`pg_sleep`, `pg_read_file`, `dblink`, ...)
  - table allowlist based on the retrieved schema, including tables inside CTEs and subqueries
  - QueryPilot's own tables (`users`, `query_history`) are never reachable
  - LIMIT enforced and clamped
- Execution safety: `SET TRANSACTION READ ONLY`, per-query `statement_timeout`, hard row cap
- Self-correction loop with configurable retry limit
- JWT authentication (bcrypt password hashing), per-user query history
- React + TypeScript frontend: chat interface, suggested questions, bar / line / pie / scatter / KPI / table results (Recharts), CSV download, dark mode, history page, dashboard of usage stats (total queries, success rate, average retries, rows returned)
- Unit tests for the SQL validator and retrieval logic (36 tests, no database, API key or model download required)

## Tech stack

| Layer | Tools |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy, Pydantic, python-jose, passlib |
| LLM | Groq API (`groq` SDK), model set by `LLM_MODEL` |
| Retrieval | sentence-transformers, FAISS (`faiss-cpu`) |
| SQL safety | sqlparse + regex checks, read-only transactions |
| Database | PostgreSQL |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, TanStack Query, Recharts |
| Tests | pytest |

## Architecture

```mermaid
flowchart LR
    U["User"] --> FE["React + TypeScript (Vite)"]
    FE -->|"JWT, /api/*"| API["FastAPI backend"]
    API --> RET["Schema retrieval"]
    RET --> IDX[("FAISS index + schema metadata")]
    RET --> EMB["MiniLM embeddings"]
    API --> LLM["Groq LLM"]
    API --> VAL["SQL validator"]
    VAL --> EXE["Read-only executor"]
    EXE --> PG[("PostgreSQL analytics tables")]
    API --> APPDB[("users, query_history")]
```

## Workflow

```mermaid
sequenceDiagram
    participant U as User
    participant A as FastAPI
    participant R as Retrieval
    participant L as Groq LLM
    participant V as Validator
    participant D as PostgreSQL
    U->>A: POST /api/query (question, JWT)
    A->>R: top-k relevant tables
    R-->>A: schema context
    A->>L: generate SQL (question + schema context)
    L-->>A: SQL
    loop up to SQL_RETRY_LIMIT + 1 attempts
        A->>V: validate SQL
        V-->>A: cleaned SQL or rejection
        A->>D: execute (read-only, timeout, row cap)
        D-->>A: rows or error
        A->>L: on failure: send error, request corrected SQL
    end
    A->>L: explain SQL, choose chart type
    A-->>U: SQL, explanation, rows, chart spec
```

## Project structure

```
QueryPilot/
├── backend/
│   ├── app/
│   │   ├── main.py            # app assembly, CORS, routers
│   │   ├── config.py          # typed settings from environment
│   │   ├── prompts.py         # all LLM prompt templates
│   │   ├── routers/           # auth, query, history, schema, health
│   │   └── services/
│   │       ├── embeddings.py  # FAISS build + vector/keyword search
│   │       ├── rag.py         # schema context assembly
│   │       ├── sql_generator.py, llm_client.py
│   │       ├── sql_validator.py, sql_executor.py
│   │       └── chart_generator.py, history_service.py
│   ├── tests/                 # validator + retrieval tests
│   ├── embeddings/vector_store/   # generated FAISS index + metadata
│   ├── requirements.txt
│   └── .env.example
├── database/                  # schema.sql, seed_data.sql, seed.py
├── embeddings/build_index.py  # builds the FAISS index from the live schema
└── frontend/                  # React + TypeScript (Vite)
```

## Installation

Prerequisites: Python 3.11, Node.js 18+, PostgreSQL, a [Groq API key](https://console.groq.com).

```bash
# 1. Database
createdb querypilot            # or create it with your PostgreSQL tool of choice

# 2. Backend
cd backend
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # then edit .env (see below)

# 3. Frontend
cd ../frontend
npm install
```

## Environment variables

Set these in `backend/.env`.

| Variable | Purpose | Default / example |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://USER:PASSWORD@localhost:5432/querypilot` |
| `ANALYTICS_DATABASE_URL` | Optional separate connection used only to run generated SQL. Recommended: a role with SELECT on the analytics tables only | falls back to `DATABASE_URL` |
| `JWT_SECRET_KEY` | JWT signing secret | generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GROQ_API_KEY` | Groq API key | required |
| `LLM_MODEL` | Groq model id | see `.env.example` |
| `RETRIEVAL_MODE` | `vector` (FAISS) or `keyword` (no embedding model) | `vector` |
| `FAISS_INDEX_PATH` | Index location, relative to the directory you start the backend from | `./embeddings/vector_store/schema_index` |
| `MAX_QUERY_ROWS` | Row cap on results | `500` |
| `SQL_RETRY_LIMIT` | LLM self-correction retries | `2` |
| `STATEMENT_TIMEOUT_MS` | Per-query execution limit | `10000` |
| `CORS_ORIGINS` | Allowed frontend origins | `http://localhost:5173` |

Never commit `.env`.

## Run

Run all backend commands from the `backend/` directory.

```bash
# Create tables and load the demo e-commerce data
python ../database/seed.py

# Build the schema index (first run downloads the all-MiniLM-L6-v2 model)
python ../embeddings/build_index.py

# Start the API on http://localhost:8000  (docs at /docs)
uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```bash
cd frontend
npm run dev                    # http://localhost:5173, proxies /api to port 8000
```

Open the app, register an account, and ask a question.


## Sample questions

Run against the demo e-commerce data:

- What is the total revenue from completed orders?
- Show me revenue by product category
- Which 5 customers have spent the most?
- How many orders were placed each month?
- What is the average order value by country?

### API response shape

`POST /api/query` with `{"question": "..."}` and an `Authorization: Bearer <token>` header returns:

```json
{
  "question": "string",
  "generated_sql": "string (validated, with LIMIT applied)",
  "explanation": "plain-English description of the query",
  "columns": ["..."],
  "rows": [{"...": "..."}],
  "row_count": 0,
  "chart": {"chart_type": "bar | pie | line | scatter | table | kpi", "data": [], "x_key": "string|null", "y_key": "string|null"},
  "retry_count": 0,
  "execution_time_ms": 0.0
}
```

## Testing

```bash
cd backend
pytest
```

The suite covers the SQL validator (destructive statements, statement chaining, hidden tables in CTEs and subqueries, protected tables, dangerous functions, LIMIT handling, false-positive cases) and schema retrieval (vector ranking, keyword mode, fallback behaviour). It uses dummy environment values and a small fake embedder, so it needs no database, Groq key, or model download. The full request path (LLM + PostgreSQL) is not covered by automated tests.

## Limitations

- The demo schema has 5 tables, so retrieval is not stressed; behaviour on large schemas is untested.
- No evaluation set: SQL accuracy has not been measured, and LLM output is non-deterministic.
- The validator is text-based (sqlparse + regex), not a full SQL parser. Real isolation should come from a restricted database role (`ANALYTICS_DATABASE_URL`); by default generated SQL runs on the same database as the app tables.
- Registration is open and there is no rate limiting; each question typically makes 3 LLM calls (generate, explain, chart type) plus any retries.
- JWT is stored in `localStorage`.
- App tables are created with `create_all`, not migrations.
- No Docker setup and no live deployment.

## Future improvements

- Evaluation set of question / expected-result pairs to measure accuracy
- Rate limiting and per-user quotas
- Dockerfile and docker-compose for one-command setup
- Alembic migrations
- httpOnly-cookie auth
- Deployment (free-tier host) once the above are in place
