"""scripts/forge.py's pull request commands, game-free and offline: the HTTP layer (forge.api) is a stand-in Forgejo
that keeps the pull requests, labels and comments in memory, and a real request fails the test. pr create opens as work
in progress (WIP: title) with the template as the description when none is given (main's and this PR's commits filled
in), and --ready only when the description passes check_pr_description.py; pr ready runs that check with the PR's diff,
refuses an incomplete description (--force goes on with a warning), prints the check's warnings, and takes WIP: and the
'needs measurements' label off; pr wip puts WIP: back (and the label, when asked); pr
edit keeps the WIP state; pr update edits the description and comments in one step, or only comments; pr comment takes
--body-file; pr status shows the state, the check, the checklist, the reviews and the last comments.

    python -I -B tests/test_forge_pr.py
"""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


forge = load("forge", ROOT / "scripts" / "forge.py")
samples = load("test_check_pr_description", ROOT / "tests" / "test_check_pr_description.py")
TEMPLATE = samples.TEMPLATE
FILLED = samples.GOOD
DIFF = samples.DIFF


class Forgejo:
    """The API calls forge.py's pr commands make, against pull requests kept in memory"""

    def __init__(self):
        self.prs, self.comments, self.calls = {}, {}, []
        self.labels = [{"id": 7, "name": "testing wanted"}, {"id": 9, "name": "needs measurements"}]
        self.reviews, self.diffs = {}, {}
        self.wip_prefixes = ("WIP:", "[WIP]")

    def add(self, number, title, body, labels=()):
        self.prs[number] = {"number": number, "title": title, "body": body, "state": "open", "merged": False,
                            "mergeable": True, "user": {"login": "alice"}, "head": {"label": "alice:fix"},
                            "base": {"ref": "main"}, "labels": [l for l in self.labels if l["name"] in labels],
                            "html_url": "https://git.notrexed.dev/Hookshot/Hookshot/pulls/%d" % number}
        self.prs[number]["draft"] = self.draft(title)
        self.comments[number] = []
        return self.prs[number]

    def draft(self, title):
        return any(title.upper().startswith(p) for p in self.wip_prefixes)

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        base = "/repos/MagicBeanDev/Hookshot"
        if method == "GET" and path.startswith("/repos/alice/Hookshot/branches/"):
            return {"name": path.rsplit("/", 1)[1]}
        assert path.startswith(base), path
        path = path[len(base):]
        parts = path.split("?")[0].strip("/").split("/")
        if method == "POST" and parts == ["pulls"]:
            pr = self.add(len(self.prs) + 1, body["title"], body["body"])
            pr["head"]["sha"], pr["merge_base"] = "992a2420" + "1" * 32, "7afe6ae1" + "2" * 32
            return dict(pr)
        if parts[:2] == ["git", "commits"]:
            return {"commit": {"committer": {"date": "2026-11-10T12:00:00Z"}}}
        if parts[0] == "labels":
            return self.labels
        number = int(parts[1])
        pr = self.prs[number]
        if parts[0] == "pulls" and len(parts) == 2:
            if method == "PATCH":
                pr.update(body)
                pr["draft"] = self.draft(pr["title"])
            return dict(pr)
        if parts[2:] == ["reviews"]:
            return self.reviews.get(number, [])
        if parts[2:] == ["comments"]:
            if method == "POST":
                self.comments[number].append({"body": body["body"], "user": {"login": "alice"},
                                              "created_at": "2026-11-12T18:00:00Z"})
                return {"html_url": pr["html_url"] + "#issuecomment-%d" % len(self.comments[number])}
            return self.comments[number]
        if parts[2] == "labels":
            if method == "POST":
                pr["labels"] += [l for l in self.labels if l["id"] in body["labels"]]
            elif method == "DELETE":
                pr["labels"] = [l for l in pr["labels"] if l["id"] != int(parts[3])]
            return None
        raise AssertionError("unexpected call %s %s" % (method, path))

    def text(self, path):
        self.calls.append(("GET", path, None))
        number = int(path.rsplit("/", 1)[1].split(".")[0])
        return self.diffs.get(number, "")

    def writes(self):
        return [(m, p, b) for m, p, b in self.calls if m != "GET"]


def no_network(*args, **kwargs):
    raise AssertionError("a real request in a test: %r" % (args,))


