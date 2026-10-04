using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace QuiverLauncher.Services;

public sealed record KnulliLicenseAcceptance(
    [property: JsonPropertyName("schema")] int Schema,
    [property: JsonPropertyName("repository")] string Repository,
    [property: JsonPropertyName("source_ref")] string SourceRef,
    [property: JsonPropertyName("license_path")] string LicensePath,
    [property: JsonPropertyName("license_sha256")] string LicenseSha256,
    [property: JsonPropertyName("accept_terms")] bool AcceptTerms,
    [property: JsonPropertyName("accept_public_fork_and_artifacts")] bool AcceptPublicForkAndArtifacts);

public sealed record KnulliLicenseReview(string Repository, string SourceRef, string LicensePath,
    string LicenseName, string LicenseText, string LicenseSha256)
{
    public string LicenseUrl => $"https://github.com/{Repository}/blob/{SourceRef}/" +
        string.Join("/", LicensePath.Split('/').Select(Uri.EscapeDataString));

    public string ReviewMessage =>
        $"Repository: {Repository}\nSource: {SourceRef}\nLicense: {LicenseName}\n{LicenseUrl}\n\n" +
        "This license was not automatically recognized as an approved open-source license. " +
        "Read all terms below. D-pad Up/Down scrolls; Left/Right selects a button. " +
        "Continue only if you can comply. Missing rights cannot be granted by this dialog.\n\n" +
        LicenseText;

    public string AcceptanceMessage =>
        $"Build {Repository} at {SourceRef[..12]}?\n\n" +
        "By choosing Accept & build, I confirm:\n\n" +
        "- I have read and accept the displayed license terms, including any noncommercial restrictions.\n" +
        "- My intended use is permitted, and I have the rights required to create a PUBLIC fork, " +
        "push modified source, and publish downloadable GitHub build artifacts.\n" +
        "- I will preserve license/copyright notices and will not bundle commercial game data without permission.\n\n" +
        "Acceptance does not override restrictions or establish legal permission. If you are unsure, cancel.\n\n" +
        "This approval applies only to this repository, source commit and exact license text for this build request.";

    public KnulliLicenseAcceptance Accept() =>
        new(1, Repository, SourceRef, LicensePath, LicenseSha256, true, true);
}

public sealed class KnulliLicenseDeclinedException() : OperationCanceledException("The license review was declined.");

public static class KnulliBuildLicense
{
    private static readonly HashSet<string> Recognized = new(StringComparer.Ordinal)
    {
        "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "GPL-2.0", "GPL-3.0",
        "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
        "LGPL-2.1", "LGPL-3.0", "AGPL-3.0", "MPL-2.0", "ISC", "Zlib",
        "Unlicense", "CC0-1.0", "BSL-1.0"
    };

    public static async Task<KnulliLicenseReview?> InspectAsync(HttpClient client,
        Action<HttpRequestMessage> authenticate, string repository, string sourceRef, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get,
            $"https://api.github.com/repos/{repository}/license?ref={Uri.EscapeDataString(sourceRef)}");
        authenticate(request);
        using var response = await client.SendAsync(request, ct).ConfigureAwait(false);
        if (response.StatusCode == HttpStatusCode.NotFound)
            throw new InvalidOperationException(
                "No license file was found for this source commit. A license must be provided and reviewed before a build can be authorized.");
        response.EnsureSuccessStatusCode();
        using var json = await JsonDocument.ParseAsync(
            await response.Content.ReadAsStreamAsync(ct), cancellationToken: ct).ConfigureAwait(false);
        var root = json.RootElement;
        var license = root.GetProperty("license");
        var spdx = license.ValueKind == JsonValueKind.Object &&
            license.TryGetProperty("spdx_id", out var id) ? id.GetString() : null;
        if (spdx != null && Recognized.Contains(spdx))
            return null;
        if (root.GetProperty("encoding").GetString() != "base64")
            throw new InvalidDataException("The license text could not be retrieved for review.");
        var bytes = Convert.FromBase64String(root.GetProperty("content").GetString() ?? "");
        if (bytes.Length is 0 or > 120000)
            throw new InvalidDataException("The license text is empty or too large to review safely in the launcher.");
        var text = new UTF8Encoding(false, true).GetString(bytes);
        if (string.IsNullOrWhiteSpace(text))
            throw new InvalidDataException("The license text is empty.");
        var path = root.GetProperty("path").GetString();
        if (string.IsNullOrWhiteSpace(path))
            throw new InvalidDataException("GitHub did not identify the license file.");
        var name = license.ValueKind == JsonValueKind.Object &&
            license.TryGetProperty("name", out var licenseName) ? licenseName.GetString() : null;
        return new(repository, sourceRef, path, name ?? spdx ?? "Unrecognized",
            text, Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant());
    }
}
