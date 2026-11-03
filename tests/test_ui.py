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
    me = client.get("/api/me", headers=h("reviewer")).json()
    assert me["name"] == "rita-reviewer" and me["role"] == "reviewer"
    assert set(me["permissions"]) == {"read", "approve_facts", "review"}
    assert me["required_approval_roles"] == ["reviewer"]
    assert "author" in client.get("/api/me", headers=h("author")).json()["permissions"]


def test_samples_require_auth_and_match_topic_schema(client):
    assert client.get("/api/samples").status_code == 401
    samples = client.get("/api/samples", headers=h("author")).json()
    assert {s["topic"] for s in samples} == {"Heart", "Brain"}
    assert all("_comment" not in s for s in samples)
    created = client.post("/api/topics", json=samples[0], headers=h("author"))
    assert created.status_code == 201


def test_health_reports_local_mode(tmp_path, mock):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import make_settings

    app = create_app(make_settings(tmp_path, auth_mode="dev", dev_auth_enabled=True), provider_factory=lambda: mock)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["local_mode"] is True
        me = c.get("/api/me", headers={"X-Dev-User": "Reviewer:reviewer"}).json()
        assert me["name"] == "Reviewer" and me["role"] == "reviewer"


def test_ui_has_no_logo_or_tagline(client):
    page = client.get("/ui/").text
    assert "brand-mark" not in page and "Approved facts in" not in page
    assert 'value="reviewer"' in page and "medical_reviewer" not in page and "educator" not in page
