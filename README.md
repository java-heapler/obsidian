# obsidian-mcp

A JSON CLI and agent skill for local Obsidian Markdown vaults. Discover registered vaults, list notes, search literal text, read Markdown and YAML frontmatter, and explicitly create, replace, or append notes. Runs without Obsidian or plugins.

This is filesystem integration, not the official Obsidian CLI: it does not operate the application, graph, plugins, workspace, sync, or rendered links. App discovery defaults to macOS configuration; explicit vault paths work on other systems. No network calls.

Repository: [java-heapler/obsidian-mcp](https://github.com/java-heapler/obsidian-mcp). The CLI and skill names remain unchanged; this package does not expose an MCP server.

## Install and use

```sh
uv tool install .
obsidian-vault vaults
obsidian-vault --vault /path/to/vault list --limit 50
obsidian-vault --vault /path/to/vault search 'dynamic programming'
obsidian-vault --vault /path/to/vault read 'Courses/Algorithms.md'
obsidian-vault --vault /path/to/vault create 'Courses/New.md' --content '# New course' --write
obsidian-vault --vault /path/to/vault append 'Courses/New.md' --content 'Next lesson' --if-sha256 HASH_FROM_READ --write
```

Use `--stdin` for exact multiline content:

```sh
printf '\nNext lesson\n' | obsidian-vault --vault /path/to/vault append 'Courses/New.md' --stdin --if-sha256 HASH_FROM_READ --write
```

Options `--vault` and `--config` precede the command. `update` replaces the complete file and requires both `--write` and the current `--if-sha256`. `append` adds the exact provided text with no automatic newline. Parent folders must already exist. `read` returns full content, SHA-256, and parsed frontmatter. YAML dates serialize as ISO strings. Search is case-insensitive literal, one result per matching line, with `--offset`, `--limit`, `total`, and `next_offset`. Listing has the same pagination. Exit status: 0 success, 1 JSON operational error, 2 argparse usage error.

Install `skills/obsidian` into your agent's skill directory separately. Do not copy personal notes into this repository.

## Safety and limits

Read commands leave notes unchanged. Hidden files/folders, symlink entries, and non-Markdown attachments are excluded. Note paths reject traversal and symlinks. Vault root symlinks are rejected. Creating uses atomic no-clobber publication. Updates preserve file permissions and use staged atomic replacement, checking content hashes before staging and again before replacement. This catches stale reads and ordinary intervening edits; it is **not a filesystem compare-and-swap** and cannot guarantee against another editor changing the file in the final check/replace interval. Avoid concurrent writes to the same note. Path validation likewise does not protect against an adversarial process replacing directories during an operation. Atomic replacement may not preserve extended attributes or ACLs.

UTF-8 notes only; existing bytes and line endings are preserved by append, while update uses exactly your replacement. Malformed frontmatter produces an error when reading. Search scans all eligible notes each time; it has no index, semantic retrieval, Wikilink resolution, or rendered Markdown search. Results are paginated but the scan is not memory-bounded. Reading cloud-backed files may download them. Vault discovery reads local app config, whose schema may change. No live note edits were used during development.

## Development

```sh
uv sync --extra dev
uv run pytest
uv build
```

Current baseline: 31 tests pass, 164/164 executable lines and 82/82 branches covered (100% each). CI enforces 100% coverage. Synthetic tests cover path boundaries, privacy exclusions, parsing, exact bytes, permission preservation, stale-write and no-clobber behavior, JSON CLI errors, and subprocess execution. CI runs these tests and builds on Python 3.11 and 3.13. Coverage measures exercised code, not proof of complete correctness.
