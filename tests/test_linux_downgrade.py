"""Game-free: folder dispatch, version admission and the real Tk update handlers before make_game.py starts."""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "builder/linux"))
import linux_update


class Value:
    def __init__(self, value=""):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class Widget:
    def __init__(self):
        self.config = {}
        self.visible = False
    def winfo_exists(self):
        return True
    def configure(self, **kwargs):
        self.config.update(kwargs)
    def pack(self, **kwargs):
        self.visible = True
    def pack_forget(self):
        self.visible = False


def handlers(answer):
    """Compile complete production handlers; only UI/process/filesystem observations are authored boundaries."""
    tree = ast.parse((ROOT / "builder/linux/hookshot_builder.py").read_text(encoding="utf-8"))
    builder = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Builder")
    names = {"older_ok", "begin_update", "build", "apply_mode", "apply_launch"}
    methods = [n for n in builder.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(methods) == len(names)
    events = []
    popup = types.SimpleNamespace(askyesno=lambda *a: events.append("confirm") or answer)
    namespace = dict(os=os, linux_update=linux_update, messagebox=popup,
                     tk=types.SimpleNamespace(TclError=RuntimeError), folder_has_dlss=lambda _: False)
    exec(compile(ast.Module(body=methods, type_ignores=[]), "production-builder-handlers", "exec"), namespace)
    app = types.SimpleNamespace(tools="v2.1.0", source_picked=False, update_folder="prior", out=Value("authored-game"),
                                launch_folder="", build_button=Widget(),
                                status=Value(), launch_status=Value(), launch_update=Widget(), px=lambda n: n,
                                dlss=Value(True), dlss_toggled=False,
                                show_progress=lambda _: events.append("progress"),
                                show_form=lambda: events.append("form"),
                                start_make_update=lambda folder: events.append(("make", folder)))
    for name in names:
        setattr(app, name, types.MethodType(namespace[name], app))
    return app, events


class Admission(unittest.TestCase):
    def test_default_never_rebuilds_a_known_newer_game_backwards(self):
        for game, tools in [("v2.2.0", "v2.1.0"), ("v2.2.0", "v2.0.14"), ("v2.1.0", "v2.1.0-rc.10"),
                            ("v2.1.0-rc.10", "v2.1.0-rc.9"), ("v2.1.0-3-gabc1234", "v2.1.0")]:
            with self.subTest(game=game, tools=tools):
                self.assertEqual(linux_update.choose_mode(game, tools, False, True), "up-to-date")

    def test_forward_and_unordered_versions_are_updated(self):
        for game, tools in [("v2.0.9", "v2.0.10"), ("v2.1.0-rc.9", "v2.1.0-rc.10"), ("v2.1.0-rc.10", "v2.1.0"),
                            ("v2.1.0", "v2.1.0-3-gabc1234"), ("v2.1.0-3-gabc1234", "v2.1.0-4-gdef1234"),
                            ("authored-hash", "v2.1.0")]:
            with self.subTest(game=game, tools=tools):
                self.assertEqual(linux_update.choose_mode(game, tools, False, True), "update-game")
        self.assertEqual(linux_update.choose_mode("v2.2.0", "v2.1.0", True, True), "build")
        self.assertEqual(linux_update.choose_mode("v2.2.0", "v2.1.0", False, False), "up-to-date")

    def test_direct_launcher_update_decline_starts_nothing_and_preserves_page(self):
        app, events = handlers(False)
        with mock.patch.object(linux_update, "game_version", return_value=("v2.2.0", True)):
            app.begin_update("authored-game")
        self.assertEqual(events, ["confirm"])
        self.assertEqual(app.update_folder, "prior")

    def test_acceptance_and_forward_update_build_that_folder_once(self):
        for game, answer, expected in [("v2.2.0", True, ["confirm", "progress", ("make", "authored-game")]),
                                        ("v2.0.14", False, ["progress", ("make", "authored-game")])]:
            app, events = handlers(answer)
            with mock.patch.object(linux_update, "game_version", return_value=(game, True)):
                app.begin_update("authored-game")
            self.assertEqual(events, expected)
            self.assertEqual(app.update_folder, "authored-game")

    def test_an_update_with_no_folder_goes_back_to_the_form(self):
        app, events = handlers(True)
        app.update_folder = ""
        app.begin_update()
        self.assertEqual(events, ["form"])

    def test_form_and_launcher_do_not_offer_an_implicit_downgrade(self):
        app, events = handlers(False)
        with mock.patch.object(linux_update, "game_version", return_value=("v2.2.0", True)):
            app.apply_mode()
            self.assertEqual(app.mode, "up-to-date")
            self.assertEqual(app.build_button.config["text"], "Play")
            self.assertIn("newer than", app.status.get())
            app.launch_folder = "authored-game"
            app.apply_launch()
            self.assertFalse(app.launch_update.visible)
            self.assertIn("newer than", app.launch_status.get())
        self.assertEqual(events, [])

    def test_the_launcher_offers_update_for_an_older_game(self):
        app, events = handlers(False)
        app.launch_folder = "authored-game"
        with mock.patch.object(linux_update, "game_version", return_value=("v2.0.14", True)):
            app.apply_launch()
        self.assertTrue(app.launch_update.visible)
        self.assertEqual(app.launch_status.get(), "Update available: v2.1.0")

    def test_dlss_rebuild_cannot_bypass_downgrade_confirmation(self):
        app, events = handlers(False)
        app.dlss_toggled = True
        with mock.patch.object(linux_update, "game_version", return_value=("v2.2.0", True)):
            app.apply_mode()
            self.assertEqual(app.mode, "update-game")
            app.build()
        self.assertEqual(events, ["confirm"])
        self.assertEqual(app.update_folder, "prior")

    def test_cli_update_opens_the_builder_on_that_folder(self):
        tree = ast.parse((ROOT / "builder/linux/hookshot_builder.py").read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        args = ["review", "--update", "authored-game"]
        received = []
        def argument(name):
            return args[args.index(name) + 1] if name in args else None
        namespace = dict(sys=types.SimpleNamespace(argv=args), _arg=argument,
                         Builder=lambda **kw: received.append(kw) or types.SimpleNamespace(mainloop=lambda: None))
        exec(compile(ast.Module(body=[main], type_ignores=[]), "production-main", "exec"), namespace)
        namespace["main"]()
        self.assertEqual(received, [dict(update="authored-game")])


@unittest.skipUnless(sys.platform.startswith("linux"), "actual POSIX folder launcher")
class FolderDispatch(unittest.TestCase):
    def test_recorded_builder_owns_its_game_with_a_game_fallback(self):
        with tempfile.TemporaryDirectory(prefix="builder-dispatch-") as folder:
            root = Path(folder)
            game = root / "Authored 'game % folder"
            (game / "bin").mkdir(parents=True)
            script = game / "hookshot"
            shutil.copyfile(ROOT / "builder/linux/hookshot-folder", script)
            script.chmod(0o700)
            recorded = root / "Recorded 'Builder.AppImage"
            direct = game / "bin/hookshot"
            for path, label in [(recorded, "recorded"), (direct, "game")]:
                path.write_text("#!/bin/sh\nprintf '%s\\n' " + label + " \"$@\"\n")
                path.chmod(0o700)
            stamp = game / "hookshot_version.txt"
            stamp.write_text("version=v2.1.0-rc.3\nbuilder=" + str(recorded) + "\n")
            def run(expected):
                ran = subprocess.run([str(script)], capture_output=True, text=True, timeout=10)
                self.assertEqual(ran.returncode, 0, ran.stderr)
                self.assertEqual(ran.stdout.splitlines(), [expected, "--launch", str(game)] if expected != "game" else ["game"])
            run("recorded")
            recorded.chmod(0o600)
            run("game")
            recorded.unlink()
            run("game")
            stamp.unlink()
            run("game")


if __name__ == "__main__":
    unittest.main()
