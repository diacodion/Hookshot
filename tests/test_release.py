"""scripts/release.py (docs/releases.md, RELEASING.md; the same file in MagicBeanDev/LongshotLauncher), with no network: keys,
SHA256SUMS and its signature; the channels as the launcher reads them from the release list; the version a tag must reach
(the bump from the commits since the last stable), in a git repository made here; the nightly's name; and publish and
prune against a fake Forgejo on 127.0.0.1 (a draft first, every file uploaded and checked, published last; only nightlies
pruned). Needs git and the openssl command line.

    python -I tests/test_release.py
"""
import base64
import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = release.main([str(a) for a in args])
    return code, out.getvalue()


class Keys(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="release-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        run("keygen", self.dir / "keys")
        self.key, self.pub = self.dir / "keys" / "release.pem", self.dir / "keys" / "release.pub.pem"

    def test_keygen_never_overwrites_and_keeps_the_key_private(self):
        with self.assertRaises(SystemExit):
            run("keygen", self.dir / "keys")
        if os.name != "nt":
            self.assertEqual(self.key.stat().st_mode & 0o077, 0)

    def test_sums_sign_verify(self):
        files = self.dir / "files"
        files.mkdir()
        (files / "LongshotLauncher-v1.0.0-x86_64.AppImage").write_bytes(b"launcher")
        (files / "Hookshot-v3.0.0-x86_64.AppImage").write_bytes(b"builder")
        run("sums", *files.iterdir(), "--out", files / "SHA256SUMS")
        sums = (files / "SHA256SUMS").read_text()
        self.assertEqual(sums.splitlines()[0], "%s  Hookshot-v3.0.0-x86_64.AppImage" % hashlib.sha256(b"builder").hexdigest())
        run("sign", files / "SHA256SUMS", "--key", self.key, "--out", files / "SHA256SUMS.sig")
        code, out = run("verify", files / "SHA256SUMS", "--pub", self.pub, "--files", files)
        self.assertEqual(code, 0)
        self.assertIn("every file", out)
        # (a file changed, the list changed, another key)
        (files / "Hookshot-v3.0.0-x86_64.AppImage").write_bytes(b"other")
        with self.assertRaises(SystemExit):
            run("verify", files / "SHA256SUMS", "--pub", self.pub, "--files", files)
        (files / "SHA256SUMS").write_text(sums.replace("Hookshot-v3", "Hookshot-v4"))
        with self.assertRaises(SystemExit):
            run("verify", files / "SHA256SUMS", "--pub", self.pub)
        (files / "SHA256SUMS").write_text(sums)
        run("keygen", self.dir / "other")
        with self.assertRaises(SystemExit):
            run("verify", files / "SHA256SUMS", "--pub", self.dir / "other" / "release.pub.pem")

    def test_the_launcher_test_fixture_is_this_format(self):
        # (the launcher's tests/fixtures/release are signed the same way: a base64 DER signature of SHA256SUMS)
        der = base64.b64decode(release.sign(str(self.pub), str(self.key)))
        self.assertTrue(release.verify_sig(self.pub.read_bytes(), der, [release.spki_der(str(self.pub))]))

    def test_the_trusted_keys(self):
        trusted = release.trusted_keys()
        test_key = ROOT / "tests" / "fixtures" / "release" / "test-key.pub.pem"
        if test_key.is_file():
            self.assertNotIn(release.spki_der(str(test_key)), trusted, "the public test key is trusted")
        cs = self.dir / "Core" / "ReleaseTrust.cs"
        cs.parent.mkdir()
        cs.write_text('x\n    public static readonly IReadOnlyList<ReleaseKey> BuiltIn = new ReleaseKey[]\n    {\n'
                      '        ReleaseKey.FromSpki("QUJD"),\n    };\n')
        self.assertEqual(release.trusted_keys(str(self.dir)), [b"ABC"])
        self.assertEqual(release.product_of(str(self.dir)), "launcher")
        # (comments with ; { } before, inside and after the initializer change nothing; a commented-out key is no key)
        cs.write_text('/* BuiltIn = { ReleaseKey.FromSpki("WFla") }; */\n'
                      '    // the keys; one per line { as keygen prints them }\n'
                      '    public static readonly IReadOnlyList<ReleaseKey> BuiltIn = new ReleaseKey[]\n    {\n'
                      '        // id 5e18330371d7f5aa, keygen 2026-11-13; RELEASING.md { "One-time setup" }\n'
                      '        ReleaseKey.FromSpki("QUJD"),   /* the first; } */\n'
                      '        // ReleaseKey.FromSpki("WFla"),\n'
                      '        ReleaseKey.FromSpki( "REVG" ),\n    };\n'
                      '    static readonly string Other = "ReleaseKey.FromSpki(\\"R0hJ\\")";\n')
        self.assertEqual(release.trusted_keys(str(self.dir)), [b"ABC", b"DEF"])
        cs.write_text('class ReleaseKeys {}\n')
        self.assertEqual(release.trusted_keys(str(self.dir)), [])
        (self.dir / "scripts").mkdir()
        (self.dir / "scripts" / "release_keys.json").write_text('{"keys": ["REVG"]}')
        cs.unlink()
        self.assertEqual(release.trusted_keys(str(self.dir)), [b"DEF"])
        self.assertEqual(release.product_of(str(self.dir)), "hookshot")


class Channels(unittest.TestCase):
    def test_the_version_order(self):
        order = ["v3.0.0-rc.1", "v3.0.0-rc.2", "v3.0.0-rc.10", "v3.0.0", "v3.0.1-rc.1", "v3.0.1", "v3.1.0", "v10.0.0"]
        self.assertEqual(sorted(order, key=release.version_key), order)
        for tag, channel in (("v3.0.0", "stable"), ("v3.1.0-rc.2", "rc"), ("nightly-412-1a2b3c4d", "nightly"), ("v2.9.0", None),
                             ("v3.0.0-3-g1a2b3c4", None), ("dev", None), ("tools", None)):
            self.assertEqual(release.channel_of(tag, 3), channel, tag)
        self.assertEqual(release.channel_of("v1.0.0", 1), "stable")

    def test_what_each_channel_offers(self):
        rels = [{"tag_name": t, "draft": d, "prerelease": p} for t, d, p in (
            ("v3.0.0", False, False), ("v3.1.0-rc.1", False, True), ("v3.1.0-rc.2", True, True), ("v3.2.0", True, False),
            ("nightly-9-aaaaaaaa", False, True), ("nightly-12-bbbbbbbb", False, True), ("nightly-100-cccccccc", True, True),
            ("tools", False, True), ("v2.9.0", False, False))]
        self.assertEqual(release.newest(rels, "stable", 3), "v3.0.0")        # (drafts are not there yet)
        self.assertEqual(release.newest(rels, "rc", 3), "v3.1.0-rc.1")
        self.assertEqual(release.newest(rels, "nightly", 3), "nightly-12-bbbbbbbb")   # (by count, not by text)
        rels.append({"tag_name": "v3.1.0", "draft": False, "prerelease": False})
        self.assertEqual(release.newest(rels, "rc", 3), "v3.1.0")          # (rc offers the stable once it is out)
        # (pulling a bad version: deleted, or back to a draft, the channel falls back to the one before)
        rels[-1]["draft"] = True
        self.assertEqual(release.newest(rels, "stable", 3), "v3.0.0")


class Bump(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="release-git-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.git("init", "-q", "-b", "main")
        self.commit("chore: start")

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "tag.gpgsign=false",
                               *args], cwd=self.dir, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, subject, body=""):
        self.git("commit", "-q", "--allow-empty", "-m", subject, *(["-m", body] if body else []))

    def check(self, tag, existing=()):
        return release.check_tag(tag, list(existing), 3, root=str(self.dir), main="main")

    def test_the_first_release(self):
        self.commit("feat: something")
        self.git("tag", "v2.9.0")
        self.git("tag", "v3.0.0-rc.1")
        self.assertEqual(self.check("v3.0.0-rc.1"), [])
        self.assertIn("is not vX.Y.Z", self.check("v2.9.0")[0])   # (Hookshot's numbers are not Hookshot's)
        self.assertEqual(release.next_tag([], 3, root=str(self.dir)), "v3.0.0-rc.1")

    def test_the_bump(self):
        self.git("tag", "v3.0.0")
        self.commit("fix(vk): a fix")
        self.assertEqual(release.required(3, str(self.dir))[:3:2], ("v3.0.1", "patch"))
        self.commit("feat(menu): a row")
        self.assertEqual(release.required(3, str(self.dir))[0], "v3.1.0")
        self.git("tag", "v3.0.1-rc.1")
        self.assertIn("at least v3.1.0", self.check("v3.0.1-rc.1", ["v3.0.0"])[0])
        self.git("tag", "v3.1.0-rc.1")
        self.assertEqual(self.check("v3.1.0-rc.1", ["v3.0.0"]), [])
        self.git("tag", "v4.0.0-rc.1")
        self.assertEqual(self.check("v4.0.0-rc.1", ["v3.0.0"]), [])            # (higher than needed is fine)
        self.commit("refactor(vk)!: a new interface")
        self.assertEqual(release.required(3, str(self.dir))[0], "v4.0.0")
        self.commit("fix: x", "BREAKING CHANGE: the options file")
        self.assertEqual(release.bump_of([("fix: x", "BREAKING CHANGE: the options file")]), "major")
        self.assertEqual(release.bump_of([("docs: x", ""), ("Merge branch", "")]), None)

    def test_a_stable_on_its_release_candidate(self):
        self.git("tag", "v3.0.0")
        self.commit("feat: a")
        self.git("tag", "v3.1.0-rc.1")
        self.assertEqual(release.next_tag(["v3.0.0", "v3.1.0-rc.1"], 3, root=str(self.dir)), "v3.1.0-rc.2")
        self.git("tag", "v3.1.0")                         # (the same commit: the normal path)
        self.assertEqual(self.check("v3.1.0", ["v3.0.0", "v3.1.0-rc.1"]), [])
        self.assertIn("not above every release", self.check("v3.1.0-rc.1", ["v3.0.0", "v3.1.0-rc.1", "v3.1.0"])[0])
        self.assertEqual(release.next_tag(["v3.0.0", "v3.1.0-rc.1", "v3.1.0"], 3, root=str(self.dir)), "v3.1.1-rc.1")

    def test_only_from_main(self):
        self.git("tag", "v3.0.0")
        self.git("switch", "-q", "-c", "side")
        self.commit("fix: on a branch")
        self.git("tag", "v3.0.1")
        self.assertIn("not on main", self.check("v3.0.1", ["v3.0.0"])[0])
        self.assertIn("no tag", self.check("v3.0.2", ["v3.0.0"])[0])

    def test_the_nightly_name(self):
        self.commit("fix: a")
        with mock.patch.dict(release.PRODUCTS["launcher"], since=None):
            name = release.nightly_name("launcher", root=str(self.dir))
        self.assertRegex(name, r"^nightly-2-[0-9a-f]{8}$")
        self.assertEqual(release.channel_of(name, 1), "nightly")

    def test_the_notes_start_after_the_previous_tag(self):
        self.git("tag", "v3.0.0")
        self.commit("feat: a")
        self.git("tag", "v3.1.0-rc.1")
        self.commit("fix: b")
        self.git("tag", "v3.1.0-rc.2")
        self.git("tag", "v3.1.0")
        self.assertEqual(release.previous_tag("v3.1.0-rc.2", 3, str(self.dir)), "v3.1.0-rc.1")
        self.assertEqual(release.previous_tag("v3.1.0", 3, str(self.dir)), "v3.0.0")   # (a stable: since the stable before)


