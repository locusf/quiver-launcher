using System.Diagnostics;
using System.Text.Json;

namespace QuiverLauncher.Services;

public static class KnulliDeviceProfile
{
    public static async Task<string> CollectAsync(CancellationToken cancellationToken)
    {
        var script = Path.Combine(AppContext.BaseDirectory, "device_profile.py");
        if (!File.Exists(script))
            throw new FileNotFoundException("The Knulli device profiler is missing. Update the complete launcher package.", script);
        var start = new ProcessStartInfo("python3")
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        start.ArgumentList.Add(script);
        start.Environment.Remove("QUIVER_GITHUB_TOKEN");
        using var process = Process.Start(start)
            ?? throw new InvalidOperationException("Could not start the Knulli device profiler.");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(15));
        var output = process.StandardOutput.ReadToEndAsync(timeout.Token);
        var error = process.StandardError.ReadToEndAsync(timeout.Token);
        try
        {
            await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
            var errorText = await error;
            var outputText = await output;
            if (process.ExitCode != 0)
                throw new InvalidOperationException($"Knulli hardware detection failed: {errorText}");
            return Validate(outputText);
        }
        finally
        {
            if (!process.HasExited)
                process.Kill(entireProcessTree: true);
        }
    }

    public static string Validate(string json)
    {
        if (json.Length > 12000)
            throw new InvalidDataException("Knulli hardware profile exceeds the dispatch limit.");
        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;
        if (root.GetProperty("schema").GetInt32() != 1 ||
            root.GetProperty("architecture").GetString() != "aarch64" ||
            root.GetProperty("controllers").ValueKind != JsonValueKind.Array ||
            root.GetProperty("display").ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("The agent requires an ARM64 Knulli hardware/input profile.");
        return JsonSerializer.Serialize(root);
    }
}
