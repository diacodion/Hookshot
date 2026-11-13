"""The versions the Linux builder's buttons go by: the version these tools are, the one a game folder was built with, and
their order. The builder no longer installs or looks for versions: the Hookshot Launcher (MagicBeanDev/LongshotLauncher)
reads Hookshot's signed releases, installs each version's builder and moves it to its channel's newest (docs/releases.md).
What the builder still does with a game folder older than itself is build it again with its own tools (Update:
nx2/make_game.py --update, from the game files the folder keeps).
"""
import os
import re


def data_root():
    base = os.environ.get("HOOKSHOT_BUILDER_HOME") or os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "hookshot", "builder")


def release_key(tag):
    """A version to compare: a release tag (v2.0.5, v2.1.0-rc.1) or git describe's v2.0.5-3-g1a2b3c4[-dirty] (a build from
    the Actions tab or a checkout: after v2.0.5, before any later release). None for a build that names no release: a bare
    commit (digits alone too: no v and no dot), dev, or no version at all. Prereleases in semver order: v2.1.0-rc.1 <
    v2.1.0-rc.2 < v2.1.0."""
    m = re.match(r"^(?=v|\d+\.)v?(\d+(?:\.\d+)*)(?:-(?!\d+-g|dirty$)([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?(-\d+-g[0-9a-f]+)?(-dirty)?$",
                 (tag or "").strip())
    if not m:
        return None
    pre = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in (m.group(2) or "").split(".") if p)
    # (a prerelease before its release; commits after a tag, or changes to it, after that tag and before the next)
    return tuple(int(n) for n in m.group(1).split(".")), m.group(2) is None, pre, bool(m.group(3) or m.group(4))


def older_version(target, have):
    """Known version order only: arbitrary hashes and differing nightlies cannot establish commit ancestry."""
    old, new = release_key(have), release_key(target)
    return bool(old and new and new < old)


def choose_mode(game, tools, source_picked, kept):
    """What the builder's button does: a game older than this builder that still has its files is updated (built again
    with these tools), a game it built, or a newer one, is played, and anything else is built. A version with no known
    order (a bare commit, dev) that differs from these tools is updated too."""
    if kept and game and tools and game != tools and not source_picked and not older_version(tools, game):
        return "update-game"
    if game and not source_picked:
        return "up-to-date"
    return "build"


def tools_tag(root):
    """The release these tools are, from the file a package writes (VERSION, or version.txt). A source checkout has neither."""
    if not root:
        return ""
    for rel in ("usr/share/hookshot/VERSION", "usr/share/hookshot/version.txt", "VERSION", "version.txt"):
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8") as f:
                line = f.readline().strip()
        except OSError:
            continue
        if line:
            return line
    return ""


def running_root():
    """The AppDir that is running: the AppImage's mount, or an unpacked one (the launcher's copy: its AppRun beside usr/)."""
    appdir = os.environ.get("APPDIR")
    if appdir and os.path.isfile(os.path.join(appdir, "AppRun")):
        return appdir
    tools = os.environ.get("HOOKSHOT_TOOLS")
    if tools:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(tools))))   # (<root>/usr/share/hookshot)
        if os.path.isfile(os.path.join(root, "AppRun")):
            return root
    return None


def game_version(folder):
    """The release a folder was built with, when it still has the game files an update builds from."""
    if not folder:
        return None, False
    kept = os.path.isdir(os.path.join(folder, ".hookshot_exefs")) and os.path.isdir(os.path.join(folder, "romfs"))
    version = None
    try:
        with open(os.path.join(folder, "hookshot_version.txt"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("version="):
                    version = line[8:].strip() or None
                    break
    except OSError:
        pass
    return version, kept


def game_exe(folder):
    """bin/hookshot under the launcher, or hookshot in a folder built before the launcher."""
    if not folder:
        return None
    nested = os.path.join(folder, "bin", "hookshot")
    if os.path.isfile(nested) and os.path.isfile(os.path.join(folder, "image.bin")):
        return nested
    flat = os.path.join(folder, "hookshot")
    if os.path.isfile(flat) and os.path.isfile(os.path.join(folder, "image.bin")):
        return flat
    return None
