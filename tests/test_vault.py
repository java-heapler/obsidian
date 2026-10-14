import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from obsidian_vault import cli


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Course").mkdir()
    (root / "Course/python.md").write_bytes(b'---\r\ntags: [coding]\r\n---\r\nPython basics\r\nSecond Python line\r\n')
    (root / "empty.md").write_text("")
    return root


def test_read_scan_nonmutation(vault):
    before = {str(p): p.read_bytes() for p in vault.rglob("*.md")}
    note = cli.read_note(vault, "Course/python.md")
    assert note["frontmatter"] == {"tags": ["coding"]}
    assert "\r\n" in note["content"]
    assert note["sha256"] == hashlib.sha256(before[str(vault / "Course/python.md")]).hexdigest()
    assert cli.scan(vault)["total"] == 2
    first = cli.scan(vault, "PYTHON", limit=1)
    assert first["total"] == 2 and first["next_offset"] == 1
    assert first["items"][0]["line"] == 4
    assert cli.scan(vault, "PYTHON", offset=1)["items"][0]["line"] == 5
    assert before == {str(p): p.read_bytes() for p in vault.rglob("*.md")}


def test_discovery(tmp_path, vault):
    config = tmp_path / "config.json"
    assert cli.discover(config) == []
    config.write_text(json.dumps({"vaults": {"id": {"path": str(vault), "open": True}}}))
    assert cli.discover(config)[0]["exists"]
    assert cli.vault_path(None, config) == vault
    assert cli.vault_path(str(vault)) == vault
    config.write_text("{}")
    assert cli.discover(config) == []
    with pytest.raises(ValueError, match="exactly one"):
        cli.vault_path(None, config)
    with pytest.raises(ValueError, match="directory"):
        cli.vault_path(config)
    link = tmp_path / "link"
    link.symlink_to(vault)
    with pytest.raises(ValueError, match="symlink"):
        cli.vault_path(link)


@pytest.mark.parametrize("name", ["../secret.md", "/secret.md", ".obsidian/private.md", "x/.hidden.md", "x.txt", ""])
def test_unsafe_paths(vault, name):
    with pytest.raises(ValueError):
        cli.note_path(vault, name)


