"""Game-free checks that Hookshot's Windows files are made on the Linux runner (docs/forgejo-runners.md: there is no Windows
one): no workflow asks for a Windows runner, which would queue forever and hold a release; the Linux llvm-mingw that compiles
them is tools.lock's release; and the Windows builder's job publishes for win-x64 and checks what it made.

Run with: python -I tests/test_windows_on_linux.py
"""
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".forgejo" / "workflows"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class WindowsOnLinuxTests(unittest.TestCase):
    def test_no_windows_runner(self):
        for wf in sorted(WORKFLOWS.glob("*.yml")):
            text = wf.read_text(encoding="utf-8")
            with self.subTest(wf.name):
                self.assertIsNone(re.search(r"runs-on:.*windows", text, re.I))
                # (a matrix's os, which runs-on names through ${{ matrix.os }})
                self.assertIsNone(re.search(r"^\s*-?\s*os:\s*windows", text, re.I | re.M))

    def test_linux_llvm_mingw_is_tools_lock_release(self):
        package = module("builder_package_under_test", ROOT / "scripts" / "builder_package.py")
        tools = module("tools_under_test", ROOT / "scripts" / "tools.py")
        windows = tools.named(tools.load(), "llvm-mingw")
        version, prefix, sha256, size, url = package.LINUX_LLVM_MINGW
        self.assertEqual(version, windows.version)
        self.assertEqual(prefix, "llvm-mingw-%s-ucrt-ubuntu-22.04-x86_64" % version)
        self.assertRegex(sha256, r"^[0-9a-f]{64}$")
        self.assertGreater(size, 0)
        self.assertEqual(url, "https://github.com/mstorsjo/llvm-mingw/releases/download/%s/%s.tar.xz" % (version, prefix))

    def test_builder_job(self):
        wf = (WORKFLOWS / "build-windows.yml").read_text(encoding="utf-8")
        self.assertIn("--runtime win-x64 -p:EnableWindowsTargeting=true", wf)
        self.assertIn("windres", wf)
        self.assertIn("scripts/check_windows_files.py --objdump", wf)
        self.assertRegex(wf, r"check_windows_files\.py \\\n\s+--wine ")
        for name in ("build-windows.yml", "vulkan-dlss.yml"):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertNotIn("NUMBER_OF_PROCESSORS", text)   # (Windows' own variable)
                for line in re.findall(r".*builder_package\.py llvm-mingw.*", text):
                    self.assertIn("--host linux", line)

    def test_release_stays_self_contained(self):
        # (dotnet publish -c Release: self-contained win-x64, a folder, as the Windows build made it)
        csproj = (ROOT / "builder" / "HookshotBuilder" / "HookshotBuilder.csproj").read_text(encoding="utf-8")
        self.assertIn("<RuntimeIdentifier>win-x64</RuntimeIdentifier>", csproj)
        self.assertIn("<SelfContained>true</SelfContained>", csproj)
        self.assertIn("<PublishSingleFile>false</PublishSingleFile>", csproj)


if __name__ == "__main__":
    unittest.main()
