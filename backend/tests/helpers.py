"""Shared helpers for API tests.

Preparations run in the background. The helper starts one, checks that it paused for review, and returns the
application it produced, so that tests can keep working on an application.
"""

from typing import Any


def prepare_via_api(client: Any, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/applications", json=body, headers=headers)
    assert response.status_code == 202, response.text
    run = client.get(f"/applications/preparations/{response.json()['run_id']}", headers=headers).json()
    assert run["status"] == "awaiting_review", run
    application: dict[str, Any] = client.get(f"/applications/{run['application_id']}", headers=headers).json()
    return application
