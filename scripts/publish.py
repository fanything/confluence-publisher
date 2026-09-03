#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["Markdown>=3.7,<4", "markdownify>=0.14,<2"]
# ///

import argparse
from collections import deque
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

import markdown
from markdownify import markdownify


class ConfluenceError(RuntimeError):
    pass


def config_file() -> Path:
    explicit = os.environ.get("CONFLUENCE_ENV_FILE")
    if explicit:
        return Path(explicit).expanduser()
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "confluence-publisher" / ".env"


def load_config() -> tuple[str, str]:
    path = config_file()
    if path.is_file():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            key = key.strip()
            if separator and key in {"CONFLUENCE_BASE_URL", "CONFLUENCE_TOKEN"}:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                os.environ.setdefault(key, value)

    base_url = os.environ.get("CONFLUENCE_BASE_URL", "").rstrip("/")
    token = os.environ.get("CONFLUENCE_TOKEN", "")
    if urlsplit(base_url).scheme not in {"http", "https"} or not token:
        raise ConfluenceError(
            "Set CONFLUENCE_BASE_URL and CONFLUENCE_TOKEN in the environment "
            f"or {path}"
        )
    return base_url, token


def request_json(
    base_url: str,
    token: str,
    method: str,
    path: str,
    *,
    query: dict[str, str | int] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{base_url}/rest/api/{path.lstrip('/')}"
    if query:
        url += "?" + urlencode(query)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "confluence-publisher-skill/1",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"

    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise ConfluenceError(f"Confluence HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise ConfluenceError(f"Confluence connection failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ConfluenceError("Confluence returned invalid JSON") from exc


def render_content(content: str, input_format: str) -> str:
    if not content.strip():
        raise ConfluenceError("Input document is empty")
    if input_format == "storage":
        return content
    return markdown.markdown(
        content,
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="xhtml",
    )


def page_url(base_url: str, page: dict[str, Any]) -> str:
    webui = page.get("_links", {}).get("webui")
    if webui:
        return webui if webui.startswith("http") else base_url + "/" + webui.lstrip("/")
    return f"{base_url}/pages/viewpage.action?pageId={page['id']}"


def page_summary(base_url: str, page: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(page["id"]),
        "title": page["title"],
        "version": page.get("version", {}).get("number"),
        "url": page_url(base_url, page),
    }


def resolve_space(
    base_url: str,
    token: str,
    space_name: str | None,
    space_key: str | None,
) -> dict[str, Any]:
    if space_key:
        space = request_json(
            base_url,
            token,
            "GET",
            f"space/{quote(space_key, safe='')}",
            query={"expand": "homepage"},
        )
    else:
        spaces: list[dict[str, Any]] = []
        start = 0
        while True:
            response = request_json(
                base_url,
                token,
                "GET",
                "space",
                query={"start": start, "limit": 200, "expand": "homepage"},
            )
            batch = response.get("results", [])
            if not isinstance(batch, list):
                raise ConfluenceError("Confluence returned an invalid space list")
            spaces.extend(batch)
            if len(batch) < 200:
                break
            start += len(batch)

        matches = [item for item in spaces if item.get("name") == space_name]
        if not matches:
            matches = [item for item in spaces if item.get("key") == space_name]
        if not matches:
            raise ConfluenceError(f"Space not found: {space_name}")
        if len(matches) > 1:
            keys = ", ".join(str(item.get("key")) for item in matches)
            raise ConfluenceError(f"Space name is ambiguous: {space_name} ({keys})")
        space = matches[0]

    homepage = space.get("homepage")
    if not space.get("key") or not isinstance(homepage, dict) or not homepage.get("id"):
        raise ConfluenceError("Confluence did not return the space homepage")
    return space


def find_direct_child(
    base_url: str,
    token: str,
    space_key: str,
    parent_page_id: str,
    title: str,
) -> dict[str, Any]:
    candidates = request_json(
        base_url,
        token,
        "GET",
        "content",
        query={
            "spaceKey": space_key,
            "type": "page",
            "title": title,
            "expand": "ancestors,version",
            "limit": 100,
        },
    ).get("results", [])
    if not isinstance(candidates, list):
        raise ConfluenceError("Confluence returned an invalid page list")

    matches = []
    for candidate in candidates:
        ancestors = candidate.get("ancestors", [])
        direct_parent_id = str(ancestors[-1]["id"]) if ancestors else None
        if candidate.get("title") == title and direct_parent_id == str(parent_page_id):
            matches.append(candidate)
    if not matches:
        raise ConfluenceError(f"Page not found directly under parent {parent_page_id}: {title}")
    if len(matches) > 1:
        raise ConfluenceError(f"Multiple direct child pages matched: {title}")
    return matches[0]


def resolve_parent(
    base_url: str,
    token: str,
    space: dict[str, Any],
    parent_titles: list[str],
    parent_page_id: str | None,
) -> tuple[str, list[str]]:
    if parent_page_id:
        parent = request_json(
            base_url,
            token,
            "GET",
            f"content/{quote(parent_page_id, safe='')}",
            query={"expand": "space"},
        )
        if parent.get("type") != "page" or parent.get("space", {}).get("key") != space["key"]:
            raise ConfluenceError("Parent page does not belong to the selected space")
        return str(parent["id"]), [str(parent.get("title") or parent["id"])]

    current = space["homepage"]
    titles = list(parent_titles)
    if titles and titles[0] == current.get("title"):
        titles.pop(0)

    resolved: list[str] = []
    for title in titles:
        if not title.strip():
            raise ConfluenceError("Parent page title cannot be empty")
        try:
            current = find_direct_child(
                base_url, token, space["key"], str(current["id"]), title
            )
        except ConfluenceError as exc:
            raise ConfluenceError(
                f"Parent page not found directly under '{current.get('title')}': {title}"
            ) from exc
        resolved.append(title)

    return str(current["id"]), resolved


def list_children(
    base_url: str, token: str, parent_page_id: str
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    start = 0
    while True:
        response = request_json(
            base_url,
            token,
            "GET",
            f"content/{quote(parent_page_id, safe='')}/child/page",
            query={"start": start, "limit": 200, "expand": "version"},
        )
        batch = response.get("results", [])
        if not isinstance(batch, list):
            raise ConfluenceError("Confluence returned an invalid child page list")
        pages.extend(batch)
        if len(batch) < 200:
            break
        start += len(batch)
    return pages


def read_page(
    base_url: str, token: str, page_id: str, output_format: str
) -> dict[str, Any]:
    page = request_json(
        base_url,
        token,
        "GET",
        f"content/{quote(page_id, safe='')}",
        query={"expand": "body.storage,version,ancestors,space"},
    )
    storage = page.get("body", {}).get("storage", {}).get("value")
    if not isinstance(storage, str):
        raise ConfluenceError(f"Confluence did not return page content: {page_id}")
    body = storage if output_format == "storage" else markdownify(storage, heading_style="ATX").strip()
    return {
        **page_summary(base_url, page),
        "space_name": page.get("space", {}).get("name"),
        "path": [ancestor.get("title") for ancestor in page.get("ancestors", [])]
        + [page["title"]],
        "format": output_format,
        "body": body,
    }


def read_tree(
    base_url: str,
    token: str,
    parent_page_id: str,
    output_format: str,
    max_pages: int,
) -> list[dict[str, Any]]:
    if max_pages < 1:
        raise ConfluenceError("max-pages must be greater than zero")
    queue = deque(list_children(base_url, token, parent_page_id))
    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    while queue:
        page = queue.popleft()
        page_id = str(page["id"])
        if page_id in seen:
            continue
        if len(pages) >= max_pages:
            raise ConfluenceError(
                f"Directory exceeds max-pages={max_pages}; increase the limit explicitly"
            )
        seen.add(page_id)
        pages.append(read_page(base_url, token, page_id, output_format))
        queue.extend(list_children(base_url, token, page_id))
    return pages


def publish_page(
    base_url: str,
    token: str,
    space_key: str,
    title: str,
    storage_xhtml: str,
    parent_page_id: str | None = None,
) -> dict[str, Any]:
    matches = request_json(
        base_url,
        token,
        "GET",
        "content",
        query={
            "spaceKey": space_key,
            "type": "page",
            "title": title,
            "expand": "version,ancestors",
        },
    ).get("results", [])
    if len(matches) > 1:
        raise ConfluenceError("Multiple pages matched the same space and title")

    page: dict[str, Any] = {
        "type": "page",
        "title": title,
        "space": {"key": space_key},
        "body": {
            "storage": {
                "representation": "storage",
                "value": storage_xhtml,
            }
        },
    }

    if not matches:
        if parent_page_id:
            page["ancestors"] = [{"id": parent_page_id}]
        result = request_json(base_url, token, "POST", "content", payload=page)
        action = "created"
    else:
        current = matches[0]
        ancestors = current.get("ancestors", [])
        current_parent_id = str(ancestors[-1]["id"]) if ancestors else None
        if parent_page_id and current_parent_id != str(parent_page_id):
            raise ConfluenceError(
                f"Page '{title}' exists under a different parent; moving pages is not supported"
            )
        page["id"] = current["id"]
        page["version"] = {
            "number": current["version"]["number"] + 1,
            "message": "Published by Codex",
        }
        result = request_json(
            base_url, token, "PUT", f"content/{current['id']}", payload=page
        )
        action = "updated"

    verified = request_json(
        base_url,
        token,
        "GET",
        f"content/{result['id']}",
        query={"expand": "version,ancestors"},
    )
    return {
        "action": action,
        "page_id": verified["id"],
        "version": verified["version"]["number"],
        "url": page_url(base_url, verified),
    }


def add_destination_arguments(command: argparse.ArgumentParser) -> None:
    space = command.add_mutually_exclusive_group(required=True)
    space.add_argument("--space", help="Exact space name or key")
    space.add_argument("--space-key", help="Advanced: exact space key")
    parent = command.add_mutually_exclusive_group()
    parent.add_argument(
        "--parent",
        action="append",
        default=[],
        help="Exact parent title, repeated from top to bottom",
    )
    parent.add_argument("--parent-page-id", help="Advanced: exact parent page ID")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read and publish Confluence pages")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Verify credentials without writing")

    publish = commands.add_parser("publish", help="Create or update one page")
    add_destination_arguments(publish)
    publish.add_argument("--title", required=True)
    publish.add_argument("--input", required=True, help="UTF-8 file path or - for stdin")
    publish.add_argument("--format", choices=("markdown", "storage"), default="markdown")

    list_command = commands.add_parser("list", help="List direct child pages")
    add_destination_arguments(list_command)

    read = commands.add_parser("read", help="Read one direct child page")
    add_destination_arguments(read)
    read.add_argument("--title", required=True)
    read.add_argument("--format", choices=("markdown", "storage"), default="markdown")

    tree = commands.add_parser("read-tree", help="Recursively read descendant pages")
    add_destination_arguments(tree)
    tree.add_argument("--format", choices=("markdown", "storage"), default="markdown")
    tree.add_argument("--max-pages", type=int, default=50)
    tree.add_argument("--output", help="Optional JSON output file")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        base_url, token = load_config()
        if args.command == "check":
            user = request_json(base_url, token, "GET", "user/current")
            result = {
                "status": "ok",
                "base_url": base_url,
                "user": user.get("displayName") or user.get("username") or user.get("userKey"),
            }
        else:
            space = resolve_space(base_url, token, args.space, args.space_key)
            parent_page_id, parent_path = resolve_parent(
                base_url,
                token,
                space,
                args.parent,
                args.parent_page_id,
            )
            destination = {
                "space_name": space.get("name"),
                "space_key": space["key"],
                "parent_path": parent_path,
                "parent_page_id": parent_page_id,
            }
            if args.command == "publish":
                content = (
                    sys.stdin.read()
                    if args.input == "-"
                    else Path(args.input).read_text(encoding="utf-8")
                )
                result = publish_page(
                    base_url,
                    token,
                    space["key"],
                    args.title,
                    render_content(content, args.format),
                    parent_page_id,
                )
                result.update(destination)
            elif args.command == "list":
                pages = [
                    page_summary(base_url, page)
                    for page in list_children(base_url, token, parent_page_id)
                ]
                result = {**destination, "count": len(pages), "pages": pages}
            elif args.command == "read":
                page = find_direct_child(
                    base_url, token, space["key"], parent_page_id, args.title
                )
                result = {
                    **destination,
                    "page": read_page(base_url, token, str(page["id"]), args.format),
                }
            else:
                pages = read_tree(
                    base_url,
                    token,
                    parent_page_id,
                    args.format,
                    args.max_pages,
                )
                result = {**destination, "count": len(pages), "pages": pages}
                if args.output:
                    output = Path(args.output).expanduser()
                    output.write_text(
                        json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    result = {
                        "status": "ok",
                        "count": len(pages),
                        "output": str(output.resolve()),
                    }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ConfluenceError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
