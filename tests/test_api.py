"""HTTP API: endpoints, status codes, authN/authZ, workflow transitions, safe errors."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import LLMAuthenticationError
from app.main import create_app
from tests.conftest import h, load_sample, make_settings
from tests.test_pipeline import rap_json


def create_topic(client, name="heart") -> int:
    r = client.post("/api/topics", json=load_sample(name), headers=h("author"))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def ready_topic(client) -> int:
    tid = create_topic(client)
    ex = client.post(f"/api/topics/{tid}/facts/extract", headers=h("author"))
    assert ex.status_code == 200
    ids = [f["fact_key"] for f in ex.json()["candidates"]]
    ap = client.post(f"/api/topics/{tid}/facts/approve", json={"approve_fact_ids": ids}, headers=h("medical"))
    assert ap.status_code == 200 and ap.json()["approved_fact_count"] == len(ids)
    return tid


def generate(client, tid, **kw) -> dict:
    r = client.post("/api/raps/generate", json={"topic_id": tid, **kw}, headers=h("author"))
    assert r.status_code == 201, r.text
    return r.json()["raps"][0]


def review(client, rap_id, who, decision, **kw):
    return client.post(f"/api/raps/{rap_id}/review", json={"decision": decision, **kw}, headers=h(who))


# --- basics -------------------------------------------------------------------------------------

def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["database"] == "ok"
    assert "X-Request-ID" in r.headers


def test_openapi_lists_all_required_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ["/api/topics", "/api/topics/{topic_id}", "/api/topics/{topic_id}/facts/extract",
              "/api/topics/{topic_id}/facts/approve", "/api/raps/generate", "/api/raps", "/api/raps/{rap_id}",
              "/api/raps/{rap_id}/validate", "/api/raps/{rap_id}/evaluate", "/api/raps/{rap_id}/revise",
              "/api/raps/{rap_id}/review", "/api/raps/{rap_id}/history", "/api/health"]:
        assert p in paths, p


@pytest.mark.parametrize("method,path", [("get", "/api/topics"), ("get", "/api/raps"), ("get", "/api/raps/1"),
                                         ("post", "/api/raps/1/review"), ("get", "/api/raps/1/history")])
def test_unpublished_content_requires_authentication(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
    assert getattr(client, method)(path, headers={"X-API-Key": "wrong-key-000000000"}).status_code == 401


def test_dev_header_rejected_unless_dev_mode(client):
    assert client.get("/api/topics", headers={"X-Dev-User": "eve:medical_reviewer"}).status_code == 401


def test_dev_mode_works_only_when_explicitly_enabled(tmp_path, mock):
    app = create_app(make_settings(tmp_path, auth_mode="dev", dev_auth_enabled=True), provider_factory=lambda: mock)
    with TestClient(app) as c:
        assert c.get("/api/topics", headers={"X-Dev-User": "eve:educator"}).status_code == 200
        assert c.get("/api/topics", headers={"X-Dev-User": "eve:superuser"}).status_code == 401


def test_input_validation_errors_are_422(client):
    bad = load_sample("heart") | {"target_age_min": 9, "target_age_max": 5}
    r = client.post("/api/topics", json=bad, headers=h("author"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "request_validation_failed"
    r = client.post("/api/topics", json=load_sample("heart") | {"category": "Cooking"}, headers=h("author"))
    assert r.status_code == 422


def test_oversized_payload_rejected(client):
    huge = load_sample("heart") | {"educational_content": "a" * 600_000}
    assert client.post("/api/topics", json=huge, headers=h("author")).status_code == 413


def test_source_over_configured_limit_rejected(tmp_path, mock):
    app = create_app(make_settings(tmp_path, max_source_chars=100), provider_factory=lambda: mock)
    with TestClient(app) as c:
        r = c.post("/api/topics", json=load_sample("heart"), headers=h("author"))
        assert r.status_code == 422 and r.json()["error"]["code"] == "input_rejected"


def test_role_authorization(client):
    tid = create_topic(client)
    for who in ("medical", "educator"):  # reviewers cannot author content
        assert client.post("/api/topics", json=load_sample("brain"), headers=h(who)).status_code == 403
    ex = client.post(f"/api/topics/{tid}/facts/extract", headers=h("author")).json()
    ids = [f["fact_key"] for f in ex["candidates"]]
    for who in ("author", "educator", "admin"):  # only medical reviewers approve facts
        r = client.post(f"/api/topics/{tid}/facts/approve", json={"approve_fact_ids": ids}, headers=h(who))
        assert r.status_code == 403
    assert client.post(f"/api/topics/{tid}/facts/approve", json={"approve_fact_ids": ids},
                       headers=h("medical")).status_code == 200


def test_not_found(client):
    assert client.get("/api/topics/999", headers=h("author")).status_code == 404
    assert client.get("/api/raps/999", headers=h("author")).status_code == 404


# --- full workflow ------------------------------------------------------------------------------

def test_full_review_workflow_requires_both_human_approvals(client, tmp_path):
    tid = ready_topic(client)
    rap = generate(client, tid)
    rid = rap["id"]
    assert rap["status"] == "pending_review"
    assert rap["medical_validation"]["status"] in {"passed", "requires_review"}
    assert rap["evaluation"]["total_score"] > 0 and rap["is_demo_data"] is True

    assert review(client, rid, "author", "approve").status_code == 403  # authors are not reviewers
    assert client.get(f"/api/raps/{rid}/export", headers=h("author")).status_code == 409

    r = review(client, rid, "medical", "approve", feedback="Facts match the approved card.")
    assert r.status_code == 200 and r.json()["rap"]["status"] == "pending_review"
    assert r.json()["rap"]["approvals_for_current_version"] == ["medical_reviewer"]
    assert review(client, rid, "medical2", "approve").status_code == 409  # same role twice

    r = review(client, rid, "educator", "approve", feedback="Age-appropriate.")
    assert r.status_code == 200 and r.json()["rap"]["status"] == "approved"

    export = client.get(f"/api/raps/{rid}/export", headers=h("author"))
    assert export.status_code == 200 and export.json()["approvals"] == {
        "medical_reviewer": "dr-mo", "educator": "ed-teacher"}
    files = list((tmp_path / "outputs" / "approved").glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["rap_id"] == rid

    assert review(client, rid, "medical", "reject", feedback="changed my mind").status_code == 409  # terminal
    hist = client.get(f"/api/raps/{rid}/history", headers=h("educator")).json()
    assert [e["to_status"] for e in hist["events"]][-1] == "approved"
    assert len(hist["reviews"]) == 2 and hist["versions"][0]["prompt_sha256"]


def test_cannot_approve_rap_with_failing_validation(client, mock):
    tid = ready_topic(client)
    mock.queue("rap_generation", rap_json())
    rap = generate(client, tid, auto_revise=False)
    assert rap["status"] == "validation_failed"
    r = review(client, rap["id"], "medical", "approve")
    assert r.status_code == 409 and "approval blocked" in r.json()["error"]["message"]
    assert "unresolved critical/major medical accuracy errors" in r.json()["error"]["details"]["blockers"][0] \
        or "pending_review" in r.json()["error"]["details"]["blockers"][0]


def test_validate_evaluate_revise_endpoints(client, mock):
    tid = ready_topic(client)
    mock.queue("rap_generation", rap_json())
    rap = generate(client, tid, run_pipeline=False)
    rid = rap["id"]
    assert rap["status"] == "draft"
    assert client.post(f"/api/raps/{rid}/evaluate", headers=h("author")).status_code == 409  # validate first
    v = client.post(f"/api/raps/{rid}/validate", headers=h("author"))
    assert v.status_code == 200 and v.json()["medical_accuracy"]["status"] == "failed"
    e = client.post(f"/api/raps/{rid}/evaluate", headers=h("author"))
    assert e.status_code == 200 and e.json()["evaluation"]["hard_gates"]["medical_accuracy_not_failed"] is False
    assert e.json()["rap"]["status"] == "validation_failed"
    rv = client.post(f"/api/raps/{rid}/revise", json={"auto_validate": True}, headers=h("author"))
    assert rv.status_code == 200 and rv.json()["changed"] is True
    assert rv.json()["rap"]["status"] == "pending_review" and rv.json()["rap"]["current_version"] == 2


def test_reviewer_revision_invalidates_earlier_approval(client):
    tid = ready_topic(client)
    rid = generate(client, tid)["id"]
    assert review(client, rid, "medical", "approve").status_code == 200
    r = review(client, rid, "educator", "request_revision", feedback="Line too long for 4-year-olds",
               issues=[{"line_id": "v1_l03", "severity": "major", "reason": "too long"}])
    assert r.status_code == 200 and r.json()["rap"]["status"] == "revision_requested"
    assert review(client, rid, "educator", "approve").status_code == 409  # not pending_review
    rv = client.post(f"/api/raps/{rid}/revise", headers=h("author"))
    assert rv.status_code == 200
    detail = rv.json()["rap"]
    assert detail["current_version"] == 2 and detail["status"] == "pending_review"
    assert detail["approvals_for_current_version"] == []  # medical approval of v1 no longer counts


def test_revision_request_without_line_issues_cannot_auto_revise(client):
    tid = ready_topic(client)
    rid = generate(client, tid)["id"]
    review(client, rid, "educator", "request_revision", feedback="Make it funnier overall")
    r = client.post(f"/api/raps/{rid}/revise", headers=h("author"))
    assert r.status_code == 422 and "line-level" in r.json()["error"]["message"]


def test_critical_reviewer_comment_blocks_approval(client):
    tid = ready_topic(client)
    rid = generate(client, tid)["id"]
    c = review(client, rid, "medical", "comment", issues=[{"line_id": "v1_l01", "severity": "critical",
                                                          "reason": "misleading for this age"}])
    assert c.status_code == 200
    r = review(client, rid, "educator", "approve")
    assert r.status_code == 409 and "critical issues" in r.json()["error"]["message"]


def test_reject_is_terminal(client):
    tid = ready_topic(client)
    rid = generate(client, tid)["id"]
    assert review(client, rid, "educator", "reject", feedback="Off-topic").json()["rap"]["status"] == "rejected"
    assert client.post(f"/api/raps/{rid}/validate", headers=h("author")).status_code == 409
    assert client.post(f"/api/raps/{rid}/revise", headers=h("author")).status_code == 409


def test_generate_without_approved_facts_conflict(client):
    tid = create_topic(client)
    r = client.post("/api/raps/generate", json={"topic_id": tid}, headers=h("author"))
    assert r.status_code == 409 and "approved facts" in r.json()["error"]["message"]


def test_list_filters(client):
    tid = ready_topic(client)
    generate(client, tid)
    assert len(client.get("/api/raps", params={"status": "pending_review"}, headers=h("author")).json()) == 1
    assert client.get("/api/raps", params={"status": "approved"}, headers=h("author")).json() == []
    assert client.get("/api/raps", params={"status": "bogus"}, headers=h("author")).status_code == 422
    assert [t["slug"] for t in client.get("/api/topics", headers=h("educator")).json()] == ["heart"]


def test_llm_failures_return_safe_errors(client, mock):
    tid = ready_topic(client)
    mock.queue("age_simplification", LLMAuthenticationError("sk-ant-secret-key-should-not-leak"))
    mock.queue("rap_generation", LLMAuthenticationError("sk-ant-secret-key-should-not-leak"))
    r = client.post("/api/raps/generate", json={"topic_id": tid}, headers=h("author"))
    assert r.status_code == 502
    body = r.json()["error"]
    assert body["code"] == "llm_auth_failed" and "sk-ant" not in r.text and body["details"] == {}


def test_malformed_generation_output_maps_to_502(client, mock):
    tid = ready_topic(client)
    mock.queue("rap_generation", *["garbage"] * 3)
    r = client.post("/api/raps/generate", json={"topic_id": tid}, headers=h("author"))
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_invalid_output"
