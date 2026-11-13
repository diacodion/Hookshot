"""tools.lock and scripts/tools.py (docs/releases.md, "tools.lock"), with authored archives and no network: the lock in the
repository reads and names only https links; a bad line is refused; a tool comes from the first URL that gives the bytes the
lock names (the next one when upstream is gone or changed); the download cache is shared; only the archive's prefix is
unpacked, and nothing outside the install; a line is replaced in place; and the archives Hookshot assembles (Python with the
prompt packages, the CPM cache with dav1d, the third_party prebuilt files, the play profile) are deterministic and hold only
what they should. builder/setup/setup.c and the launcher's Core/ToolsLock.cs read the same lines.

    python -I tests/test_tools_lock.py
"""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tools", ROOT / "scripts" / "tools.py")
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


def zip_of(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


class Lock(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="tools-lock-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.out = io.StringIO()
        ctx = contextlib.redirect_stdout(self.out)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def test_the_repository_lock(self):
        lock = tools.load()
        self.assertTrue(lock)
        for t in lock:
            self.assertTrue(all(u.startswith("https://") for u in t.urls), t.name)
            self.assertEqual(t.system, "windows-x64")
            self.assertTrue(t.dest.startswith("HookshotBuilder/tools/"), t.name)
            # (the forge's copy first, where it came from as the fallback: never a repository we do not control alone)
            self.assertTrue(t.urls[0].startswith(tools.MIRROR), "%s: its copy in the tools release is not its first URL" % t.name)
            self.assertEqual(t.urls[0], "%s%s-%s.zip" % (tools.MIRROR, t.sha256, t.name), t.name)
        self.assertEqual(tools.named(lock, "llvm-mingw").prefix, "llvm-mingw-20261104-ucrt-x86_64")
        # (the release workflow requires every tool; what is still to assemble is listed in RELEASING.md)
        with self.assertRaises(SystemExit):
            tools.named(lock, "nothing")

    def test_a_bad_line_is_refused(self):
        good = "tool zstd 1.0 windows-x64 %s 10 HookshotBuilder/tools/zstd - https://x/z.zip" % ("a" * 64)
        self.assertEqual(tools.parse(good)[0].prefix, "-")
        for bad in (good.replace("https://", "http://"), good.replace("a" * 64, "A" * 64), good.replace(" 10 ", " -1 "),
                    good.replace("HookshotBuilder/tools/zstd", "../zstd"), good.replace("HookshotBuilder/tools/zstd", "/etc"),
                    good.replace(" - ", " a/../b "), good.replace("windows-x64", "mac"), good + "\n" + good, "part x"):
            with self.assertRaises(SystemExit, msg=bad):
                tools.parse(bad)

    def test_fetch_tries_the_next_url_and_shares_the_cache(self):
        sha, size = zip_of(self.dir / "a.zip", {"t/x.txt": "x"})
        data = (self.dir / "a.zip").read_bytes()
        t = tools.parse("tool t 1 windows-x64 %s %d T t https://upstream/a.zip https://mirror/a.zip" % (sha, size))[0]
        asked = []

        def getter(url):
            asked.append(url)
            return b"changed upstream" if "upstream" in url else data
        path = tools.fetch(t, str(self.dir / "cache"), getter)
        self.assertEqual((Path(path).read_bytes(), asked), (data, ["https://upstream/a.zip", "https://mirror/a.zip"]))
        self.assertEqual(tools.fetch(t, str(self.dir / "cache"), lambda url: self.fail("downloaded again")), path)
        with self.assertRaises(SystemExit):
            tools.fetch(t, str(self.dir / "cache2"), lambda url: b"nothing right")

    def test_install_unpacks_only_the_prefix_and_stays_inside(self):
        sha, size = zip_of(self.dir / "a.zip", {"HookshotBuilder/tools/python/python.exe": "py", "HookshotBuilder/app.exe": "no"})
        t = tools.parse("tool python 3 windows-x64 %s %d HookshotBuilder/tools/python HookshotBuilder/tools/python https://x/a"
                        % (sha, size))[0]
        data = (self.dir / "a.zip").read_bytes()
        tools.install(str(self.dir / "install"), [t], str(self.dir / "cache"), lambda url: data)
        self.assertEqual(sorted(str(p.relative_to(self.dir / "install")) for p in (self.dir / "install").rglob("*") if p.is_file()),
                         [os.path.join("HookshotBuilder", "tools", "python", "python.exe")])
        tools.install(str(self.dir / "install"), [t], str(self.dir / "cache"), lambda url: self.fail("again"))   # (there already)
        sha, size = zip_of(self.dir / "b.zip", {"p/../../evil.txt": "x"})
        bad = tools.parse("tool p 1 windows-x64 %s %d T p https://x/b" % (sha, size))[0]
        bdata = (self.dir / "b.zip").read_bytes()
        with self.assertRaises(SystemExit):
            tools.install(str(self.dir / "i2"), [bad], str(self.dir / "cache"), lambda url: bdata)
        self.assertFalse((self.dir / "evil.txt").exists())

    def test_a_line_is_replaced_in_place(self):
        lock = self.dir / "tools.lock"
        shutil.copy(tools.LOCK, lock)
        before = lock.read_text()
        t = tools.named(tools.load(str(lock)), "cpm")
        t.version, t.urls = "dav1d", [tools.MIRROR + "x.zip"]
        tools.write_line(t, str(lock))
        after = lock.read_text()
        self.assertEqual(len(after.splitlines()), len(before.splitlines()))
        self.assertIn("tool cpm dav1d windows-x64", after)
        self.assertTrue(after.startswith("# "))
        tools.write_line(tools.Tool("profile", "1", "windows-x64", "b" * 64, 5, "HookshotBuilder/tools/work", "work",
                                    [tools.MIRROR + "p.zip"]), str(lock))
        self.assertEqual(tools.named(tools.load(str(lock)), "profile").prefix, "work")

    def test_check_requires_every_tool(self):
        lock = self.dir / "tools.lock"
        lock.write_text("tool llvm-mingw 1 windows-x64 %s 10 HookshotBuilder/tools/llvm-mingw - https://x/l\n" % ("a" * 64))
        self.assertEqual(tools.main(["--lock", str(lock), "check"]), 0)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tools.main(["--lock", str(lock), "check", "--require"]), 1)


class Assemble(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="tools-assemble-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        ctx = contextlib.redirect_stdout(io.StringIO())
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

    def test_cpm_gets_dav1d_once(self):
        zip_of(self.dir / "cpm.zip", {"HookshotBuilder/tools/cpm/ffmpeg/9/lib/avcodec.lib": "ffmpeg"})
        add = self.dir / "add" / "dav1d" / "1.5.4" / "lib"
        add.mkdir(parents=True)
        (add / "libdav1d.a").write_bytes(b"!<arch>\n")
        t = tools.Tool("cpm", "1", "windows-x64", "a" * 64, 1, "HookshotBuilder/tools/cpm", "HookshotBuilder/tools/cpm", ["https://x"])
        first = tools.assemble_cpm(t, str(self.dir / "cpm.zip"), str(self.dir / "add"), str(self.dir / "new.zip"))
        with zipfile.ZipFile(self.dir / "new.zip") as z:
            self.assertEqual(sorted(z.namelist()), ["cpm/dav1d/1.5.4/lib/libdav1d.a", "cpm/ffmpeg/9/lib/avcodec.lib"])
        again = tools.assemble_cpm(t, str(self.dir / "cpm.zip"), str(self.dir / "add"), str(self.dir / "again.zip"))
        self.assertEqual(first, again)                                   # (deterministic)
        t2 = tools.Tool("cpm", "2", "windows-x64", "a" * 64, 1, "HookshotBuilder/tools/cpm", "cpm", ["https://x"])
        self.assertIsNone(tools.assemble_cpm(t2, str(self.dir / "new.zip"), str(self.dir / "add"), str(self.dir / "n.zip")))

    def test_python_gets_the_missing_pins_for_its_version(self):
        zip_of(self.dir / "py.zip", {"HookshotBuilder/tools/python/python314.dll": "dll",
                                     "HookshotBuilder/tools/python/Lib/site-packages/pillow-13.0.0.dist-info/METADATA": "m"})
        req = self.dir / "req.txt"
        req.write_text("pillow==13.0.0\nfonttools==4.68.0  # a comment\n")
        t = tools.Tool("python", "1", "windows-x64", "a" * 64, 1, "HookshotBuilder/tools/python", "HookshotBuilder/tools/python", ["https://x"])
        ran = []

        def pip(args, check):
            ran.append(args)
            target = Path(args[args.index("--target") + 1])
            (target / "fontTools").mkdir(parents=True)
            (target / "fontTools" / "__init__.py").write_text("")
        tools.assemble_python(t, str(self.dir / "py.zip"), str(req), str(self.dir / "new.zip"), pip=pip)
        self.assertEqual(ran[0][-1], "fonttools==4.68.0")
        self.assertIn("3.14", ran[0])
        self.assertIn("win_amd64", ran[0])
        with zipfile.ZipFile(self.dir / "new.zip") as z:
            self.assertIn("python/Lib/site-packages/fontTools/__init__.py", z.namelist())
            self.assertIn("python/python314.dll", z.namelist())

    def test_third_party_keeps_only_prebuilt_files(self):
        zip_of(self.dir / "tp.zip", {"HookshotBuilder/tools/third_party/a64-kit/lift/a64emit.dll": "dll",
                                     "HookshotBuilder/tools/third_party/a64-kit/lift/a64_lift.py": "Hookshot's source",
                                     "HookshotBuilder/tools/third_party/sdl2/SDL3.dll": "sdl"})
        tools.assemble_third_party(str(self.dir / "tp.zip"), "HookshotBuilder/tools/third_party", str(self.dir / "o.zip"),
                                   {"third_party/a64-kit/lift/a64_lift.py"})
        with zipfile.ZipFile(self.dir / "o.zip") as z:
            self.assertEqual(sorted(z.namelist()), ["third_party/a64-kit/lift/a64emit.dll", "third_party/sdl2/SDL3.dll"])

    def test_the_profile(self):
        work = self.dir / "work"
        work.mkdir()
        for n in ("hot_order.txt", "hookshot_pieces.prof", "image.bin", "dump.txt"):
            (work / n).write_text(n)
        tools.assemble_profile(str(work), str(self.dir / "p.zip"))
        with zipfile.ZipFile(self.dir / "p.zip") as z:
            self.assertEqual(sorted(z.namelist()), ["work/hot_order.txt", "work/hookshot_pieces.prof"])   # (nothing of the game)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
