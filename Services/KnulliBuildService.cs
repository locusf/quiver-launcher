using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using QuiverLauncher.Core.Models;
using QuiverLauncher.Models;

namespace QuiverLauncher.Services;

public sealed record KnulliBuildRecipe(string Id, string Repository, string SourceRef)
{
    public string Version => $"knulli-{Id}-{SourceRef}";
    public GitHubRelease Release => new() { tag_name = Version, assets = [] };
}

public sealed record KnulliBuildConfiguration(string Repository, string Ref, KnulliBuildRecipe[] Recipes)
{
    public void Validate()
    {
        if (!Regex.IsMatch(Repository ?? "", @"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\z") ||
            string.IsNullOrWhiteSpace(Ref) || Recipes is null)
            throw new InvalidDataException("Invalid Knulli build repository, ref, or recipes.");
        foreach (var recipe in Recipes)
        {
            if (recipe.Id != "2048" || recipe.Repository != "libretro/libretro-2048" ||
                !Regex.IsMatch(recipe.SourceRef ?? "", @"\A[0-9a-f]{40}\z"))
                throw new InvalidDataException("Knulli builds require a supported recipe and a pinned source commit.");
        }
    }
}

public sealed record KnulliBuildArtifact(string Url, string Sha256);

public sealed class KnulliBuildService(HttpClient httpClient, KnulliBuildConfiguration configuration,
    string token, string stateDirectory)
{
    private static readonly SemaphoreSlim BuildLock = new(1, 1);
    private sealed record PendingBuild(string RequestId, DateTimeOffset CreatedAt);

    public static (KnulliBuildService Service, KnulliBuildRecipe Recipe)? ForGame(
        GameInfo game, HttpClient httpClient, AppSettings settings)
    {
        if (!KnulliRuntime.IsEnabled || game.EffectiveRepositorySource != "github")
            return null;
        var path = Path.Combine(QuiverLauncherPaths.UserDataRoot, "knulli-builds.json");
        if (!File.Exists(path))
            return null;
        var config = JsonSerializer.Deserialize<KnulliBuildConfiguration>(File.ReadAllText(path))
            ?? throw new InvalidDataException("Knulli build configuration is empty.");
        config.Validate();
        var recipe = config.Recipes.SingleOrDefault(r =>
            string.Equals(r.Repository, game.Repository, StringComparison.OrdinalIgnoreCase));
        if (recipe is null)
            return null;
        var tokenPath = Path.Combine(QuiverLauncherPaths.UserDataRoot, "github-token");
        var token = Environment.GetEnvironmentVariable("QUIVER_GITHUB_TOKEN") ?? settings.GitHubApiToken;
        if (string.IsNullOrWhiteSpace(token) && File.Exists(tokenPath))
            token = File.ReadAllText(tokenPath).Trim();
        return (new KnulliBuildService(httpClient, config, token ?? "",
            Path.Combine(QuiverLauncherPaths.UserDataRoot, "Builds")), recipe);
    }

    public async Task<KnulliBuildArtifact> BuildAsync(KnulliBuildRecipe recipe,
        CancellationToken cancellationToken = default)
    {
        configuration.Validate();
        if (!configuration.Recipes.Contains(recipe))
            throw new InvalidOperationException("The requested recipe is not configured.");
        if (string.IsNullOrWhiteSpace(token))
            throw new InvalidOperationException(
                "GitHub builds need an Actions read/write token for the build repository. " +
                "Configure the GitHub API token in Settings, or the private github-token file.");

        await BuildLock.WaitAsync(cancellationToken);
        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromMinutes(45));
            var ct = timeout.Token;
            Directory.CreateDirectory(stateDirectory);
            var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
                $"{configuration.Repository}\n{configuration.Ref}\n{recipe.Id}\n{recipe.SourceRef}")));
            var statePath = Path.Combine(stateDirectory, key + ".json");
            PendingBuild pending;
            if (File.Exists(statePath))
                pending = JsonSerializer.Deserialize<PendingBuild>(await File.ReadAllTextAsync(statePath, ct))
                    ?? throw new InvalidDataException("Invalid saved GitHub build request.");
            else
            {
                pending = new PendingBuild(Guid.NewGuid().ToString("N"), DateTimeOffset.UtcNow);
                using var request = CreateRequest(HttpMethod.Post, "actions/workflows/knulli.yml/dispatches");
                request.Content = JsonContent.Create(new
                {
                    @ref = configuration.Ref,
                    inputs = new { recipe = recipe.Id, source_ref = recipe.SourceRef, request_id = pending.RequestId }
                });
                using var response = await httpClient.SendAsync(request, ct);
                response.EnsureSuccessStatusCode();
                await File.WriteAllTextAsync(statePath + ".tmp", JsonSerializer.Serialize(pending), ct);
                File.Move(statePath + ".tmp", statePath, overwrite: true);
            }

            long? runId = null;
            while (true)
            {
                ct.ThrowIfCancellationRequested();
                JsonElement? run = null;
                if (runId.HasValue)
                    run = await GetAsync($"actions/runs/{runId.Value}", ct);
                else
                {
                    var since = Uri.EscapeDataString(pending.CreatedAt.AddMinutes(-1).ToString("O"));
                    for (var page = 1; ; page++)
                    {
                        var response = await GetAsync(
                            $"actions/workflows/knulli.yml/runs?event=workflow_dispatch&created=%3E%3D{since}&per_page=100&page={page}", ct);
                        var runs = response.GetProperty("workflow_runs");
                        foreach (var candidate in runs.EnumerateArray())
                        {
                            if (candidate.GetProperty("display_title").GetString() == $"knulli-{pending.RequestId}")
                            {
                                run = candidate;
                                runId = candidate.GetProperty("id").GetInt64();
                                break;
                            }
                        }
                        if (run.HasValue || runs.GetArrayLength() < 100)
                            break;
                    }
                }

                if (run is { } current && current.GetProperty("status").GetString() == "completed")
                {
                    var conclusion = current.GetProperty("conclusion").GetString();
                    if (conclusion != "success")
                    {
                        File.Delete(statePath);
                        throw new InvalidOperationException(
                            $"GitHub build {runId} ended with {conclusion}. See https://github.com/{configuration.Repository}/actions/runs/{runId}");
                    }
                    var artifacts = await GetAsync($"actions/runs/{runId}/artifacts?per_page=100", ct);
                    var expectedName = $"knulli-{recipe.Id}-{pending.RequestId}";
                    var matches = artifacts.GetProperty("artifacts").EnumerateArray()
                        .Where(a => a.GetProperty("name").GetString() == expectedName).ToArray();
                    if (matches.Length != 1 || matches[0].GetProperty("expired").GetBoolean())
                    {
                        File.Delete(statePath);
                        throw new InvalidOperationException("The GitHub build artifact is missing or expired. Retry to build again.");
                    }
                    var artifact = matches[0];
                    var digest = artifact.GetProperty("digest").GetString() ?? "";
                    if (!Regex.IsMatch(digest, @"\Asha256:[0-9a-f]{64}\z"))
                        throw new InvalidDataException("GitHub did not provide a valid SHA-256 artifact digest.");
                    return new KnulliBuildArtifact(
                        $"https://api.github.com/repos/{configuration.Repository}/actions/artifacts/{artifact.GetProperty("id").GetInt64()}/zip",
                        digest["sha256:".Length..]);
                }
                if (!runId.HasValue && DateTimeOffset.UtcNow - pending.CreatedAt > TimeSpan.FromMinutes(5))
                {
                    File.Delete(statePath);
                    throw new TimeoutException("GitHub did not register the build within five minutes. Check Actions and retry.");
                }
                await Task.Delay(TimeSpan.FromSeconds(5), ct);
            }
        }
        finally
        {
            BuildLock.Release();
        }
    }

    public void AuthenticateArtifactRequest(HttpRequestMessage request)
    {
        var prefix = $"https://api.github.com/repos/{configuration.Repository}/actions/artifacts/";
        if (request.RequestUri is null || !request.RequestUri.AbsoluteUri.StartsWith(prefix, StringComparison.Ordinal))
            throw new InvalidOperationException("Refusing to send build credentials to a non-artifact URL.");
        Authenticate(request);
    }

    private HttpRequestMessage CreateRequest(HttpMethod method, string path)
    {
        var request = new HttpRequestMessage(method, $"https://api.github.com/repos/{configuration.Repository}/{path}");
        Authenticate(request);
        return request;
    }

    private void Authenticate(HttpRequestMessage request)
    {
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        request.Headers.UserAgent.ParseAdd("QuiverLauncher-Knulli/1.0");
        request.Headers.Add("X-GitHub-Api-Version", "2022-11-28");
    }

    private async Task<JsonElement> GetAsync(string path, CancellationToken ct)
    {
        using var request = CreateRequest(HttpMethod.Get, path);
        using var response = await httpClient.SendAsync(request, ct);
        response.EnsureSuccessStatusCode();
        using var json = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync(ct), cancellationToken: ct);
        return json.RootElement.Clone();
    }
}
