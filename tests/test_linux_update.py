"""The Linux builder's versions and its game folder launcher: which button a game folder gets, the version these tools are,
the folder launcher starting the builder that made the game (or the game), and the packaging of the parts a Hookshot Launcher
parts list names (scripts/linux/make_parts.py, for HOOKSHOT_LAUNCHER_PARTS).

python -I -B tests/test_linux_update.py
"""
import contextlib
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
import zipfile
from pathlib import Path

# (Linux only)
RUN_ALL_SKIP = {"windows": "the Linux builder's versions and folder launcher, POSIX files and shell scripts"}

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "builder" / "linux"))
sys.path.insert(0, str(ROOT / "nx2"))
sys.path.insert(0, str(ROOT / "scripts" / "linux"))
import linux_update  # noqa: E402
import make_parts  # noqa: E402
from part_links import SYSROOT_PART  # noqa: E402


def appdir(path):
    """A tiny AppDir: one file in each part, plus the launcher script an install copies to AppRun."""
    tools = path / "usr" / "share" / "hookshot"
    (tools / "builder" / "linux").mkdir(parents=True)
    shutil.copyfile(ROOT / "builder" / "linux" / "AppRun", tools / "builder" / "linux" / "AppRun")
    (tools / "VERSION").write_text("v1\n", encoding="utf-8")
    (tools / "nx2").mkdir()
    (tools / "nx2" / "marker.py").write_text("print('one')\n", encoding="utf-8")
    for dest in ("usr/python", "usr/lib/hookshot/emsdk", "usr/lib/hookshot/ffmpeg", "usr/lib/hookshot/sysroot"):
        folder = path / dest
        folder.mkdir(parents=True)
        (folder / "kept").write_text(dest + "\n", encoding="utf-8")
    lib = path / "usr" / "lib" / "hookshot"
    (lib / "hookshot_vk.so").write_bytes(b"vk")
    (lib / "libSDL2-2.0.so.0").write_bytes(b"sdl")
    (path / "AppRun").write_text("#!/bin/sh\n", encoding="utf-8")
    (path / "AppRun").chmod(0o755)


