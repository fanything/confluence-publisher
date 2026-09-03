#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["Markdown>=3.7,<4", "markdownify>=0.14,<2"]
# ///

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import publish


class PublishPageTest(unittest.TestCase):
    @patch("publish.request_json")
    def test_creates_and_verifies_page(self, request):
        request.side_effect = [
            {"results": []},
            {"id": "34"},
            {
                "id": "34",
                "version": {"number": 1},
                "_links": {"webui": "/pages/34"},
            },
        ]

        result = publish.publish_page(
            "https://wiki.example", "token", "DEV", "Title", "<p>Body</p>", "12"
        )

        payload = request.call_args_list[1].kwargs["payload"]
        self.assertEqual(payload["ancestors"], [{"id": "12"}])
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["url"], "https://wiki.example/pages/34")

    @patch("publish.request_json")
    def test_updates_with_next_version(self, request):
        request.side_effect = [
            {
                "results": [
                    {
                        "id": "34",
                        "version": {"number": 3},
                        "ancestors": [{"id": "12"}],
                    }
                ]
            },
            {"id": "34"},
            {
                "id": "34",
                "version": {"number": 4},
                "_links": {"webui": "/pages/34"},
            },
        ]

        result = publish.publish_page(
            "https://wiki.example", "token", "DEV", "Title", "<p>New</p>", "12"
        )

        payload = request.call_args_list[1].kwargs["payload"]
        self.assertEqual(payload["version"]["number"], 4)
        self.assertEqual(result["action"], "updated")

    @patch("publish.request_json")
    def test_resolves_advanced_space_key(self, request):
        request.return_value = {
            "key": "DEV",
            "name": "Development",
            "homepage": {"id": "1", "title": "Development"},
        }

        space = publish.resolve_space(
            "https://wiki.example", "token", None, "DEV"
        )

        self.assertEqual(space["key"], "DEV")
        self.assertIn("space/DEV", request.call_args.args[3])

    @patch("publish.request_json", return_value={"results": [{}, {}]})
    def test_rejects_ambiguous_title(self, _request):
        with self.assertRaisesRegex(publish.ConfluenceError, "Multiple pages"):
            publish.publish_page(
                "https://wiki.example", "token", "DEV", "Title", "<p>x</p>"
            )

    @patch("publish.request_json")
    def test_resolves_space_name_and_parent_hierarchy(self, request):
        request.side_effect = [
            {
                "results": [
                    {
                        "key": "~example",
                        "name": "示例空间",
                        "homepage": {"id": "1", "title": "示例空间"},
                    }
                ]
            },
            {
                "results": [
                    {
                        "id": "2",
                        "title": "技术文档",
                        "ancestors": [{"id": "1"}],
                    }
                ]
            },
            {
                "results": [
                    {
                        "id": "3",
                        "title": "Agent",
                        "ancestors": [{"id": "1"}, {"id": "2"}],
                    }
                ]
            },
        ]

        space = publish.resolve_space(
            "https://wiki.example", "token", "示例空间", None
        )
        parent_id, path = publish.resolve_parent(
            "https://wiki.example", "token", space, ["技术文档", "Agent"], None
        )

        self.assertEqual(space["key"], "~example")
        self.assertEqual(parent_id, "3")
        self.assertEqual(path, ["技术文档", "Agent"])

    @patch("publish.request_json")
    def test_rejects_duplicate_space_names(self, request):
        request.return_value = {
            "results": [
                {"key": "ONE", "name": "Docs", "homepage": {"id": "1"}},
                {"key": "TWO", "name": "Docs", "homepage": {"id": "2"}},
            ]
        }

        with self.assertRaisesRegex(publish.ConfluenceError, "ambiguous"):
            publish.resolve_space("https://wiki.example", "token", "Docs", None)

    @patch("publish.request_json")
    def test_rejects_parent_from_another_branch(self, request):
        request.return_value = {
            "results": [
                {
                    "id": "3",
                    "title": "Agent",
                    "ancestors": [{"id": "1"}, {"id": "99"}],
                }
            ]
        }
        space = {
            "key": "DEV",
            "name": "Docs",
            "homepage": {"id": "1", "title": "Docs"},
        }

        with self.assertRaisesRegex(publish.ConfluenceError, "directly under"):
            publish.resolve_parent(
                "https://wiki.example", "token", space, ["Agent"], None
            )

    @patch("publish.request_json")
    def test_rejects_update_under_different_parent(self, request):
        request.return_value = {
            "results": [
                {
                    "id": "34",
                    "version": {"number": 3},
                    "ancestors": [{"id": "99"}],
                }
            ]
        }

        with self.assertRaisesRegex(publish.ConfluenceError, "different parent"):
            publish.publish_page(
                "https://wiki.example",
                "token",
                "DEV",
                "Title",
                "<p>x</p>",
                "12",
            )

    @patch("publish.request_json")
    def test_lists_direct_children(self, request):
        request.return_value = {
            "results": [
                {
                    "id": "34",
                    "title": "Child",
                    "version": {"number": 2},
                    "_links": {"webui": "/pages/34"},
                }
            ]
        }

        pages = publish.list_children("https://wiki.example", "token", "12")

        self.assertEqual(len(pages), 1)
        self.assertEqual(publish.page_summary("https://wiki.example", pages[0])["version"], 2)

    @patch("publish.request_json")
    def test_reads_page_as_markdown(self, request):
        request.return_value = {
            "id": "34",
            "title": "Child",
            "version": {"number": 2},
            "space": {"name": "Docs"},
            "ancestors": [{"title": "Home"}],
            "body": {"storage": {"value": "<h1>Title</h1><p>Hello</p>"}},
            "_links": {"webui": "/pages/34"},
        }

        page = publish.read_page("https://wiki.example", "token", "34", "markdown")

        self.assertIn("# Title", page["body"])
        self.assertEqual(page["path"], ["Home", "Child"])

    def test_reads_tree_recursively(self):
        with patch("publish.list_children") as children, patch(
            "publish.read_page"
        ) as read:
            children.side_effect = [
                [{"id": "2", "title": "One"}],
                [{"id": "3", "title": "Two"}],
                [],
            ]
            read.side_effect = [{"id": "2"}, {"id": "3"}]

            pages = publish.read_tree(
                "https://wiki.example", "token", "1", "markdown", 10
            )

        self.assertEqual([page["id"] for page in pages], ["2", "3"])

    def test_rejects_tree_over_limit(self):
        with patch("publish.list_children") as children, patch(
            "publish.read_page", return_value={"id": "2"}
        ):
            children.side_effect = [
                [{"id": "2", "title": "One"}, {"id": "3", "title": "Two"}],
                [],
            ]
            with self.assertRaisesRegex(publish.ConfluenceError, "max-pages=1"):
                publish.read_tree(
                    "https://wiki.example", "token", "1", "markdown", 1
                )

    def test_converts_markdown_to_xhtml(self):
        result = publish.render_content("# Title\n\n| A | B |\n| - | - |\n| 1 | 2 |", "markdown")

        self.assertIn("<h1>Title</h1>", result)
        self.assertIn("<table>", result)

    def test_loads_external_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "CONFLUENCE_BASE_URL=https://wiki.example\nCONFLUENCE_TOKEN=test-token\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ, {"CONFLUENCE_ENV_FILE": str(path)}, clear=True
            ):
                self.assertEqual(
                    publish.load_config(), ("https://wiki.example", "test-token")
                )


if __name__ == "__main__":
    unittest.main()
