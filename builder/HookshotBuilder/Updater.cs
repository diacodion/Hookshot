using System.Diagnostics;

namespace HookshotBuilder;

/// The builder no longer updates itself (docs/releases.md): the Hookshot Launcher installs each version's builder from Hookshot's
/// signed releases and moves it to its channel's newest. What is left here: --update &lt;game folder&gt; builds the folder again with
/// this builder's own tools (nx2/make_game.py --update, from the game files the folder keeps), and the way to the launcher.
static class Updater
{
    /// Where players get the launcher, which keeps the builder and the game up to date: the website's download page
    public const string LauncherDownload = "https://hookshot.magicbean.dev/download";

    public static void FromCommandLine(MainWindow w)
    {
        var a = Environment.GetCommandLineArgs();
        var i = Array.IndexOf(a, "--update");
        if (i >= 0 && i + 1 < a.Length) UpdateFolder(w, a[i + 1]);
    }

    /// The game in folder built again with these tools
    public static void UpdateFolder(MainWindow w, string folder)
    {
        var tools = Tools.Find();
        w.BeginUpdate(folder, tools?.Version);
        if (tools == null)
        {
            w.DownloadFailed("The Hookshot tools were not found next to this app.");
            return;
        }
        w.UpdateBuild(tools, folder);
    }

    public static void OpenLauncherDownload() => Process.Start(new ProcessStartInfo(LauncherDownload) { UseShellExecute = true });
}
