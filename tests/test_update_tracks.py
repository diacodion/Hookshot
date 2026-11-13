"""The builders no longer update themselves (docs/releases.md): the Hookshot Launcher reads Hookshot's signed releases, and
installs and updates each version's builder. What is left of the tracks in the builders: which track a version is on and the
version order (builder/linux/linux_update.py, which the Linux builder's buttons use), and that no builder, setup or game asks
a server for a release: no GitHub link, no release lookup, no game update check (host/hos_update.c is gone)."""
import os
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "builder" / "linux"))
import linux_update  # noqa: E402

# (version, track)
TRACKS = [
    ("v2.0.7", "release"), ("v3.1.0", "release"),
    ("v3.1.0-rc.1", "rc"), ("v3.1.0-rc.12", "rc"),
    ("v3.1.0-rc.1-3-g1a2b3c4", "nightly"), ("v3.1.0-rc.1-0-g1a2b3c4", "nightly"), ("v3.0.14-12-gabcdef01", "nightly"),
    ("v3.0.5-3-g1a2b3c4-dirty", "nightly"),
]
# newer, older (semver: a release candidate before its release, rc.N by number)
ORDER = [
    ("v3.1.0-rc.2", "v3.1.0-rc.1"), ("v3.1.0-rc.10", "v3.1.0-rc.9"), ("v3.1.0", "v3.1.0-rc.2"), ("v3.1.0-rc.1", "v3.0.14"),
    ("v3.0.10", "v3.0.9"), ("v3.0.0-rc.1", "v2.9.9"),
]

# (the code that used to ask GitHub for a release, and must not ask any server now)
SOURCES = [
    "builder/linux/linux_update.py", "builder/linux/hookshot_builder.py", "builder/py/hookshot_builder/__init__.py",
    "builder/py/hookshot_builder/updater.py", "builder/py/hookshot_builder/setup_parts.py", "builder/py/hookshot_builder/window.py",
    "builder/HookshotBuilder/Updater.cs", "builder/HookshotBuilder/MainWindow.xaml.cs", "builder/setup/setup.c", "host/hos_main.c",
]


class Tracks(unittest.TestCase):
    def test_the_track_of_a_version(self):
        for version, track in TRACKS + [("nightly", "release"), ("rc", "release"), ("dev", "release")]:
            self.assertEqual(linux_update.track_of(version), track, version)

    def test_semver_order(self):
        for newer, older in ORDER:
            self.assertGreater(linux_update.release_key(newer), linux_update.release_key(older), (newer, older))
        self.assertIsNone(linux_update.release_key("rc"))
        self.assertEqual(linux_update.newest_release(["rc", "nightly", "v3.1.0-rc.2", "v3.1.0-rc.10", "v3.0.14"]), "v3.1.0-rc.10")
        self.assertEqual(linux_update.newest_release(["rc", "nightly"]), None)

    def test_a_builder_offers_only_its_own_version(self):
        # (no newest known, as the builder asks no server: the game is built with these tools, and no builder update)
        self.assertEqual(linux_update.update_target(None, "v3.1.0"), "v3.1.0")
        self.assertEqual(linux_update.choose_mode(None, "v3.1.0", None, False, False), "build")
        self.assertEqual(linux_update.choose_mode("v3.0.0", "v3.1.0", None, False, True), "update-game")
        self.assertEqual(linux_update.choose_mode("v3.1.0", "v3.1.0", None, False, True), "up-to-date")

    def test_no_builder_looks_for_a_release(self):
        for rel in SOURCES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for pattern in (r"api\.github\.com", r"github\.com/[^/\s\"]+/(Hookshot|Hookshot)/releases", r"releases/latest",
                            r"NotRexed[/]Hookshot", r"hos_update_check"):
                self.assertIsNone(re.search(pattern, text), "%s: %s" % (rel, pattern))
        self.assertFalse((ROOT / "host" / "hos_update.c").exists(), "the game's own update check is back")
        for name in ("latest_tag", "list_url", "LATEST_LIST", "LATEST_API", "RELEASES_API"):
            self.assertFalse(hasattr(linux_update, name), name)

    def test_a_list_must_be_given(self):
        old = os.environ.pop("HOOKSHOT_LINUX_LIST", None)
        try:
            with self.assertRaises(RuntimeError) as caught:
                linux_update.fetch_manifest()
            self.assertIn("Hookshot Launcher", str(caught.exception))
        finally:
            if old is not None:
                os.environ["HOOKSHOT_LINUX_LIST"] = old


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
