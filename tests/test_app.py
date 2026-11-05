"""Smoke tests for routes and auth. Runs against a temp data/ copy so real users are untouched."""
import json
import sys
import types

import pytest
from werkzeug.security import generate_password_hash

# downloader pulls scenedetect/opencv, not needed to test routes
sys.modules.setdefault("core.downloader", types.SimpleNamespace(download_video=lambda *a, **k: None))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import main
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.mp4").write_bytes(b"\x00")
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"admin": generate_password_hash("pw")}))
    monkeypatch.setattr(main, "USERS_JSON", users)
    monkeypatch.setattr(main, "LOGIN_LOG", tmp_path / "log.json")
    monkeypatch.setattr(main, "SETTINGS_JSON", tmp_path / "settings.json")
    monkeypatch.setitem(main.settings, "output_folder", str(out))
    monkeypatch.setattr(main.jobs, "submit", lambda *a, **k: "x")     # never run real work
    main.app.config["TESTING"] = True
    return main.app.test_client()


def login(c):
    return c.post("/login", data={"username": "admin", "password": "pw"})


@pytest.mark.parametrize("method,path", [
    ("get", "/section/creation"), ("post", "/create"), ("post", "/auto_confirm"), ("post", "/restore"),
    ("post", "/download"), ("post", "/clear_logs"), ("get", "/media/a.mp4"), ("post", "/library/delete"),
    ("get", "/editor/a.mp4"), ("post", "/tiktok/connect"),
])
def test_requires_login(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_bad_login(client):
    assert "invalide" in client.post("/login", data={"username": "admin", "password": "no"}).get_data(as_text=True)


@pytest.mark.parametrize("name", ["creation", "download", "library", "accounts", "settings", "stats", "admin"])
def test_sections_render(client, name):
    login(client)
    assert client.get(f"/section/{name}").status_code == 200


def test_editor_and_media(client):
    login(client)
    assert client.get("/editor/a.mp4").status_code == 200
    assert client.get("/media/a.mp4").status_code == 200
    assert client.get("/media/..%2Fusers.json").status_code == 404
    assert client.get("/editor/missing.mp4").status_code == 404


def test_create_bad_input_does_not_500(client):
    login(client)
    assert client.post("/create", data={"number": "", "cible": "abc"}).status_code == 302


def test_export_and_delete(client):
    login(client)
    r = client.post("/editor/a.mp4/export", data={"video_vol": "80", "texts": '[{"text":"hi","start":0,"end":2}]'})
    assert r.status_code == 302
    client.post("/library/delete", data={"name": "a.mp4"})
    assert client.get("/media/a.mp4").status_code == 404


def test_settings_requires_absolute_path(client, tmp_path):
    login(client)
    client.post("/settings/folders", data={"output_folder": "relative/dir"})
    import main
    assert main.settings["output_folder"] != "relative/dir"
    client.post("/settings/folders", data={"output_folder": str(tmp_path / "new")})
    assert main.settings["output_folder"] == str(tmp_path / "new")


def test_tiktok_callback_rejects_bad_state(client):
    login(client)
    with client.session_transaction() as s:
        s["tt_state"], s["tt_verifier"] = "good", "v"
    r = client.get("/tiktok/callback/?state=evil&code=x", follow_redirects=True)
    assert "invalide" in r.get_data(as_text=True)