NIGHTLY_OF = lambda tag: release.NIGHTLY.match(tag)


class FakeForgejo(http.server.BaseHTTPRequestHandler):
    releases, deleted, uploads = [], [], []

    def log_message(self, *a):
        pass

    def answer(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    max_items = 50   # (the server's max_response_items: /api/v1/settings/api)

    def do_GET(self):
        # (as Forgejo 17's GET /repos/{owner}/{repo}/releases: drafts only with a writer token; draft= and pre-release= keep
        # only the releases whose field equals the value; limit (default 30, at most max_response_items) and page; newest first)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        rels = sorted(self.releases, key=lambda r: r["id"], reverse=True)
        if not self.headers.get("Authorization"):
            rels = [r for r in rels if not r["draft"]]
        for param, field in (("draft", "draft"), ("pre-release", "prerelease")):
            if param in q:
                rels = [r for r in rels if r[field] == (q[param][0] == "true")]
        limit = min(int(q.get("limit", ["30"])[0]), self.max_items)
        page = int(q.get("page", ["1"])[0])
        self.answer(200, rels[(page - 1) * limit:page * limit])

    def do_POST(self):
        data = self.body()
        if "/assets" in self.path:
            rid = int(self.path.split("/releases/")[1].split("/")[0])
            name = self.path.split("name=")[1]
            payload = data.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
            self.uploads.append((rid, name, payload))
            rel = next(r for r in self.releases if r["id"] == rid)
            rel["assets"].append({"id": len(self.uploads), "name": name, "size": len(payload)})
            return self.answer(201, rel["assets"][-1])
        rel = dict(json.loads(data), id=len(self.releases) + 1, assets=[])
        self.releases.append(rel)
        self.answer(201, rel)

    def do_PATCH(self):
        rid = int(self.path.split("/releases/")[1])
        rel = next(r for r in self.releases if r["id"] == rid)
        rel.update(json.loads(self.body()))
        self.answer(200, rel)

    tags = set()   # (the tags there are: a draft's tag is made when it is published)

    def do_DELETE(self):
        self.deleted.append(self.path)
        if "/tags/" in self.path:
            tag = urllib.parse.unquote(self.path.rsplit("/", 1)[1])
            if tag not in self.tags:
                return self.answer(404, {"message": "tag not found"})
            self.tags.discard(tag)
        elif "/releases/" in self.path and "/assets/" not in self.path:
            FakeForgejo.releases = [r for r in self.releases if r["id"] != int(self.path.rsplit("/", 1)[1])]
        self.answer(204)


class Publish(unittest.TestCase):
    def setUp(self):
        FakeForgejo.releases, FakeForgejo.deleted, FakeForgejo.uploads, FakeForgejo.tags = [], [], [], set()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeForgejo)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.dir = Path(tempfile.mkdtemp(prefix="release-publish-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        env = {"FORGEJO_API": "http://127.0.0.1:%d/api/v1" % self.server.server_port, "RELEASE_TOKEN": "t",
               "GITHUB_REPOSITORY": "MagicBeanDev/Test"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_draft_then_the_files_then_published(self):
        notes = self.dir / "notes.md"
        notes.write_text("## v3.0.0-rc.1\n")
        a, b = self.dir / "Hookshot-v3.0.0-rc.1-x86_64.AppImage", self.dir / "SHA256SUMS"
        a.write_bytes(b"\x7fELF builder")
        b.write_text("sums\n")
        run("--product", "hookshot", "publish", "v3.0.0-rc.1", "--target", "abc", "--notes", notes, a, b)
        rel = FakeForgejo.releases[0]
        self.assertEqual((rel["tag_name"], rel["draft"], rel["prerelease"], rel["name"]), ("v3.0.0-rc.1", False, True, "Hookshot v3.0.0-rc.1"))
        self.assertEqual([(n, p) for _, n, p in FakeForgejo.uploads], [(a.name, b"\x7fELF builder"), ("SHA256SUMS", b"sums\n")])
        with self.assertRaises(SystemExit):   # (a published version's files never change)
            run("--product", "hookshot", "publish", "v3.0.0-rc.1", "--target", "abc", "--notes", notes, a)
        bad = self.dir / "image.bin"
        bad.write_bytes(b"x")
        with self.assertRaises(SystemExit):
            run("--product", "hookshot", "publish", "v3.0.1-rc.1", "--target", "abc", "--notes", notes, bad)

    def test_a_draft_left_by_a_failed_run_is_reused(self):
        FakeForgejo.releases = [{"id": 1, "tag_name": "v3.0.0", "draft": True, "prerelease": False,
                                 "assets": [{"id": 9, "name": "half", "size": 1}]}]
        notes = self.dir / "notes.md"
        notes.write_text("notes\n")
        f = self.dir / "SHA256SUMS"
        f.write_text("x\n")
        run("--product", "hookshot", "publish", "v3.0.0", "--target", "abc", "--notes", notes, f)
        self.assertEqual(len(FakeForgejo.releases), 1)
        self.assertIn("/api/v1/repos/MagicBeanDev/Test/releases/1/assets/9", FakeForgejo.deleted)
        self.assertEqual((FakeForgejo.releases[0]["draft"], FakeForgejo.releases[0]["prerelease"]), (False, False))

    def releases_of(self, *rows):
        """FakeForgejo's releases from (tag, draft, prerelease) rows; a published one has its tag"""
        FakeForgejo.releases = [{"id": i + 1, "tag_name": t, "draft": d, "prerelease": p, "assets": []}
                                for i, (t, d, p) in enumerate(rows)]
        FakeForgejo.tags = {t for t, d, _ in rows if not d}

    def test_prune_keeps_the_newest_nightlies_and_everything_else(self):
        nightly = lambda n: "nightly-%d-%08x" % (n, n)
        self.releases_of(*[(nightly(n), False, True) for n in (3, 12, 7, 100, 50, 9, 1)],
                         ("tools", False, True), ("v3.0.0", False, False), ("v3.1.0-rc.1", False, True), ("v2.9.0", False, False),
                         (nightly(4), True, True), (nightly(101), True, True))   # (drafts a failed run left; 101: still to come)
        run("--product", "hookshot", "prune", "--keep", "5")
        left = sorted(r["tag_name"] for r in FakeForgejo.releases)
        self.assertEqual(left, sorted([nightly(n) for n in (101, 100, 50, 12, 9, 7)] + ["tools", "v3.0.0", "v3.1.0-rc.1", "v2.9.0"]))
        self.assertIn("/api/v1/repos/MagicBeanDev/Test/tags/nightly-1-00000001", FakeForgejo.deleted)
        self.assertIn("/api/v1/repos/MagicBeanDev/Test/tags/nightly-3-00000003", FakeForgejo.deleted)
        self.assertEqual(FakeForgejo.tags, {nightly(n) for n in (100, 50, 12, 9, 7)} | {"tools", "v3.0.0", "v3.1.0-rc.1", "v2.9.0"})
        kept = [r["id"] for r in FakeForgejo.releases if not NIGHTLY_OF(r["tag_name"])]
        self.assertFalse([p for p in FakeForgejo.deleted if p.rsplit("/", 1)[1] in [str(i) for i in kept] + ["tools", "v3.0.0", "v3.1.0-rc.1", "v2.9.0"]])

    def test_every_release_on_every_page(self):
        # (more than a page, at a smaller max_response_items than release.py asks for: pages until an empty one)
        FakeForgejo.max_items = 20
        self.addCleanup(setattr, FakeForgejo, "max_items", 50)
        self.releases_of(*[("nightly-%d-%08x" % (n, n), n % 7 == 0, True) for n in range(1, 46)], ("v3.0.0", False, False))
        got = release.releases("MagicBeanDev/Test")
        self.assertEqual(len(got), 46)
        self.assertEqual(sum(r["draft"] for r in got), 6)
        self.assertEqual(release.newest(got, "nightly", 3), "nightly-45-0000002d")
        self.assertEqual(release.newest(got, "stable", 3), "v3.0.0")

    def test_check_tag_against_the_published_releases(self):
        # (check-tag asks Forgejo: a tag at or below a published release is refused; a draft is no release yet)
        repo = Path(tempfile.mkdtemp(prefix="release-git-"))
        self.addCleanup(shutil.rmtree, repo, True)
        g = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "tag.gpgsign=false",
                                       *a], cwd=repo, check=True, capture_output=True)
        g("init", "-q", "-b", "main")
        g("commit", "-q", "--allow-empty", "-m", "feat: a")
        g("tag", "v3.0.0-rc.1")
        self.releases_of(("v3.0.0", False, False), ("v3.1.0-rc.1", True, True), ("tools", False, True))
        with mock.patch.object(release, "ROOT", str(repo)), mock.patch.object(release, "git",
                lambda *a, root=None, check=True, _g=release.git: _g(*a, root=str(repo), check=check)):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(run("--product", "hookshot", "check-tag", "v3.0.0-rc.1", "--main", "main")[0], 1)
            self.assertIn("not above every release there is: v3.0.0", err.getvalue())
            FakeForgejo.releases[0]["draft"] = True   # (back to a draft: no longer in the way)
            self.assertEqual(run("--product", "hookshot", "check-tag", "v3.0.0-rc.1", "--main", "main")[0], 0)

    def test_publish_refuses_a_published_tag_on_a_later_page(self):
        self.releases_of(("v3.0.0", False, False), *[("nightly-%d-%08x" % (n, n), False, True) for n in range(1, 80)])
        notes = self.dir / "notes.md"
        notes.write_text("notes\n")
        f = self.dir / "SHA256SUMS"
        f.write_text("x\n")
        with self.assertRaises(SystemExit) as e:
            run("--product", "hookshot", "publish", "v3.0.0", "--target", "abc", "--notes", notes, f)
        self.assertIn("already published", str(e.exception))
        self.assertEqual(FakeForgejo.uploads, [])


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
