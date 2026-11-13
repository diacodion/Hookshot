"""Hookshot Builder, in Python: the same window as the WPF builder (builder/HookshotBuilder), run by python.org's own python.exe and
Qt's DLLs (both signed by their makers), with no executable of Hookshot's own to download. It also does what the setup exe did
(builder/setup/setup.c): it reads a hookshot-setup.txt, downloads the parts it names, checks each one's SHA-256 and unpacks them
into %LOCALAPPDATA%\\HookshotBuilder\\versions\\<tag>. It looks for no release itself: the Hookshot Launcher installs and updates
the builder (docs/releases.md)."""
APP_ID = "MagicBean.Hookshot.Builder"   # (Windows' AppUserModelID: the taskbar groups and shows the window as Hookshot, not Python)
# (where players get the launcher, which installs and updates the builder)
LAUNCHER_RELEASES = "https://git.notrexed.dev/Hookshot/HookshotLauncher/releases"
