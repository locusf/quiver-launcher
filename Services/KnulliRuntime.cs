using System.Diagnostics;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Threading;

namespace QuiverLauncher.Services;

public static class KnulliRuntime
{
    public static bool IsEnabled => OperatingSystem.IsLinux() &&
        Environment.GetEnvironmentVariable("QUIVER_KNULLI") == "1";

    public const int LaunchExitCode = 75;

    public sealed record LaunchRequest(string FileName, string WorkingDirectory,
        string[] Arguments, Dictionary<string, string?> Environment);

    public static async Task HandOffAsync(ProcessStartInfo startInfo)
    {
        if (startInfo.UseShellExecute || startInfo.Arguments.Length != 0)
            throw new InvalidOperationException("Knulli requires a direct executable and an argument list.");

        var request = new LaunchRequest(startInfo.FileName, startInfo.WorkingDirectory,
            startInfo.ArgumentList.ToArray(), new Dictionary<string, string?>(startInfo.Environment));
        var path = Path.Combine(QuiverLauncherPaths.UserDataRoot, "launch-request.json");
        await File.WriteAllTextAsync(path + ".tmp", JsonSerializer.Serialize(request));
        File.Move(path + ".tmp", path, overwrite: true);
        await ExitAsync(LaunchExitCode);
    }

    public static async Task ExitAsync(int exitCode = 0)
    {
        await Dispatcher.UIThread.InvokeAsync(async () =>
        {
            if (Application.Current?.ApplicationLifetime is not IControlledApplicationLifetime lifetime)
                throw new InvalidOperationException("No controlled Knulli application lifetime is active.");
            if (App.TryGetHostedMainView() is { } view)
                await view.ShutdownAsync();
            lifetime.Shutdown(exitCode);
        });
    }
}
