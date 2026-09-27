"""Online results publishing + prize list."""

import os

import app as appmod
import db
import publish


def test_publish_to_folder_is_self_contained(cfg, tmp_path):
    cfg.save({"publish_dir": str(tmp_path / "web")})
    targets = appmod.app.test_client().post("/api/publish/now").get_json()["targets"]
    assert targets == [str(tmp_path / "web")]
    html = (tmp_path / "web" / "results.html").read_text(encoding="utf-8")
    assert "<style>" in html and "/static/style.css" not in html and "location.reload" in html
    assert (tmp_path / "web" / "results.xml").read_text(encoding="utf-8").startswith("<?xml")


def test_publish_only_when_changed(cfg, tmp_path):
    cfg.save({"publish_dir": str(tmp_path / "w2"), "publish_interval_seconds": 10})
    publish._state.update(revision=db.revision(), at=0.0)
    assert publish.maybe_publish() is False
    db.mark_changed()
    assert publish.maybe_publish() is True


def test_publish_off_without_settings(cfg):
    assert appmod.app.test_client().post("/api/publish/now").status_code == 400


def test_ftp_upload_replaces_files_atomically(cfg, monkeypatch):
    calls = []

    class FakeFTP:
        def connect(self, host, port, timeout): calls.append(("connect", host, port))
        def login(self, user, pw): calls.append(("login", user))
        def cwd(self, d): calls.append(("cwd", d))
        def storbinary(self, cmd, f): calls.append(("stor", cmd))
        def delete(self, name): calls.append(("delete", name))
        def rename(self, a, b): calls.append(("rename", a, b))
        def quit(self): calls.append(("quit",))

    monkeypatch.setattr(publish.ftplib, "FTP", FakeFTP)
    cfg.save({"ftp_host": "ftp.example", "ftp_user": "u", "ftp_pass": "p",
              "ftp_dir": "results", "ftp_tls": False})
    publish._upload_ftp({"results.html": b"<html>"})
    assert ("stor", "STOR results.html.tmp") in calls
    assert ("rename", "results.html.tmp", "results.html") in calls


def test_prize_list_pdf():
    assert appmod.app.test_client().get("/export/prizes.pdf").data[:4] == b"%PDF"
