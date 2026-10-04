using System.Net;
using System.Net.Http.Json;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using FluentAssertions;
using QuiverLauncher.Core.Models;
using QuiverLauncher.Models;
using QuiverLauncher.Services;

namespace QuiverLauncher.Tests;

public sealed class KnulliBuildTests : IDisposable
{
    private readonly string _state = Path.Combine(Path.GetTempPath(), "QuiverKnulliTests", Guid.NewGuid().ToString("N"));
    private static readonly KnulliBuildRecipe Recipe = new("2048", "libretro/libretro-2048", new string('a', 40));
    private static readonly KnulliBuildConfiguration Configuration = new("owner/builds", "build-branch", [Recipe]);

    [Theory]
    [InlineData("game-knulli-arm64.zip", true)]
    [InlineData("game-linux-aarch64.tar.gz", true)]
    [InlineData("game-linux-x64.tar.gz", false)]
    [InlineData("game-linux-arm64.AppImage", false)]
    [InlineData("game-linux-arm64.flatpak", false)]
    [InlineData("game-linux.tar.gz", false)]
    [InlineData("game-Windows.zip", false)]
    [InlineData("game-PortMaster.zip", false)]
    [InlineData("game-linux-arm64-sources.zip", false)]
    public void Only_explicit_native_arm64_archives_are_eligible(string name, bool expected) =>
        KnulliAssetPolicy.IsCompatible(name).Should().Be(expected);

    [Fact]
    public async Task Generic_attempt_resolves_source_commit_before_dispatch()
    {
        var handler = new BuildHandler { RecipeId = "auto" };
        using var client = new HttpClient(handler);
        var service = new KnulliBuildService(client,
            Configuration with { AttemptUnconfiguredGames = true }, "test-token", _state);
        var artifact = await service.BuildAsync(new KnulliBuildRecipe("auto", "owner/game", "v1.2"));
        artifact.SourceRef.Should().Be(new string('c', 40));
        handler.SourceRef.Should().Be(artifact.SourceRef);
        handler.SourceRepository.Should().Be("owner/game");
    }

    [Fact]
    public async Task Generic_attempt_is_opt_in()
    {
        using var client = new HttpClient(new BuildHandler());
        var service = new KnulliBuildService(client, Configuration, "test-token", _state);
        await service.Invoking(s => s.BuildAsync(new KnulliBuildRecipe("auto", "owner/game", "HEAD")))
            .Should().ThrowAsync<InvalidOperationException>().WithMessage("*not configured*");
    }

    [Fact]
    public async Task Artifact_redirect_does_not_forward_github_credentials()
    {
        var handler = new RedirectHandler();
        using var client = new HttpClient(handler);
        var service = new KnulliBuildService(client, Configuration, "test-token", _state);
        using var response = await service.DownloadArtifactAsync(
            new KnulliBuildArtifact("https://api.github.com/repos/owner/builds/actions/artifacts/42/zip", "", ""),
            TestContext.Current.CancellationToken);
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        handler.Downloaded.Should().BeTrue();
    }

    [Fact]
    public async Task Verified_build_uses_normal_installer_and_records_pinned_version()
    {
        await VerifyInstallationAsync(corruptDigest: false);
    }

    [Fact]
    public async Task Corrupt_build_never_reaches_the_installer()
    {
        await VerifyInstallationAsync(corruptDigest: true);
    }

