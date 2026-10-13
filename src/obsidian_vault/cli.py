"""JSON CLI operating on Markdown files, without app or plugin dependencies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

import yaml


def discover(config=None):
    path = Path(config).expanduser() if config else Path.home() / "Library/Application Support/obsidian/obsidian.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("vaults", {}), dict):
        raise ValueError("Obsidian config must contain a vaults object")
    for value in data.get("vaults", {}).values():
        if not isinstance(value, dict) or not isinstance(value.get("path"), str) or not value["path"]:
            raise ValueError("Each registered vault must contain a nonempty path string")
    return [{"id": key, "path": value["path"], "open": bool(value.get("open")),
             "exists": Path(value["path"]).is_dir()} for key, value in data.get("vaults", {}).items()]


def vault_path(path, config=None):
    if path is None:
        vaults = discover(config)
        if len(vaults) != 1:
            raise ValueError("Specify --vault; discovery did not find exactly one vault")
        path = vaults[0]["path"]
    root = Path(path).expanduser()
    if root.is_symlink():
        raise ValueError("Vault root must not be a symlink")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Vault must be a directory")
    return root


def note_path(root, name):
    relative = Path(name)
    if relative.is_absolute() or any(p in ("..",) or p.startswith(".") for p in relative.parts):
        raise ValueError("Use a relative Markdown path without hidden or parent components")
    if relative.suffix.lower() != ".md":
        raise ValueError("Notes must have a .md extension")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink paths are not supported")
    if not current.resolve().is_relative_to(root):
        raise ValueError("Note is outside vault")
    return current


def note_files(root):
    def fail(error):
        raise error
    for base, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(base) / d).is_symlink())
        for name in sorted(files):
            path = Path(base) / name
            if not name.startswith(".") and path.suffix.lower() == ".md" and not path.is_symlink():
                yield path


def digest(content):
    return hashlib.sha256(content).hexdigest()


def frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}
    for end in range(1, len(lines)):
        if lines[end] in ("---", "..."):
            value = yaml.safe_load("\n".join(lines[1:end]))
            if value is None:
                return {}
            if not isinstance(value, dict):
                raise ValueError("YAML frontmatter must be a mapping")
            return value
    raise ValueError("Unterminated YAML frontmatter")


def read_note(root, name):
    raw = note_path(root, name).read_bytes()
    text = raw.decode("utf-8")
    return {"path": name, "sha256": digest(raw), "content": text, "frontmatter": frontmatter(text)}


def scan(root, query=None, offset=0, limit=50):
    matches = []
    for path in note_files(root):
        relative = path.relative_to(root).as_posix()
        if query is None:
            matches.append({"path": relative})
        else:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": relative, "line": number, "text": line})
    return {"items": matches[offset:offset + limit], "total": len(matches), "offset": offset,
            "next_offset": offset + limit if offset + limit < len(matches) else None}


def write_note(root, name, content, operation, expected=None, allow_write=False):
    if not allow_write:
        raise ValueError("Writes require --write")
    path = note_path(root, name)
    previous = None
    if operation != "create":
        if not expected:
            raise ValueError("Updating or appending requires --if-sha256 from read")
        previous = path.read_bytes()
        if digest(previous) != expected:
            raise ValueError("Conflict: note changed since read")
    elif path.exists():
        raise ValueError("Note already exists")
    data = (previous if operation == "append" else b"") + content.encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".obsidian-vault-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck after staging; preserve existing mode and detect intervening changes.
        note_path(root, name)
        if previous is None:
            os.link(temporary, path)  # Atomic no-clobber creation.
        else:
            if path.read_bytes() != previous:
                raise ValueError("Conflict: note changed while preparing write")
            temporary.chmod(path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        return {"path": name, "operation": operation, "sha256": digest(data), "bytes": len(data)}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def nonnegative(value):
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("Must be nonnegative")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", help="Vault directory; required when discovery is ambiguous")
    parser.add_argument("--config", help="Override Obsidian app config for vault discovery")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("vaults")
    for command in ("list", "search"):
        sub = commands.add_parser(command)
        sub.add_argument("--offset", type=nonnegative, default=0)
        sub.add_argument("--limit", type=nonnegative, default=50)
        if command == "search":
            sub.add_argument("query")
    sub = commands.add_parser("read")
    sub.add_argument("note")
    for command in ("create", "update", "append"):
        sub = commands.add_parser(command)
        sub.add_argument("note")
        source = sub.add_mutually_exclusive_group(required=True)
        source.add_argument("--content")
        source.add_argument("--stdin", action="store_true", help="Read UTF-8 content from stdin")
        sub.add_argument("--write", action="store_true")
        sub.add_argument("--if-sha256")
    args = parser.parse_args(argv)
    try:
        if args.command == "vaults":
            result = {"vaults": discover(args.config)}
        else:
            root = vault_path(args.vault, args.config)
            if args.command in ("list", "search"):
                query = getattr(args, "query", None)
                if query == "":
                    raise ValueError("Search query must not be empty")
                if args.limit == 0:
                    raise ValueError("Limit must be greater than zero")
                result = scan(root, query, args.offset, args.limit)
            elif args.command == "read":
                result = read_note(root, args.note)
            else:
                result = write_note(root, args.note, sys.stdin.read() if args.stdin else args.content,
                                    args.command, args.if_sha256, args.write)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (OSError, ValueError, yaml.YAMLError, KeyError, TypeError) as error:
        print(json.dumps({"error": str(error)}))
        return 1


def run():
    sys.exit(main())