class PR(unittest.TestCase):
    def setUp(self):
        self.forgejo = Forgejo()
        self.patches = [mock.patch.object(forge, "api", self.forgejo),
                        mock.patch.object(forge, "api_text", self.forgejo.text),
                        mock.patch.object(forge, "token", lambda: {"login": "alice", "access_token": "t"}),
                        mock.patch.object(forge, "ensure_fork", lambda login: ("alice/Hookshot", False)),
                        mock.patch.object(forge.auth, "forge_request", no_network),
                        mock.patch.object(forge.auth, "forge_session", no_network)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tempfile.mkdtemp()

    def run_forge(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = forge.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def file(self, text, name="body.md"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    # --- create ---

    def test_create_opens_as_wip_with_the_template(self):
        code, out, _ = self.run_forge("pr", "create", "fix", "--title", "fix(menu): text cut off at 4K")
        self.assertEqual(code, 0)
        pr = self.forgejo.prs[1]
        self.assertEqual(pr["title"], "WIP: fix(menu): text cut off at 4K")
        self.assertTrue(pr["draft"])
        # (no --body-file: the description starts from the template, with the commits filled in)
        self.assertEqual(pr["body"], TEMPLATE.replace("main: <commit> (<date>)", "main: 7afe6ae1 (2026-11-10)")
                         .replace("this PR: <branch> @ <commit>", "this PR: fix @ 992a2420")
                         .replace("| | main @ <commit> | this PR @ <commit> |", "| | main @ 7afe6ae1 | this PR @ 992a2420 |"))
        self.assertIn("<what was measured>", pr["body"])
        self.assertIn("pr ready 1", out)

    def test_create_draft_is_the_default_and_wip_is_not_doubled(self):
        self.assertEqual(self.run_forge("pr", "create", "fix", "--title", "fix: x", "--draft", "--body", "b")[0], 0)
        self.assertEqual(self.run_forge("pr", "create", "fix", "--title", "WIP: fix: y", "--wip")[0], 0)
        self.assertEqual(self.forgejo.prs[1]["title"], "WIP: fix: x")
        self.assertEqual(self.forgejo.prs[1]["body"], "b")
        self.assertEqual([m for m, _, _ in self.forgejo.writes()], ["POST", "POST", "PATCH"], "only the template is filled")
        self.assertEqual(self.forgejo.prs[2]["title"], "WIP: fix: y")

    def test_create_ready_needs_a_complete_description(self):
        code, _, err = self.run_forge("pr", "create", "fix", "--title", "fix: x", "--ready")
        self.assertEqual(code, 1)
        self.assertIn("not ready for review", err)
        self.assertIn("## Why still holds the template's placeholders", err)
        self.assertEqual(self.forgejo.writes(), [], "nothing is opened")
        code, _, _ = self.run_forge("pr", "create", "fix", "--title", "fix: x", "--ready", "--body-file", self.file(FILLED))
        self.assertEqual(code, 0)
        self.assertEqual(self.forgejo.prs[1]["title"], "fix: x")
        code, _, err = self.run_forge("pr", "create", "fix", "--title", "fix: y", "--ready", "--force")
        self.assertEqual((code, self.forgejo.prs[2]["title"]), (0, "fix: y"))
        self.assertIn("warning", err)

    def test_create_refuses(self):
        self.assertIn("Conventional Commit", self.run_forge("pr", "create", "fix", "--title", "Fix things")[2])
        self.assertIn("--force goes with --ready", self.run_forge("pr", "create", "fix", "--title", "fix: x", "--force")[2])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            forge.main(["pr", "create", "fix", "--title", "fix: x", "--ready", "--wip"])
        self.assertEqual(self.forgejo.writes(), [])

    # --- ready and wip ---

    def test_ready_refuses_an_incomplete_description(self):
        self.forgejo.add(5, "WIP: fix: x", TEMPLATE)
        code, _, err = self.run_forge("pr", "ready", "5")
        self.assertEqual(code, 1)
        for want in ("## Why still holds the template's placeholders", "not ticked: One topic.", "pr edit 5 --body-file", "--force"):
            self.assertIn(want, err)
        self.assertEqual(self.forgejo.prs[5]["title"], "WIP: fix: x")
        self.assertEqual(self.forgejo.writes(), [])
        body = FILLED.replace("- [x] Rebased on current `main`.", "- [ ] Rebased on current `main`.")
        self.forgejo.prs[5]["body"] = body
        self.assertIn("not ticked: Rebased on current `main`.", self.run_forge("pr", "ready", "5")[2])

    def test_ready_checks_the_diff_and_warns(self):
        self.forgejo.add(5, "WIP: fix: x", FILLED)["merge_base"] = "0123456789abcdef"
        self.forgejo.diffs[5] = DIFF
        code, _, err = self.run_forge("pr", "ready", "5")
        self.assertEqual(code, 1)
        self.assertIn("the diff adds RX_VK_NEW_THING, which ## Flags and settings does not list", err)
        self.assertIn("warning: measured on main 7afe6ae1, but the PR is now on main 01234567", err)
        self.assertIn(("GET", "/repos/MagicBeanDev/Hookshot/pulls/5.diff", None), self.forgejo.calls)
        self.forgejo.diffs[5] = ""
        code, out, err = self.run_forge("pr", "ready", "5")
        self.assertEqual(code, 0, err)
        self.assertIn("warning: measured on main", err, "a warning does not stop it")

    def test_ready_force(self):
        self.forgejo.add(5, "WIP: fix: x", TEMPLATE)
        code, out, err = self.run_forge("pr", "ready", "5", "--force")
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertEqual(self.forgejo.prs[5]["title"], "fix: x")

    def test_ready_takes_wip_and_the_label_off(self):
        self.forgejo.add(5, "[WIP] WIP: fix: x", FILLED, labels=["needs measurements", "testing wanted"])
        code, out, _ = self.run_forge("pr", "ready", "5")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.forgejo.prs[5]["title"], "fix: x")
        self.assertFalse(self.forgejo.prs[5]["draft"])
        self.assertEqual([l["name"] for l in self.forgejo.prs[5]["labels"]], ["testing wanted"])
        self.assertIn("ready for review", out)
        code, out, _ = self.run_forge("pr", "ready", "5")
        self.assertIn("already", out)

    def test_ready_when_the_server_reads_another_prefix(self):
        self.forgejo.wip_prefixes += ("DRAFT:",)
        self.forgejo.add(5, "WIP: DRAFT: fix: x", FILLED)
        code, _, err = self.run_forge("pr", "ready", "5")
        self.assertEqual(code, 1)
        self.assertIn("WORK_IN_PROGRESS_PREFIXES", err)

    def test_wip(self):
        self.forgejo.add(5, "fix: x", FILLED)
        code, out, _ = self.run_forge("pr", "wip", "5")
        self.assertEqual((code, self.forgejo.prs[5]["title"]), (0, "WIP: fix: x"))
        self.assertEqual(self.forgejo.prs[5]["labels"], [])
        writes = len(self.forgejo.writes())
        code, out, _ = self.run_forge("pr", "wip", "5", "--needs-measurements")
        self.assertIn("already", out)
        self.assertEqual(self.forgejo.prs[5]["title"], "WIP: fix: x")
        self.assertEqual([l["name"] for l in self.forgejo.prs[5]["labels"]], ["needs measurements"])
        self.assertEqual(len(self.forgejo.writes()), writes + 1, "only the label")

    def test_closed_or_merged(self):
        self.forgejo.add(5, "WIP: fix: x", FILLED)["merged"] = True
        self.assertIn("merged", self.run_forge("pr", "ready", "5")[2])
        self.assertEqual(self.forgejo.writes(), [])

    # --- edit, update, comment ---

    def test_edit_keeps_the_wip_state(self):
        self.forgejo.add(5, "WIP: fix: x", TEMPLATE)
        self.forgejo.add(6, "fix: y", FILLED)
        self.assertEqual(self.run_forge("pr", "edit", "5", "--title", "fix(vk): x")[0], 0)
        self.assertEqual(self.forgejo.prs[5]["title"], "WIP: fix(vk): x")
        self.assertEqual(self.run_forge("pr", "edit", "6", "--title", "WIP: fix(vk): y")[0], 0)
        self.assertEqual(self.forgejo.prs[6]["title"], "fix(vk): y", "pr wip, not pr edit, makes it WIP")
        self.assertIn("Conventional Commit", self.run_forge("pr", "edit", "6", "--title", "no")[2])
        self.assertIn("nothing to change", self.run_forge("pr", "edit", "6")[2])

    def test_edit_the_description(self):
        self.forgejo.add(5, "WIP: fix: x", TEMPLATE)
        self.forgejo.add(6, "fix: y", FILLED)
        code, out, err = self.run_forge("pr", "edit", "5", "--body-file", self.file(FILLED))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.forgejo.prs[5]["body"], FILLED)
        code, out, err = self.run_forge("pr", "edit", "6", "--body", "short")
        self.assertEqual(code, 0)
        self.assertEqual(self.forgejo.prs[6]["body"], "short")
        self.assertIn("no longer passes", err, "a PR marked ready whose description stops passing")
        self.assertIn("pr wip 6", err)

    def test_update_edits_and_comments(self):
        self.forgejo.add(5, "WIP: fix: x", TEMPLATE)
        code, out, _ = self.run_forge("pr", "update", "5", "--body-file", self.file(FILLED),
                                      "--comment", "Measured on an RX 6700 XT: the table is in the description.")
        self.assertEqual(code, 0)
        self.assertEqual(self.forgejo.prs[5]["body"], FILLED)
        self.assertEqual([c["body"] for c in self.forgejo.comments[5]],
                         ["Measured on an RX 6700 XT: the table is in the description."])
        self.assertEqual([m for m, _, _ in self.forgejo.writes()], ["PATCH", "POST"])
        self.assertIn("#issuecomment-1", out)

    def test_update_comment_only(self):
        self.forgejo.add(5, "WIP: fix: x", TEMPLATE)
        code, _, _ = self.run_forge("pr", "update", "5", "--comment-file", self.file("Rebased on main.\n", "c.md"))
        self.assertEqual(code, 0)
        self.assertEqual(self.forgejo.prs[5]["body"], TEMPLATE)
        self.assertEqual([m for m, _, _ in self.forgejo.writes()], ["POST"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            forge.main(["pr", "update", "5", "--body", "b"])   # (a comment is required)
        self.assertIn("say what changed", self.run_forge("pr", "update", "5", "--comment", "  ")[2])
        self.assertEqual(len(self.forgejo.comments[5]), 1)

    def test_comment_from_a_file(self):
        self.forgejo.add(5, "fix: x", FILLED)
        self.assertEqual(self.run_forge("pr", "comment", "5", "--body-file", self.file("Tested: fixed.\n"))[0], 0)
        self.assertEqual(self.run_forge("pr", "comment", "5", "Second.")[0], 0)
        self.assertEqual([c["body"] for c in self.forgejo.comments[5]], ["Tested: fixed.\n", "Second."])
        self.assertIn("empty", self.run_forge("pr", "comment", "5")[2])

    # --- status ---

    def test_status(self):
        self.forgejo.add(5, "WIP: fix: x", TEMPLATE, labels=["needs measurements"])
        self.forgejo.reviews[5] = [{"user": {"login": "bob"}, "state": "REQUEST_CHANGES"},
                                   {"user": {"login": "bob"}, "state": "APPROVED"},
                                   {"user": {"login": "carol"}, "state": "PENDING"}]
        for i in range(5):
            self.forgejo.comments[5].append({"body": "comment %d" % i, "user": {"login": "bob"},
                                             "created_at": "2026-11-12T18:0%d:00Z" % i})
        code, out, _ = self.run_forge("pr", "status", "5")
        self.assertEqual(code, 0)
        for want in ("work in progress", "to fix before pr ready", "For everyone: 0 of 9 ticked", "Mergeable: yes",
                     "Labels: needs measurements", "Reviews: bob approved", "Last comments (5 in all)", "comment 4"):
            self.assertIn(want, out)
        self.assertNotIn("carol", out)
        self.assertNotIn("comment 1", out)
        self.forgejo.add(6, "fix: y", FILLED)
        out = self.run_forge("pr", "status", "6")[1]
        for want in ("ready for review", "Description: complete", "For everyone: 9 of 9 ticked", "Reviews: none"):
            self.assertIn(want, out)

    def test_view_shows_wip(self):
        self.forgejo.add(5, "[wip] fix: x", FILLED)
        self.assertIn("work in progress, by alice", self.run_forge("pr", "view", "5")[1])


if __name__ == "__main__":
    unittest.main()
