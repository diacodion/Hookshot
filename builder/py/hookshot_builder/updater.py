"""--update <game folder> [--tag <release>]: the folder built again (nx2/make_game.py --update, from the game files the folder keeps).
The builder looks for no release itself: the Hookshot Launcher finds new versions and installs their builders (docs/releases.md).
A version is installed here only from a list given with HOOKSHOT_SETUP_LIST (setup_parts: only the parts that changed are
downloaded), and this app then starts again on it with --fetched to build the folder."""
import os
import subprocess
import threading

from . import paths, setup_parts
from .core import Tools


def latest_tag():
    """The newest version known: none, as the builder asks no server (the Hookshot Launcher does). HOOKSHOT_LATEST_TAG names one,
    for development and for a list given with HOOKSHOT_SETUP_LIST"""
    t = os.environ.get("HOOKSHOT_LATEST_TAG")
    return None if not t or t == "none" else t


def restart(args):
    """This app started again (on the version just installed: hookshot_builder_start.py picks it) with args, and this one closes"""
    exe, base_args = paths.launcher_command()
    subprocess.Popen([exe, *base_args, *args], cwd=os.path.dirname(exe))


class Install:
    """The release installed on a worker thread; ui is the window (note, download_progress, download_failed, and done(tag) called
    on the window's thread through ui.post)"""

    def __init__(self, ui, tag=None, then=None):
        self.ui, self.tag, self.then, self.cancelled = ui, tag, then, False

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def cancel(self):
        self.cancelled = True

    def _run(self):
        post = self.ui.post
        try:
            tag, _ = setup_parts.install(lambda s: post(self.ui.note, s), lambda f: post(self.ui.download_progress, f) if f >= 0 else None,
                                         tag=self.tag, cancel=lambda: self.cancelled)
            post(self.then, tag)
        except setup_parts.SetupError as e:
            msg = str(e)
            post(self.ui.download_failed, "The download was cancelled. Your game folder is unchanged." if msg == "cancelled" else
                 msg + " Your game folder is unchanged.")
        except Exception as e:   # (anything else: said, not a silent window)
            post(self.ui.download_failed, "%s. Your game folder is unchanged." % e)


def update_folder(ui, folder, tag, fetched):
    """The game in folder built again with the latest release: here when these tools are it (or fetched), else after installing it
    (this app then starts again on it with --update folder --tag t --fetched)"""
    ui.begin_update(folder, tag)
    tools = Tools.find()
    # (just installed, but another release than asked for: the list named another version)
    if fetched and tag and tools and tools.version and tools.version != tag:
        ui.download_failed("The update installed %s, not %s. Use the Hookshot Launcher to update." % (tools.version, tag))
        return
    if tools and (fetched or (tag and tools.version == tag)):
        ui.update_build(tools, folder)
        return
    ui.note("Installing the version the parts list names")

    def installed(new_tag):
        restart(["--update", folder, "--tag", new_tag, "--fetched"])
        ui.quit_app()
    ui.download = Install(ui, tag, installed).start()


def run_latest_setup(ui):
    """The latest release installed, then this app started again on it (the builder's own update)"""
    ui.note("Installing the version the parts list names")

    def installed(_tag):
        restart([])
        ui.quit_app()
    ui.download = Install(ui, None, installed).start()
