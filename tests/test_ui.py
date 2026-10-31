"""Web UI is served, and the helper endpoints it relies on behave correctly."""

from __future__ import annotations

from tests.conftest import h


def test_root_redirects_to_ui_and_assets_are_served(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/ui/"
    page = client.get("/ui/")
    assert page.status_code == 200 and "MEDKids Rap Studio" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    for asset in ("app.js", "styles.css"):
        assert client.get(f"/ui/{asset}").status_code == 200


def test_ui_does_not_use_innerhtml_for_server_data(client):
    js = client.get("/ui/app.js").text
    assert ".innerHTML" not in js and ".outerHTML" not in js and "insertAdjacentHTML" not in js


def test_me_reports_role_and_permissions(client):
    assert client.get("/api/me").status_code == 401
    me = client.get("/api/me", headers=h("medical")).json()
    assert me["name"] == "dr-mo" and me["role"] == "medical_reviewer"
    assert set(me["permissions"]) == {"read", "approve_facts", "review"}
    assert me["required_approval_roles"] == ["medical_reviewer", "educator"]
    assert "author" in client.get("/api/me", headers=h("author")).json()["permissions"]


def test_samples_require_auth_and_match_topic_schema(client):
    assert client.get("/api/samples").status_code == 401
    samples = client.get("/api/samples", headers=h("author")).json()
    assert {s["topic"] for s in samples} == {"Heart", "Brain"}
    assert all("_comment" not in s for s in samples)
    created = client.post("/api/topics", json=samples[0], headers=h("author"))
    assert created.status_code == 201
