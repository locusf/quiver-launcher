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
    public static bool HasFramebufferHost => IsEnabled &&
        Application.Current?.ApplicationLifetime is ISingleViewApplicationLifetime;

    public const int LaunchExitCode = 75;

    public sealed record LaunchRequest(string FileName, string WorkingDirectory,
        string[] Arguments, Dictionary<string, string?> Environment);

    public static LaunchRequest CreateLaunchRequest(ProcessStartInfo startInfo)
    {
        if (startInfo.UseShellExecute || startInfo.Arguments.Length != 0)
            throw new InvalidOperationException("Knulli requires a direct executable and an argument list.");

        var environment = new Dictionary<string, string?>(startInfo.Environment);
        environment.Remove("QUIVER_GITHUB_TOKEN");
        environment.Remove("QUIVER_KNULLI");
        return new LaunchRequest(startInfo.FileName, startInfo.WorkingDirectory,
            startInfo.ArgumentList.ToArray(), environment);
    }

    public static async Task HandOffAsync(ProcessStartInfo startInfo)
    {
        var request = CreateLaunchRequest(startInfo);
        var path = Path.Combine(QuiverLauncherPaths.UserDataRoot, "launch-request.json");
        await File.WriteAllTextAsync(path + ".tmp", JsonSerializer.Serialize(request));
        File.Move(path + ".tmp", path, overwrite: true);
        // Let the tracked launch operation finish before shutdown drains that operation.
        Dispatcher.UIThread.Post(async () =>
        {
            try { await ExitAsync(LaunchExitCode); }
            catch (Exception ex)
            {
                CrashLog.Log("Knulli game handoff", ex);
                Console.Error.WriteLine(ex);
            }
        });
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
