"""Automatic backups of the open event."""

import os
import sqlite3

import app as appmod
import backups
import db
import store


def test_backup_now_writes_openable_snapshots(cfg, tmp_path):
    cfg.save({"backup_dir": str(tmp_path / "a"), "backup_dir_2": str(tmp_path / "b"),
              "backup_keep": 2})
    files = backups.backup_now()
    assert len(files) == 2 and all(os.path.exists(f) for f in files)
    snap = files[0]
    conn = sqlite3.connect(snap)
    assert conn.execute("SELECT COUNT(*) FROM competitors").fetchone()[0] > 0
    conn.close()
    assert db.read_event_meta(snap)["name"] == store.EVENT["name"]


def test_prune_keeps_newest(cfg, tmp_path):
    folder = tmp_path / "p"
    folder.mkdir()
    # The oldest is from before the rename (.bmeos): pruned all the same.
    (folder / "ev-20260101-100000.bmeos").write_bytes(b"")
    for stamp in ("20260101-100100", "20260101-100200"):
        (folder / f"ev-{stamp}.ctrl").write_bytes(b"")
    cfg.save({"backup_keep": 2})
    backups._prune(str(folder), "ev")
    assert sorted(os.listdir(folder)) == ["ev-20260101-100100.ctrl", "ev-20260101-100200.ctrl"]


def test_maybe_backup_only_when_changed(cfg, tmp_path):
    cfg.save({"backup_dir": str(tmp_path / "m"), "backup_interval_minutes": 1})
    backups._state.update(revision=db.revision(), at=0.0)
    assert backups.maybe_backup() is False            # nothing changed
    db.mark_changed()
    assert backups.maybe_backup() is True             # changed + interval passed
    db.mark_changed()
    assert backups.maybe_backup() is False            # changed, but too soon


def test_onedrive_detection_and_setup_page(cfg, tmp_path):
    assert backups.in_synced_folder(r"C:\Users\q\OneDrive - school\.control-orienteering\events")
    assert not backups.in_synced_folder(r"C:\control-orienteering\events")
    cfg.save({"backup_dir": str(tmp_path / "s")})
    c = appmod.app.test_client()
    assert c.post("/api/backups/now").status_code == 200
    assert "Back up now" in c.get("/setup").get_data(as_text=True)
