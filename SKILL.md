---
name: confluence-publisher
description: List, read, recursively export, publish, or update documents in a human-readable Confluence space and page hierarchy. Use when the user asks to inspect a Confluence directory, read Wiki pages, publish documentation, update a page, or says "读取 Confluence", "发布到 Confluence", or "更新 Wiki 页面".
---

# Confluence Publisher

Use the bundled deterministic script for API calls and format conversion. Publish only when the user explicitly requests a Confluence write.

## Destination

1. Resolve this skill directory as `SKILL_DIR`; do not assume a username or fixed installation path.
2. Require a human-readable space name and title. Accept optional parent page titles ordered from top to bottom.
3. Resolve space and parent titles by exact match only. Stop on missing, ambiguous, or incorrect hierarchy matches.

Repeat `--parent` for each directory level; never collapse titles into a `/`-separated string. Omit it to target the space homepage. Use `--space-key` and `--parent-page-id` only when the user explicitly provides internal identifiers.

## Read

List direct children before reading a directory unless the user explicitly requests recursive reading:

```bash
uv run --script "$SKILL_DIR/scripts/publish.py" list \
  --space "Space name" \
  --parent "Top-level page" \
  --parent "Nested page"
```

Read one direct child page as Markdown:

```bash
uv run --script "$SKILL_DIR/scripts/publish.py" read \
  --space "Space name" \
  --parent "Top-level page" \
  --title "Page title"
```

For explicit recursive requests, set a bounded page count and write the result to a temporary JSON file. Read that file selectively, then remove it:

```bash
uv run --script "$SKILL_DIR/scripts/publish.py" read-tree \
  --space "Space name" \
  --parent "Top-level page" \
  --max-pages 50 \
  --output "/temporary/path/confluence-tree.json"
```

Use `--format storage` when exact Confluence Storage XHTML is required; otherwise keep the default Markdown conversion.

## Publish

Use the user's Markdown file as input. For inline content, write a temporary UTF-8 Markdown file and remove it after the command finishes. Run:

```bash
uv run --script "$SKILL_DIR/scripts/publish.py" publish \
  --space "Space name" \
  --parent "Top-level page" \
  --parent "Nested page" \
  --title "Page title" \
  --input "/absolute/path/document.md"
```

Report the returned `action`, `page_id`, `version`, and `url`. Surface script errors without hiding skipped or partial work. The script rejects updates when the existing page is under a different parent; it does not delete or move pages.

## Configuration

Read credentials from process environment variables first:

- `CONFLUENCE_BASE_URL`
- `CONFLUENCE_TOKEN`

Otherwise read `${CONFLUENCE_ENV_FILE}` or `${XDG_CONFIG_HOME:-$HOME/.config}/confluence-publisher/.env`. Never print, copy into output, or commit the token.

Check configuration without writing a page:

```bash
uv run --script "$SKILL_DIR/scripts/publish.py" check
```

For another machine, copy this skill directory, install `uv`, and create the external `.env` file. Do not copy credentials inside the skill.
