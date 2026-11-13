"""--update <game folder>: the folder built again with this builder's own tools (nx2/make_game.py --update, from the game files
the folder keeps), as the WPF builder's Updater.cs does. The builder looks for and installs no release itself: the Hookshot
Launcher installs each version's builder and moves it to its channel's newest (docs/releases.md)."""
import os
import subprocess

from . import paths
from .core import Tools


def restart(args):
    """This app started again with args (Repair: --repair), and this one closes"""
    exe, base_args = paths.launcher_command()
    subprocess.Popen([exe, *base_args, *args], cwd=os.path.dirname(exe))


def update_folder(ui, folder):
    """The game in folder built again with these tools"""
    tools = Tools.find()
    ui.begin_update(folder, tools.version if tools else None)
    if not tools:
        ui.download_failed("The Hookshot tools were not found next to this app.")
        return
    ui.update_build(tools, folder)