    private async Task VerifyInstallationAsync(bool corruptDigest)
    {
        if (!OperatingSystem.IsLinux())
            return;
        var previousRoot = QuiverLauncherPaths.OverrideUserDataRoot;
        var previousMode = Environment.GetEnvironmentVariable("QUIVER_KNULLI");
        try
        {
            QuiverLauncherPaths.OverrideUserDataRoot = _state;
            Environment.SetEnvironmentVariable("QUIVER_KNULLI", "1");
            Directory.CreateDirectory(_state);
            await File.WriteAllTextAsync(Path.Combine(_state, "knulli-builds.json"), JsonSerializer.Serialize(Configuration));
            using var archiveBytes = new MemoryStream();
            using (var zip = new ZipArchive(archiveBytes, ZipArchiveMode.Create, leaveOpen: true))
            {
                await using var writer = new StreamWriter(zip.CreateEntry("launch.sh").Open());
                await writer.WriteAsync("#!/bin/sh\nexit 0\n");
            }
            var payload = archiveBytes.ToArray();
            using var client = new HttpClient(new BuildHandler
            {
                Payload = payload,
                Digest = "sha256:" + (corruptDigest ? new string('0', 64) :
                    Convert.ToHexString(SHA256.HashData(payload)).ToLowerInvariant())
            });
            using var manager = new GameManager(httpClient: client);
            var game = new GameInfo { Name = "2048", Repository = Recipe.Repository, FolderName = "2048", GameManager = manager };
            var dialogs = new TestDialogs();
            await GameDownloadInstallService.DownloadAndInstallAsync(game, client,
                Path.Combine(_state, "Apps"), Recipe.Release,
                new AppSettings { GitHubApiToken = "test-token" }, GameStatus.NotInstalled, dialogs)
                .WaitAsync(TimeSpan.FromSeconds(10));
            var versionPath = Path.Combine(_state, "Apps", "2048", "version.txt");
            if (corruptDigest)
            {
                dialogs.Error.Should().Contain("SHA-256");
                File.Exists(versionPath).Should().BeFalse();
                game.Status.Should().Be(GameStatus.NotInstalled);
            }
            else
            {
                dialogs.Error.Should().BeNull();
                (await File.ReadAllTextAsync(versionPath)).Trim().Should().Be(Recipe.Version);
                game.Status.Should().Be(GameStatus.Installed);
            }
        }
        finally
        {
            QuiverLauncherPaths.OverrideUserDataRoot = previousRoot;
            Environment.SetEnvironmentVariable("QUIVER_KNULLI", previousMode);
        }
    }

    [Fact]
    public void Handoff_preserves_argument_boundaries_without_build_credentials()
    {
        var start = new System.Diagnostics.ProcessStartInfo("/games/My Game/launch.sh")
        {
            UseShellExecute = false,
            WorkingDirectory = "/games/My Game"
        };
        start.ArgumentList.Add("one argument");
        start.Environment["QUIVER_GITHUB_TOKEN"] = "private-token";
        start.Environment["QUIVER_KNULLI"] = "1";
        var request = KnulliRuntime.CreateLaunchRequest(start);
        request.Arguments.Should().Equal("one argument");
        request.Environment.Should().NotContainKey("QUIVER_GITHUB_TOKEN");
        request.Environment.Should().NotContainKey("QUIVER_KNULLI");
    }

    [Fact]
    public async Task Dispatch_is_correlated_and_successful_artifact_is_reused()
    {
        var handler = new BuildHandler();
        using var client = new HttpClient(handler);
        var service = new KnulliBuildService(client, Configuration, "test-token", _state);

        var artifact = await service.BuildAsync(Recipe);
        artifact.Url.Should().Be("https://api.github.com/repos/owner/builds/actions/artifacts/42/zip");
        artifact.Sha256.Should().Be(new string('b', 64));
        (await service.BuildAsync(Recipe)).Should().Be(artifact);
        handler.Dispatches.Should().Be(1);
        handler.DispatchedRef.Should().Be("build-branch");
        handler.SourceRef.Should().Be(Recipe.SourceRef);
        handler.AuthorizedRequests.Should().Be(5);
    }

    [Theory]
    [InlineData("failure")]
    [InlineData("cancelled")]
    [InlineData("timed_out")]
    public async Task Unsuccessful_builds_are_reported_and_retryable(string conclusion)
    {
        using var client = new HttpClient(new BuildHandler { Conclusion = conclusion });
        var service = new KnulliBuildService(client, Configuration, "test-token", _state);
        await service.Invoking(s => s.BuildAsync(Recipe)).Should().ThrowAsync<InvalidOperationException>()
            .WithMessage($"*{conclusion}*");
        Directory.GetFiles(_state).Should().BeEmpty();
    }

