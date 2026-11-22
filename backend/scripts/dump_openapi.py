"""Write the backend's OpenAPI description as JSON, without starting the server or the database.

Used by the frontend to check its API client against the real routes (frontend/src/api/contract.test.ts).
Run from the backend folder: python scripts/dump_openapi.py > ../frontend/openapi.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from linkedin_agent.main import app

json.dump(app.openapi(), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
sys.stdout.write("\n")
