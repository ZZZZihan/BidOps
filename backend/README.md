# Backend

Python 3.12+, uv, FastAPI, Pydantic v2, SQLAlchemy and SQLite implementation.
Run from repository root; see README.md for setup and docs/P0-IMPLEMENTATION.md for limits.
`bidops/api.py`: read-only API; `models.py`: contract; `migrations/`: numbered schema;
`storage.py`: raw blobs/provenance; `parser.py`: deterministic rules; `sources.py` and
`fetcher.py`: permission-gated network; `cli.py`: operator commands.
LLM, RAG and Harness remain outside P0/P1.