    [Fact]
    public async Task Packaging_revision_invalidates_cached_artifacts()
    {
        var handler = new BuildHandler();
        using var client = new HttpClient(handler);
        await new KnulliBuildService(client, Configuration, "test-token", _state).BuildAsync(Recipe);
        await new KnulliBuildService(client, Configuration with { BuildRevision = 2 }, "test-token", _state)
            .BuildAsync(Recipe);
        handler.Dispatches.Should().Be(2);
    }

    [Fact]
    public async Task Expired_artifact_is_not_installed()
    {
        using var client = new HttpClient(new BuildHandler { Expired = true });
        var service = new KnulliBuildService(client, Configuration, "test-token", _state);
        await service.Invoking(s => s.BuildAsync(Recipe)).Should().ThrowAsync<InvalidOperationException>()
            .WithMessage("*expired*");
        Directory.GetFiles(_state).Should().BeEmpty();
    }

    [Fact]
    public async Task Missing_digest_is_not_accepted()
    {
        using var client = new HttpClient(new BuildHandler { Digest = null });
        var service = new KnulliBuildService(client, Configuration, "test-token", _state);
        await service.Invoking(s => s.BuildAsync(Recipe)).Should().ThrowAsync<InvalidDataException>()
            .WithMessage("*digest*");
    }

    [Fact]
    public async Task Missing_token_does_not_dispatch()
    {
        var handler = new BuildHandler();
        using var client = new HttpClient(handler);
        var service = new KnulliBuildService(client, Configuration, "", _state);
        await service.Invoking(s => s.BuildAsync(Recipe)).Should().ThrowAsync<InvalidOperationException>()
            .WithMessage("*token*");
        handler.Dispatches.Should().Be(0);
    }

    [Fact]
    public async Task Cancellation_does_not_dispatch()
    {
        var handler = new BuildHandler();
        using var client = new HttpClient(handler);
        var service = new KnulliBuildService(client, Configuration, "test-token", _state);
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();
        await service.Invoking(s => s.BuildAsync(Recipe, cancellation.Token)).Should().ThrowAsync<OperationCanceledException>();
        handler.Dispatches.Should().Be(0);
    }

    [Fact]
    public async Task Dispatch_authentication_failure_is_not_saved_as_success()
    {
        var handler = new BuildHandler { DispatchStatus = HttpStatusCode.Forbidden };
        using var client = new HttpClient(handler);
        var service = new KnulliBuildService(client, Configuration, "test-token", _state);
        await service.Invoking(s => s.BuildAsync(Recipe)).Should().ThrowAsync<HttpRequestException>();
        Directory.GetFiles(_state).Should().BeEmpty();
    }

    [Theory]
    [InlineData("https://example.com/archive.zip")]
    [InlineData("https://api.github.com/repos/other/builds/actions/artifacts/42/zip")]
    [InlineData("https://api.github.com.evil.example/repos/owner/builds/actions/artifacts/42/zip")]
    public void Artifact_credentials_are_restricted_to_configured_repository(string url)
    {
        using var client = new HttpClient();
        var service = new KnulliBuildService(client, Configuration, "test-token", _state);
        using var request = new HttpRequestMessage(HttpMethod.Get, url);
        service.Invoking(s => s.AuthenticateArtifactRequest(request)).Should().Throw<InvalidOperationException>();
        request.Headers.Authorization.Should().BeNull();
    }

    [Theory]
    [InlineData("main")]
    [InlineData("$(touch /tmp/injected)")]
    [InlineData("../main")]
    public void Recipe_source_must_be_a_full_commit(string sourceRef)
    {
        var config = Configuration with { Recipes = [Recipe with { SourceRef = sourceRef }] };
        config.Invoking(c => c.Validate()).Should().Throw<InvalidDataException>();
    }

