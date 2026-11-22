"""The frontend's committed OpenAPI snapshot is the API as it is now. A route change without a refreshed snapshot fails
here, which is what keeps the generated frontend types and the typed client from drifting."""

import json
from pathlib import Path

from linkedin_agent.main import app

SNAPSHOT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def test_the_committed_openapi_snapshot_matches_the_api() -> None:
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(app.openapi(), ensure_ascii=False))
    assert committed == current, (
        "frontend/openapi.json is out of date: run "
        "`python scripts/dump_openapi.py --write ../frontend/openapi.json` in backend/, then "
        "`npm run gen:types` in frontend/, and commit both."
    )
