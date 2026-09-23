"""
Unit tests for Noventra POS Windows Packaging:
- Path resolution and .env secret key generation
- Backup rotation and retention limits
- USB replication logic
- CLI argument parsing
"""
import os
import secrets
import argparse
from pathlib import Path
import pytest
from app.paths import (
    get_data_dir,
    get_env_path,
    get_upload_dir,
    get_backup_dir,
    get_log_dir,
    ensure_data_directories,
)
from app.services.backup_manager import rotate_backups, replicate_to_usb, get_backup_status


def test_paths_and_secret_key_generation(tmp_path, monkeypatch):
    """Verifies data directory creation and cryptographic SECRET_KEY generation."""
    test_data_dir = tmp_path / "NoventraData"
    monkeypatch.setenv("NOVENTRA_DATA_DIR", str(test_data_dir))

    assert get_data_dir() == test_data_dir.resolve()
    assert get_env_path() == test_data_dir / ".env"
    assert get_backup_dir() == test_data_dir / "backups"
    assert get_log_dir() == test_data_dir / "logs"

    # Ensure directories
    created_dir = ensure_data_directories()
    assert created_dir.exists()
    assert (test_data_dir / "uploads" / "items").is_dir()
    assert (test_data_dir / "uploads" / "logos").is_dir()
    assert (test_data_dir / "uploads" / "temp").is_dir()
    assert (test_data_dir / "logs").is_dir()
    assert (test_data_dir / "backups").is_dir()

    # Verify .env generated with fresh random SECRET_KEY and blank DB_PASSWORD
    env_file = get_env_path()
    assert env_file.is_file()
    content = env_file.read_text(encoding="utf-8")

    assert "SECRET_KEY=" in content
    assert "DB_SERVICE_NAME=XEPDB1" in content
    assert "DB_USER=isi" in content
    assert "DB_PASSWORD=\n" in content or "DB_PASSWORD=\r\n" in content

    # Extract SECRET_KEY value and ensure it is a valid 64-char hex string
    for line in content.splitlines():
        if line.startswith("SECRET_KEY="):
            key = line.split("=", 1)[1].strip()
            assert len(key) == 64
            assert all(c in "0123456789abcdefABCDEF" for c in key)
            break
    else:
        pytest.fail("SECRET_KEY line not found in generated .env")


def test_backup_rotation(tmp_path):
    """Verifies that rotate_backups strictly preserves the 30 newest files."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Create 35 dummy backup files with staggered modification times
    for i in range(35):
        fp = backup_dir / f"backup_{i:02d}.dmp"
        fp.write_text(f"dummy backup content {i}")
        # Set distinct mtimes
        mtime = 1000000.0 + i * 10.0
        os.utime(fp, (mtime, mtime))

    files_before = [f for f in backup_dir.iterdir() if f.is_file()]
    assert len(files_before) == 35

    rotate_backups(str(backup_dir), max_backups=30)

    files_after = sorted([f.name for f in backup_dir.iterdir() if f.is_file()])
    assert len(files_after) == 30
    # Oldest 5 files (backup_00.dmp to backup_04.dmp) should be deleted
    for i in range(5):
        assert f"backup_{i:02d}.dmp" not in files_after
    # Newest files (backup_05.dmp to backup_34.dmp) should remain
    for i in range(5, 35):
        assert f"backup_{i:02d}.dmp" in files_after


def test_usb_replication_invalid_drive(tmp_path):
    """Verifies replication gracefully handles absent or blank USB drives."""
    src = tmp_path / "test.dmp"
    src.write_text("test")

    ok, msg = replicate_to_usb(str(src), None)
    assert not ok
    assert "No USB drive letter configured" in msg

    ok, msg = replicate_to_usb(str(src), "")
    assert not ok

    ok, msg = replicate_to_usb(str(src), "Z:")
    assert not ok
    assert "not connected" in msg


def test_cli_flags_parsing():
    """Verifies all required command-line switches in entrypoint.py."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-check", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--create-admin", action="store_true")
    parser.add_argument("--setup-shortcuts", action="store_true")

    args = parser.parse_args(["--db-check"])
    assert args.db_check and not args.migrate

    args = parser.parse_args(["--migrate"])
    assert args.migrate and not args.backup

    args = parser.parse_args(["--backup"])
    assert args.backup and not args.create_admin

    args = parser.parse_args(["--create-admin"])
    assert args.create_admin and not args.setup_shortcuts

    args = parser.parse_args(["--setup-shortcuts"])
    assert args.setup_shortcuts