    public void Dispose()
    {
        if (Directory.Exists(_state))
            Directory.Delete(_state, recursive: true);
    }

    private sealed class BuildHandler : HttpMessageHandler
    {
        public int Dispatches { get; private set; }
        public int AuthorizedRequests { get; private set; }
        public string? DispatchedRef { get; private set; }
        public string? SourceRef { get; private set; }
        public string? SourceRepository { get; private set; }
        public string RecipeId { get; init; } = "2048";
        public string Conclusion { get; init; } = "success";
        public bool Expired { get; init; }
        public string? Digest { get; init; } = "sha256:" + new string('b', 64);
        public HttpStatusCode DispatchStatus { get; init; } = HttpStatusCode.NoContent;
        public byte[] Payload { get; init; } = [];
        private string? _requestId;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            request.Headers.Authorization?.ToString().Should().Be("Bearer test-token");
            AuthorizedRequests++;
            var path = request.RequestUri!.AbsolutePath;
            if (path == "/repos/owner/game/commits/v1.2")
                return Json(new { sha = new string('c', 40) });
            if (request.Method == HttpMethod.Post)
            {
                path.Should().EndWith("/actions/workflows/knulli.yml/dispatches");
                var body = await request.Content!.ReadFromJsonAsync<JsonElement>(ct);
                DispatchedRef = body.GetProperty("ref").GetString();
                SourceRef = body.GetProperty("inputs").GetProperty("source_ref").GetString();
                SourceRepository = body.GetProperty("inputs").GetProperty("source_repository").GetString();
                _requestId = body.GetProperty("inputs").GetProperty("request_id").GetString();
                Dispatches++;
                return new HttpResponseMessage(DispatchStatus);
            }
            if (path.EndsWith("/runs"))
            {
                return Json(new
                {
                    workflow_runs = new[]
                    {
                        new { id = 900, display_title = "knulli-unrelated", status = "completed", conclusion = "success" },
                        new { id = 17, display_title = $"knulli-{_requestId}", status = "completed", conclusion = Conclusion }
                    }
                });
            }
            if (path.EndsWith("/actions/artifacts/42/zip"))
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(Payload) };
            path.Should().EndWith("/actions/runs/17/artifacts");
            return Json(new
            {
                artifacts = new[]
                {
                    new { id = 42, name = $"knulli-{RecipeId}-{_requestId}", expired = Expired, digest = Digest }
                }
            });
        }

        private static HttpResponseMessage Json(object value) => new(HttpStatusCode.OK)
        {
            Content = JsonContent.Create(value)
        };
    }

    private sealed class RedirectHandler : HttpMessageHandler
    {
        public bool Downloaded { get; private set; }
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            if (request.RequestUri!.Host == "api.github.com")
            {
                request.Headers.Authorization?.Parameter.Should().Be("test-token");
                var response = new HttpResponseMessage(HttpStatusCode.Found);
                response.Headers.Location = new Uri("https://build.blob.core.windows.net/artifact.zip?signature=test");
                return Task.FromResult(response);
            }
            request.Headers.Authorization.Should().BeNull();
            Downloaded = true;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK));
        }
    }

    private sealed class TestDialogs : IGameDownloadDialogs
    {
        public string? Error { get; private set; }
        public Task ShowErrorAsync(string message, string title) { Error = message; return Task.CompletedTask; }
        public Task<bool> ConfirmDownloadWithoutRunnerAsync() => throw new NotSupportedException();
        public Task<LinuxWindowsRunnerConfig?> ConfigureWindowsRunnerAsync(string gamePath,
            LinuxWindowsRunnerConfig? existing = null, bool isInstall = true) => throw new NotSupportedException();
        public Task ShowRateLimitExceededAsync() => throw new NotSupportedException();
        public Task ShowGitLabRateLimitExceededAsync() => throw new NotSupportedException();
    }
}