def test_hidden_and_symlink_skipped(vault, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    (vault / "linked").symlink_to(outside)
    (vault / "linked.md").symlink_to(outside / "secret.md")
    (vault / ".secret.md").write_text("secret")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian/hidden.md").write_text("secret")
    (vault / "other.txt").write_text("secret")
    assert cli.scan(vault, "secret")["total"] == 0
    for name in ["linked/secret.md", "linked.md"]:
        with pytest.raises(ValueError, match="Symlink"):
            cli.read_note(vault, name)


@pytest.mark.parametrize("text,expected", [("", {}), ("plain", {}), ("---\n---", {}), ("---\nx: 2\n...\na", {"x": 2})])
def test_frontmatter(text, expected):
    assert cli.frontmatter(text) == expected


@pytest.mark.parametrize("text", ["---\nx: 2", "---\n[1, 2]\n---", "---\nx: [\n---"])
def test_bad_frontmatter(text):
    with pytest.raises((ValueError, cli.yaml.YAMLError)):
        cli.frontmatter(text)


def test_writes(vault):
    with pytest.raises(ValueError, match="--write"):
        cli.write_note(vault, "new.md", "hello", "create")
    created = cli.write_note(vault, "new.md", "hello\r\n", "create", allow_write=True)
    with pytest.raises(ValueError, match="exists"):
        cli.write_note(vault, "new.md", "bad", "create", allow_write=True)
    with pytest.raises(ValueError, match="--if-sha256"):
        cli.write_note(vault, "new.md", "bad", "update", allow_write=True)
    with pytest.raises(ValueError, match="Conflict"):
        cli.write_note(vault, "new.md", "bad", "update", "old", True)
    path = vault / "new.md"
    path.chmod(0o640)
    appended = cli.write_note(vault, "new.md", "more\r\n", "append", created["sha256"], True)
    assert path.read_bytes() == b"hello\r\nmore\r\n"
    assert path.stat().st_mode & 0o777 == 0o640
    cli.write_note(vault, "new.md", "replacement", "update", appended["sha256"], True)
    assert path.read_text() == "replacement"
    assert not list(vault.glob(".obsidian-vault-*"))


def test_intervening_edit(vault, monkeypatch):
    path = vault / "empty.md"
    original = cli.note_path
    count = 0
    def concurrent(root, name):
        nonlocal count
        count += 1
        if count == 2:
            path.write_text("another editor")
        return original(root, name)
    monkeypatch.setattr(cli, "note_path", concurrent)
    with pytest.raises(ValueError, match="preparing"):
        cli.write_note(vault, "empty.md", "ours", "update", cli.digest(b""), True)
    assert path.read_text() == "another editor"
    assert not list(vault.glob(".obsidian-vault-*"))


def test_create_race_and_staging_error(vault, monkeypatch):
    def collision(source, target):
        Path(target).write_text("competitor")
        raise FileExistsError("exists")
    monkeypatch.setattr(os, "link", collision)
    with pytest.raises(FileExistsError):
        cli.write_note(vault, "new.md", "ours", "create", allow_write=True)
    assert (vault / "new.md").read_text() == "competitor"
    with pytest.raises(FileNotFoundError):
        cli.write_note(vault, "missing/new.md", "ours", "create", allow_write=True)
    assert not list(vault.glob(".obsidian-vault-*"))


def test_cli(vault, tmp_path, capsys, monkeypatch):
    config = tmp_path / "absent"
    assert cli.main(["--config", str(config), "vaults"]) == 0
    assert json.loads(capsys.readouterr().out) == {"vaults": []}
    prefix = ["--vault", str(vault)]
    for command in [["list"], ["search", "python"], ["read", "Course/python.md"]]:
        assert cli.main(prefix + command) == 0
        assert json.loads(capsys.readouterr().out)
    for command in [["search", ""], ["list", "--limit", "0"], ["read", "missing.md"], ["create", "new.md", "--content", "hi"]]:
        assert cli.main(prefix + command) == 1
        assert "error" in json.loads(capsys.readouterr().out)
    assert cli.main(prefix + ["create", "new.md", "--content", "hi", "--write"]) == 0
    capsys.readouterr()
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin"))
    assert cli.main(prefix + ["create", "stdin.md", "--stdin", "--write"]) == 0
    capsys.readouterr()
    assert (vault / "stdin.md").read_text() == "from stdin"
    with pytest.raises(SystemExit) as error:
        cli.main(prefix + ["list", "--limit", "-1"])
    assert error.value.code == 2
    monkeypatch.setattr(sys, "argv", ["obsidian-vault", "--config", str(config), "vaults"])
    with pytest.raises(SystemExit) as error:
        cli.run()
    assert error.value.code == 0


def test_subprocess(vault):
    result = subprocess.run([sys.executable, "-c", "from obsidian_vault.cli import run; run()", "--vault", str(vault), "list"], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["total"] == 2
    assert result.stderr == ""


def test_default_config(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert cli.discover() == []


def test_yaml_dates_and_config_error(vault, tmp_path, capsys):
    (vault / "dated.md").write_text('---\ndate: 2026-10-13\n---\nBody')
    assert cli.main(["--vault", str(vault), "read", "dated.md"]) == 0
    assert json.loads(capsys.readouterr().out)["frontmatter"]["date"] == "2026-10-13"
    config = tmp_path / "config.json"
    config.write_text("not json")
    assert cli.main(["--config", str(config), "vaults"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("data", [[], None, {"vaults": []}, {"vaults": {"id": None}}, {"vaults": {"id": {"path": 1}}}, {"vaults": {"id": {"path": ""}}}])
def test_config_shapes(tmp_path, data, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(data))
    assert cli.main(["--config", str(config), "vaults"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_scan_permission_error(vault, monkeypatch, capsys):
    def walk(root, *, followlinks, onerror):
        onerror(PermissionError("unreadable folder"))
        yield
    monkeypatch.setattr(os, "walk", walk)
    assert cli.main(["--vault", str(vault), "list"]) == 1
    assert "unreadable folder" in json.loads(capsys.readouterr().out)["error"]


def test_resolved_path_escape_is_rejected(vault, tmp_path, monkeypatch):
    # Model a directory replacement between the symlink check and final resolution.
    original_resolve = Path.resolve
    target = vault / 'empty.md'
    outside = tmp_path / 'outside.md'
    outside.write_text('private')

    def moved_directory(path, *args, **kwargs):
        return outside if path == target else original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', moved_directory)
    with pytest.raises(ValueError, match='outside vault'):
        cli.read_note(vault, 'empty.md')
    assert target.read_bytes() == b''
    assert outside.read_text() == 'private'