class VersionTests(unittest.TestCase):
    def test_the_button_matches_the_windows_builder(self):
        # (an older game that keeps its files is built again with this builder; its own or a newer one is played)
        self.assertEqual(linux_update.choose_mode("v1", "v2", False, True), "update-game")
        self.assertEqual(linux_update.choose_mode("v2", "v2", False, True), "up-to-date")
        self.assertEqual(linux_update.choose_mode("v1", "v2", False, False), "up-to-date")
        self.assertEqual(linux_update.choose_mode(None, "v2", False, False), "build")
        self.assertEqual(linux_update.choose_mode("v2", "v2", True, True), "build")

    def test_a_build_from_the_actions_tab_builds_and_updates_older_games(self):
        # (issue 902) an AppImage from the Actions tab is git describe's version, after the latest release
        ci = "v2.0.5-3-g1a2b3c4"
        for tools in (ci, "1a2b3c4", "dev", "v2.0.6", "v2.1.0-rc.1"):
            self.assertEqual(linux_update.choose_mode(None, tools, False, False), "build", tools)
        self.assertEqual(linux_update.choose_mode(ci, ci, False, True), "up-to-date")
        self.assertEqual(linux_update.choose_mode("v2.0.4", ci, False, True), "update-game")
        self.assertEqual(linux_update.choose_mode("v2.0.5", ci, False, True), "update-game")
        # (a source checkout has no version: it never offers Update)
        self.assertEqual(linux_update.choose_mode("v2.0.5", "", False, True), "up-to-date")

    def test_an_unpacked_builder_finds_its_own_root(self):
        with tempfile.TemporaryDirectory(prefix="hookshot-root-") as tmp:
            root = Path(tmp) / "versions" / "v1"
            appdir(root)
            saved = {k: os.environ.get(k) for k in ("APPDIR", "HOOKSHOT_TOOLS")}
            os.environ.pop("APPDIR", None)
            os.environ["HOOKSHOT_TOOLS"] = str(root / "usr" / "share" / "hookshot")
            try:
                found = linux_update.running_root()
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(found, str(root))
            self.assertEqual(linux_update.tools_tag(found), "v1")

    @unittest.skipUnless(sys.platform.startswith("linux"), "the Linux folder layout")
    def test_a_packaged_build_puts_the_launcher_in_the_folder_and_the_game_in_bin(self):
        import ast
        from unittest import mock
        import make_game
        main = next(n for n in ast.parse((ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")).body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        start = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "staged" for t in n.targets))
        end = next(i for i, n in enumerate(main.body[start + 1:], start + 1) if isinstance(n, ast.If)
                   and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "shader_census_cmd"
                           for c in ast.walk(n)))
        finish = compile(ast.Module(body=main.body[start:end], type_ignores=[]), "make_game.py", "exec")
        with tempfile.TemporaryDirectory(prefix="hookshot-finish-") as tmp:
            work, out = Path(tmp) / "work", Path(tmp) / "game"
            built = work / "sa" / "build"
            built.mkdir(parents=True)
            (work / "sa" / "image").mkdir()
            (work / "sa" / "image" / "image.bin").write_bytes(b"image")
            for name in make_game.BESIDE:
                (built / name).write_bytes(b"new " + name.encode())
            out.mkdir()
            for name in make_game.BESIDE:   # (a folder from a builder before the launcher: the game and its libraries in it)
                (out / name).write_bytes(b"old " + name.encode())
            with mock.patch.dict(os.environ, {"HOOKSHOT_TOOLS": str(ROOT)}):
                exec(finish, make_game.__dict__, {"built": str(built), "out": str(out), "work": str(work),
                                                   "a": SimpleNamespace(fsr=False, no_fsr=False, update=None, fetch_fsr_runtime=False)})
            self.assertEqual((out / "hookshot").read_bytes(), (ROOT / "builder" / "linux" / "hookshot-folder").read_bytes())
            self.assertTrue((out / "hookshot").stat().st_mode & stat.S_IXUSR)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["bin", "image.bin", "hookshot"])
            self.assertEqual(sorted(p.name for p in (out / "bin").iterdir()), sorted(make_game.BESIDE))
            self.assertEqual((out / "bin" / "hookshot").read_bytes(), b"new hookshot")

    def test_the_sysroot_path_is_not_part_of_an_object_key(self):
        import build
        handle, name = tempfile.mkstemp()
        os.write(handle, b"int x;\n")
        os.close(handle)
        try:
            one = build.object_key(name, ["--sysroot=/tmp/.mount_aaa/usr/lib/hookshot/sysroot", "-O2"], b"deps")
            two = build.object_key(name, ["--sysroot=/tmp/.mount_bbb/usr/lib/hookshot/sysroot", "-O2"], b"deps")
        finally:
            os.remove(name)
        self.assertEqual(one, two)

    def test_the_folder_launcher_starts_the_builder(self):
        with tempfile.TemporaryDirectory(prefix="hookshot-folder-") as tmp:
            root = Path(tmp)
            game = root / "game"
            (game / "bin").mkdir(parents=True)
            (game / "image.bin").write_bytes(b"image")
            started = root / "started"
            builder = root / "builder"
            builder.write_text("#!/bin/sh\nprintf '%%s\\n' \"$0\" \"$@\" > '%s'\n" % started, encoding="utf-8")
            builder.chmod(0o755)
            (game / "hookshot_version.txt").write_text("version=v1\nbuilder=%s\n" % builder, encoding="utf-8")
            script = game / "hookshot"
            shutil.copyfile(ROOT / "builder" / "linux" / "hookshot-folder", script)
            script.chmod(0o755)
            env = os.environ.copy()
            ran = subprocess.run([str(script)], env=env, check=False, text=True, capture_output=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            lines = started.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[1], "--launch")
            self.assertEqual(lines[2], str(game))

    def test_the_folder_launcher_starts_the_game_when_its_builder_is_gone(self):
        with tempfile.TemporaryDirectory(prefix="hookshot-folder-") as tmp:
            root = Path(tmp)
            game = root / "game"
            (game / "bin").mkdir(parents=True)
            (game / "bin" / "hookshot").write_text("#!/bin/sh\nprintf 'game\\n'\n", encoding="utf-8")
            (game / "bin" / "hookshot").chmod(0o755)
            (game / "hookshot_version.txt").write_text("version=v1\nbuilder=%s\n" % (root / "gone"), encoding="utf-8")
            script = game / "hookshot"
            shutil.copyfile(ROOT / "builder" / "linux" / "hookshot-folder", script)
            script.chmod(0o755)
            env = os.environ.copy()
            ran = subprocess.run([str(script)], env=env, check=False, text=True, capture_output=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertEqual(ran.stdout.strip(), "game")

    def test_packaging_refuses_a_sysroot_link_outside_its_root(self):
        with tempfile.TemporaryDirectory(prefix="hookshot-sysroot-") as tmp:
            root = Path(tmp)
            source = root / SYSROOT_PART
            source.mkdir(parents=True)
            (source / "bad").symlink_to("/../outside")
            with self.assertRaises(ValueError):
                make_parts.write_zip(str(root / "part.zip"), str(root), SYSROOT_PART, None)

    @unittest.skipUnless(sys.platform.startswith("linux"), "the Linux sysroot copy loop")
    def test_sysroot_system_aliases_round_trip_as_library_bytes(self):
        # (the sysroot is made by deps.sh tools, then packed as tools.lock's archive: scripts/tools.py pack and unpack, the
        # builder's first start and the launcher, carry it; the parts list below is the older path)
        script = (ROOT / "scripts" / "linux" / "deps.sh").read_text(encoding="utf-8")
        start = script.index("for f in crt1.o ")
        loop = script[start:script.index("\n    done", start) + len("\n    done")]
        with tempfile.TemporaryDirectory(prefix="hookshot-sysroot-") as tmp:
            root = Path(tmp)
            system = root / "system"
            system.mkdir()
            real = system / "libmvec.so.1"
            real.write_bytes(b"private library bytes")
            alias = system / "libmvec.so"
            alias.symlink_to(real)
            source = root / "sysroot"
            (source / "usr" / "lib" / "x86_64-linux-gnu").mkdir(parents=True)
            (source / "lib").symlink_to("usr/lib")
            env = dict(os.environ, M=str(system), S=str(source))
            subprocess.run(["bash", "-euo", "pipefail", "-c", loop], env=env, check=True)
            copied = source / "usr" / "lib" / "x86_64-linux-gnu" / alias.name
            self.assertFalse(copied.is_symlink(), "the sysroot must carry the alias's bytes, not an absolute target")
            archive = root / "part.zip"
            make_parts.write_zip(str(archive), str(root), "sysroot", None)
            with zipfile.ZipFile(archive) as zipped:   # (lib is a link to usr/lib: the file is under usr/lib)
                self.assertEqual(zipped.read("usr/lib/x86_64-linux-gnu/" + alias.name), real.read_bytes())
            spec = importlib.util.spec_from_file_location("tools", ROOT / "scripts" / "tools.py")
            tools = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tools)
            with contextlib.redirect_stdout(io.StringIO()):
                tools.pack(str(source), str(root / "sysroot.zip"))
            tools.unpack(str(root / "sysroot.zip"), "sysroot", str(root / "tool"))
            self.assertEqual(os.readlink(root / "tool" / "lib"), "usr/lib")   # (the one link: relative, as packed)
            self.assertEqual((root / "tool" / "lib" / "x86_64-linux-gnu" / alias.name).read_bytes(), real.read_bytes())
            self.assertEqual(os.readlink(alias), str(real))
            self.assertEqual(real.read_bytes(), b"private library bytes")


if __name__ == "__main__":
    unittest.main()
