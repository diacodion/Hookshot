"""Back from an update that stopped, the builder's first page offers the update again.

The button of the first page is left alone while an update runs (RefreshMode / refresh_mode return at once). An update that
stopped, or was cancelled, never let that go: back on the first page the button stayed what it was before the update. After a
start with --update (the game's update offer, or the setup once the new builder is in) the page had never been set up at
all, so it showed a grey "Build the game" with no game files, and the Update button only came back when the app was closed
and opened again (#2129).

The WPF builder's MainWindow.xaml.cs is read.

python -I -B tests/test_builder_update_back.py
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "builder" / "HookshotBuilder" / "MainWindow.xaml.cs").read_text(encoding="utf-8")


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


class WindowsBuilder(unittest.TestCase):
    def test_back_and_cancel_go_through_one_function(self):
        self.assertIn("void Back(object sender, RoutedEventArgs e) => ToFiles();", MAIN)
        ended = block(MAIN, "void Ended(string? failure)")
        cancelled = ended[ended.index('if (failure == "cancelled")'):]
        self.assertIn("ToFiles();", cancelled[:cancelled.index("return;")])
        self.assertEqual(MAIN.count("ShowPage(FilesPage);"), 1)   # (no other way back to it)

    def test_it_lets_the_button_go_and_sets_the_page_up(self):
        to_files = block(MAIN, "async void ToFiles()")
        self.assertIn("if (BuildButton == null || updating || runner != null) return;", block(MAIN, "void RefreshMode()"))
        order = ["ShowPage(FilesPage);", "if (!updating) return;", "updating = false;", "if (!filesReady)", "filesReady = true;",
                 "var folder = OutPath.Text;", "Finder.FindDumps", "ApplySettings();", "OutPath.Text = folder;", "FillDumps();",
                 "CheckAll();", "Refresh();"]
        at = [to_files.index(part) for part in order]
        self.assertEqual(at, sorted(at))
        start = block(MAIN, "async void Start()")
        self.assertLess(start.index("FillDumps();"), start.index("filesReady = true;"))
        self.assertLess(start.index("filesReady = true;"), start.index("ShowMain();", start.index("FillDumps();")))


if __name__ == "__main__":
    unittest.main()
