using System.Text.RegularExpressions;
using QuiverLauncher.Core.Models;
using QuiverLauncher.Core.Services;

namespace QuiverLauncher.Services;

public static class KnulliAssetPolicy
{
    public static DownloadAssetSelection Select(GitHubRelease release, string? filter)
    {
        var assets = GitHubReleaseService.GetDownloadableAssets(release, filter)
            .Where(a => IsCompatible(a.name)).ToArray();
        return new(assets, [], assets.Length == 0
            ? "No native Knulli ARM64 package is available. Enable source-build attempts or configure a build recipe."
            : null);
    }

    public static bool IsCompatible(string name) =>
        Regex.IsMatch(name, @"(?:knulli|linux)", RegexOptions.IgnoreCase) &&
        Regex.IsMatch(name, @"(?:arm64|aarch64)", RegexOptions.IgnoreCase) &&
        !Regex.IsMatch(name, @"(?:x86|x64|amd64|armhf|appimage|flatpak|\.deb$|\.rpm$)", RegexOptions.IgnoreCase) &&
        !DownloadAssetPolicy.IsAuxiliary(name) &&
        Regex.IsMatch(name, @"\.(?:zip|7z|tar\.(?:gz|xz)|tgz)$", RegexOptions.IgnoreCase);
}
