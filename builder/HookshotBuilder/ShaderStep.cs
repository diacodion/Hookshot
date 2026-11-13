using System.Text.RegularExpressions;

namespace HookshotBuilder;

/// The build's last step, the shaders' preparation (nx2/make_game.py prepare_shaders): the argument that leaves it to the game's
/// first start, and what its row says once the build is done
static class ShaderStep
{
    public const string Id = "shaders";
    public const string Later = "--no-prepare-shaders";
    /// "Compile shaders" (make_game.py --compile-shaders, kept in the folder's hookshot_options.ini as compile_shaders=, which the
    /// launcher's setting writes too): All builds the predicted pipelines too, Most (the default) every shader and the pipelines
    /// the lists name
    public const string Compile = "--compile-shaders";
    public static readonly string[] Levels = { "all", "most" };
    public const string DefaultLevel = "most";
    public const string Tip = "All: also builds the predicted pipelines, about 40% longer, for pipelines play almost never uses. Most (recommended): every shader and the pipelines the game is known to use.";
    public static string Level(string? saved) => saved != null && Array.IndexOf(Levels, saved) >= 0 ? saved : DefaultLevel;
    // (make_game.py NOT_PREPARED: a plain line, so a builder that does not know it only shows it)
    static readonly Regex NotPrepared = new(@"^the shaders were not prepared now \((.*)\): ");

    /// make_game's arguments with the player's choice (unticked: the game prepares its shaders at its first start). level: the
    /// Compile shaders shown, for a build (make_game keeps it in the folder); null for an update, which shows none: the folder's
    /// own (the launcher's setting, or the last build's) then stands
    public static List<string> Args(List<string> args, bool prepare, string? level = null)
    {
        if (!prepare) args.Add(Later);
        if (level != null) args.AddRange(new[] { Compile, Level(level) });
        return args;
    }

    /// Why make_game left the shaders to the first start, when this line says so; else null
    public static string? Reason(string line) => NotPrepared.Match(line) is { Success: true } m ? m.Groups[1].Value : null;

    /// The line under the row of a finished build when the shaders were not prepared, or null when they were (a check mark).
    /// began: the game's @@stage shaders came; reason: make_game's not-prepared line
    public static string? Skipped(bool began, string? reason) =>
        reason != null ? $"Left to the game's first start: {reason}"
        : began ? null
        : "Left to the game's first start";
}
