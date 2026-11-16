"""Checks for selection mistakes and accidental changes outside the OSS block."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "build_oss.py"
spec = importlib.util.spec_from_file_location("build_oss", path)
oss = importlib.util.module_from_spec(spec); spec.loader.exec_module(oss)


def pr(repo="upstream/sdk", state="MERGED", **fields):
    row = {"repository": {"nameWithOwner": repo, "isPrivate": False, "licenseInfo": {"spdxId": "MIT"}},
           "author": {"login": "Muneer320"}, "state": state, "mergedAt": "2026-11-01" if state == "MERGED" else None,
           "url": f"https://github.com/{repo}/pull/1", "number": 1, "title": "Fix SDK", "isDraft": False}
    row.update(fields); return row


class OssTests(unittest.TestCase):
    def setUp(self):
        self.config = {"user": "Muneer320", "exclude_owners": ["Muneer320"],
                       "exclude_repositories": ["team/project"],
                       "projects": [{"repository": "upstream/sdk", "license": "MIT", "label": "SDK", "summary": "SDK fixes."},
                                    {"repository": "team/project", "license": "MIT", "label": "Team", "summary": "Excluded even if selected."}]}

    def test_scope_and_status(self):
        rows = [pr(), pr("team/project"), pr("Muneer320/tool"), pr("unknown/tool"),
                pr(state="CLOSED"), pr(author={"login": "someone-else"}),
                pr(repository={"nameWithOwner": "upstream/sdk", "isPrivate": True})]
        selected, unreviewed = oss.select_prs(rows, self.config)
        self.assertEqual([r["url"] for r in selected], [rows[0]["url"]])
        self.assertEqual(unreviewed, ["unknown/tool"])
        self.config["projects"][0]["summary"] = ""
        self.assertIn("Fix SDK", oss.build_block(selected, self.config, "16 Nov 2026"))
        selected, _ = oss.select_prs([pr(repository={"nameWithOwner": "upstream/sdk", "isPrivate": False, "licenseInfo": None})], self.config)
        self.assertEqual(len(selected), 1)  # Reviewed projects do not depend on license auto-detection.

    def test_readme_boundaries_and_draft_escaping(self):
        row = pr(state="OPEN", isDraft=True, title="fix | [link](bad) <img>")
        block = oss.build_block([row], self.config, "16 Nov 2026")
        original = "before\n" + oss.START + "old\n" + oss.END + "\nHolopin\n<!--STATS:START-->untouched"
        updated = oss.replace_block(original, block)
        self.assertTrue(updated.startswith("before\n")); self.assertTrue(updated.endswith("\nHolopin\n<!--STATS:START-->untouched"))
        self.assertIn("(draft)", block); self.assertIn("&#124;", block); self.assertNotIn("<img>", block)
        with self.assertRaises(ValueError): oss.replace_block(original + oss.END, block)

    def test_api_failure_does_not_rewrite_readme(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / ".github").mkdir()
            import json
            (root / ".github/oss-projects.json").write_text(json.dumps(self.config))
            readme = root / "README.md"; content = "before" + oss.START + "last good" + oss.END + "after"; readme.write_text(content)
            with patch.object(oss, "ROOT", root), patch.object(oss, "fetch_prs", side_effect=RuntimeError("API unavailable")):
                with self.assertRaises(RuntimeError): oss.main()
            self.assertEqual(readme.read_text(), content)


if __name__ == "__main__": unittest.main()
