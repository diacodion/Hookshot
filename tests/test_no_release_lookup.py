"""The builders no longer update themselves (docs/releases.md): the Hookshot Launcher reads Hookshot's signed releases, and
installs and updates each version's builder. What is left in the builders: the version order the Linux builder's buttons use
(builder/linux/linux_update.py), and that no builder, setup or game asks a server for a release: no GitHub link, no release
lookup, no game update check (host/hos_update.c is gone), and none of the old self-update's hooks."""
import os
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "builder" / "linux"))
import linux_update  # noqa: E402

# newer, older (semver: a release candidate before its release, rc.N by number; a nightly after the tag it describes)
ORDER = [
    ("v3.1.0-rc.2", "v3.1.0-rc.1"), ("v3.1.0-rc.10", "v3.1.0-rc.9"), ("v3.1.0", "v3.1.0-rc.2"), ("v3.1.0-rc.1", "v3.0.14"),
    ("v3.0.10", "v3.0.9"), ("v3.0.0-rc.1", "v2.9.9"), ("v3.0.14-12-gabcdef01", "v3.0.14"), ("v3.1.0-rc.1", "v3.0.14-12-gabcdef01"),
]

# (the code that used to ask GitHub for a release or install a newer builder, and must not now)
SOURCES = [
    "builder/linux/linux_update.py", "builder/linux/hookshot_builder.py", "builder/linux/AppRun", "builder/linux/hookshot-folder",
    "builder/py/hookshot_builder/__init__.py", "builder/py/hookshot_builder/updater.py",
    "builder/py/hookshot_builder/setup_parts.py", "builder/py/hookshot_builder/window.py", "builder/py/hookshot_builder_start.py",
    "builder/HookshotBuilder/Updater.cs", "builder/HookshotBuilder/MainWindow.xaml.cs", "builder/setup/setup.c", "host/hos_main.c",
]


class Versions(unittest.TestCase):
    def test_semver_order(self):
        for newer, older in ORDER:
            self.assertGreater(linux_update.release_key(newer), linux_update.release_key(older), (newer, older))
            self.assertTrue(linux_update.older_version(older, newer), (newer, older))
        for unordered in ("rc", "nightly", "dev", "1a2b3c4", ""):
            self.assertIsNone(linux_update.release_key(unordered), unordered)
            self.assertFalse(linux_update.older_version(unordered, "v3.0.0"))

    def test_a_builder_offers_only_its_own_version(self):
        # (no newest known, as the builder asks no server: the game is built with these tools, and no builder update)
        self.assertEqual(linux_update.choose_mode(None, "v3.1.0", False, False), "build")
        self.assertEqual(linux_update.choose_mode("v3.0.0", "v3.1.0", False, True), "update-game")
        self.assertEqual(linux_update.choose_mode("v3.1.0", "v3.1.0", False, True), "up-to-date")


class NoLookup(unittest.TestCase):
    def test_no_builder_looks_for_a_release(self):
        for rel in SOURCES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for pattern in (r"api\.github\.com", r"github\.com/[^/\s\"]+/(Hookshot|Hookshot)/releases", r"releases/latest",
                            r"NotRexed[/]Hookshot", r"hos_update_check", r"HOOKSHOT_LATEST_TAG", r"HOOKSHOT_LINUX_LIST",
                            r"latest_tag", r"update.builder", r"--fetched", r"run_latest_setup"):
                self.assertIsNone(re.search(pattern, text), "%s: %s" % (rel, pattern))
        self.assertFalse((ROOT / "host" / "hos_update.c").exists(), "the game's own update check is back")
        for name in ("install", "fetch_manifest", "startup_manifest", "parse_list", "update_target", "track_of", "TRACKS"):
            self.assertFalse(hasattr(linux_update, name), name)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
