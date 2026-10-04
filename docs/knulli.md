# Knulli on the RG CubeXX

Quiver's Knulli mode targets the ARM64 Anbernic RG CubeXX (Allwinner H700,
Cortex-A53), using `/dev/fb0` and SDL2 gamepad input. It does not require X11,
Wayland, DRM, FUSE, or an installed .NET runtime. Desktop and Android startup
remain unchanged.

## Build and install

Use .NET SDK 10:

```sh
dotnet publish QuiverLauncher.Desktop/QuiverLauncher.Desktop.csproj \
  -c Release -r linux-arm64 --self-contained true -p:PublishTrimmed=false \
  -o artifacts/knulli/quiver-launcher
cp tools/knulli/supervisor.py artifacts/knulli/quiver-launcher/
cp tools/knulli/device_profile.py artifacts/knulli/quiver-launcher/
cp 'tools/knulli/Quiver Launcher.sh' artifacts/knulli/
chmod +x 'artifacts/knulli/Quiver Launcher.sh' \
  artifacts/knulli/quiver-launcher/QuiverLauncher.Desktop
```

Alternatively, run the **Knulli builds** workflow with recipe `launcher`. Download
the `QuiverLauncher-knulli-arm64` Actions artifact, unzip the artifact, and extract
the enclosed tarball into `/userdata/roms/ports/` on the device.

For a local build, with SSH enabled and a verified host key:

```sh
tar -C artifacts/knulli -cf - . |
  ssh root@10.111.222.202 'tar -xf - -C /userdata/roms/ports'
```

Exit Quiver before replacing its binaries. Refresh the EmulationStation game list
and select **Ports > Quiver Launcher**. Do not start a second graphical launcher
over a running game or EmulationStation via SSH: both would own the framebuffer
and controller. Data is kept separately in `/userdata/system/quiver-launcher/`:

- `apps.json`, `settings.json`, `Apps/`, and `Cache/`: normal Quiver library.
- `launcher.log`, `crash.log`: startup/game errors.
- `knulli-builds.json`: explicitly enabled build recipes.
- `Builds/`: saved GitHub build requests, reused across launcher restarts.
- `github-token`: optional private token file; never distribute it in a package.

`QUIVER_DATA_HOME` overrides this data directory. For CLI use, set
`QUIVER_KNULLI=1` and run `QuiverLauncher.Desktop --list` or
`QuiverLauncher.Desktop --download "2048"`. `--knulli` starts the graphical host.

The supervisor selects framebuffer page zero before each Quiver launch. A game
launch closes Quiver, releases its display/input handles, runs the selected
executable without shell interpretation, waits for it, then restarts Quiver.
Exiting Quiver normally returns to EmulationStation.

## On-demand game builds

Copy [the example configuration](../tools/knulli/knulli-builds.json) to
`/userdata/system/quiver-launcher/knulli-builds.json`. Set `Repository` to a build
repository you control and `Ref` to the branch containing
[the workflow](../.github/workflows/knulli.yml). GitHub Actions must be enabled.
New workflow registration may require a push run or the workflow on the default
branch before GitHub permits dispatch.

The example enables `AttemptUnconfiguredGames`. Set it to `false` to permit only
explicit recipes. With it enabled, other GitHub games automatically get a
reasoning-agent build attempt when they have no native ARM64 download.

Configure a fine-grained GitHub token restricted to that repository with
**Actions: read and write** and **Contents: read**. Put it in **Settings >
Advanced > GitHub API Token**, set `QUIVER_GITHUB_TOKEN` in the launch environment,
or save it in `github-token` with permissions `0600` inside the private data
directory. Tokens are not included in dispatch inputs, game requests, or logs.
Treat device backups and settings as sensitive. Do not reuse a broad personal
token when a repository-scoped token is available.

The initial supported recipe is:

| Recipe | Source | Runtime |
| --- | --- | --- |
| `2048` | `libretro/libretro-2048`, pinned to a full Git commit | Knulli's installed RetroArch |

2048 uses a per-game RetroArch input overlay with standardized SDL2
GameController indices and Knulli's active mapping, avoiding stale controller
indices or core-specific input devices from a previously played emulator.
Press **Start** at the title screen; **Menu/Hotkey + Start** exits back to Quiver.
Global RetroArch settings are not modified.

Add an app named `2048` with repository `libretro/libretro-2048` and folder name
`2048` to the library. When Download finds no native ARM64 asset, Quiver:

1. Dispatches the configured recipe and pinned source commit.
2. Finds the exact run using a unique request ID (not the latest unrelated run).
3. Waits up to 45 minutes and reports build failures or cancellation.
4. Downloads the matching Actions artifact using authenticated requests.
5. Verifies GitHub's SHA-256 digest before using the normal game installer.

GitHub artifact redirects are followed without forwarding the API token to
artifact storage. A `knulli-build.json` receipt records the source commit,
artifact URL and checksum in the installed game's directory.

The device does not compile games. The `2048` job uses an ARM64 cross compiler on
an x64 GitHub runner, with the H700 flags from Knulli's `configs/knulli-h700.board`:
`-mcpu=cortex-a53 -mtune=cortex-a53 -fsigned-char`. It includes the source's license
with the resulting core. No commercial game data is bundled.

Completed requests reuse their artifact until its 30-day retention expires.
Increment `BuildRevision` when changing build flags or packaging on a moving
workflow branch, so an older cached artifact is not reused for the same source.
An expired artifact or failed run is reported; retrying starts a fresh build.
Closing Quiver cancels local waiting, not the remote GitHub job. Reopening and
retrying resumes that request.

## Agentic builds without recipes

For an unconfigured GitHub game, Quiver resolves the selected release tag
(or `HEAD` for projects without releases) to a full commit, then dispatches the
`auto` job. A **Copilot SDK reasoning agent** replaces the old fixed build-system
script. GitHub Actions is only the compute/dispatch/artifact transport, not the
build decision maker; this is not a self-hosted agent server.

Configure the repository secret **`COPILOT_GITHUB_TOKEN`** with a credential
authorized for Copilot, separate from the device's Actions token. The account
must have access to the configured model (`gpt-5.4`, high reasoning effort).
Agent runs may consume Copilot usage. A missing/unauthorized credential fails
explicitly; there is no silent fallback to the old scripted builder. The pinned
SDK and its verified runtime are installed only on the runner, not on Knulli.

The agent can:

1. List, search, and read the pinned game's source using constrained tools.
2. Inspect the handheld's observed graphics libraries, framebuffer, architecture,
   SDL joystick/button/axis counts, and SDL controller mapping.
3. Infer build flags, input APIs, packaging and necessary source adaptations.
4. Compile in isolation, read the actual errors, and revise its recipe.
5. Publish only after an ARM64 ELF entrypoint and portable package pass checks.

The loop permits five build attempts, six minutes per compiler run, 60 tool calls,
at most 20 source inspections before each compiler attempt, and 35 minutes of
agent time inside the 40-minute job. It starts with ARM64
compilers and common SDL2, OpenGL/EGL, image, audio, and compression libraries.
Missing dependencies can still require updating the toolchain; the agent cannot
install arbitrary network dependencies from within a source build.

The source build runs as an unprivileged user in a disposable container with no
network, GitHub credentials, Docker socket, or host filesystem access beyond its
read-only source, read-only proposed recipe and output directory. CPU, memory, process count, and job time
are limited. Submodules are fetched before the isolated build. Build systems
that download more dependencies during compilation will fail with a log rather
than receive unrestricted network access.

The Copilot session exposes no built-in shell, filesystem, MCP, SSH or subagent
tools. Repository instructions are not loaded. Only the custom bounded tools
can inspect source or invoke the container. Game code receives no AI/GitHub token.
Recipe inputs and runner logs are outside the container's writable output mount.
Special files and symlinks in generated packages are rejected.

The installed package includes `quiver-agent-report.json` (build choices,
controller analysis, exact source citations and limitations) and
`quiver-build-recipe.txt` (the successful reproducible recipe). Complete attempt
logs are available in `knulli-auto-report-<request-id>`. Controller analysis must
also appear in the install-result dialog (or CLI output), including unsupported
input and the explicit lack of device verification. Controller analysis must
identify native/adapted/unsupported input, distinguish normalized SDL
GameController indices from physical Joystick indices, and cite actual source
lines. Native/adapted claims require separate citations and explanations for
initialization/platform guards and the selected binding table, not just event
handlers or the existence of a joystick. It must never label compilation as
device or controller verification.
Both `runtime_verified` fields are enforced as `false` until separate device
testing occurs.

The Julius verification run used this reasoning path without a game-specific
recipe: it identified the upstream Linux joystick-mode guard, enabled the
existing controller implementation, compiled ARM64 code and preserved the
observed RG CubeXX mappings. This proves agent-driven source adaptation and
packaging, not complete Caesar III gameplay; original game data remains required.

`device_profile.py` reads hardware metadata and the current Knulli SDL mapping
database, not button events or arbitrary environment variables. It exports no
tokens, network addresses, usernames or user files. Hardware/input profiles are
included in the build cache key so a build for another controller is not silently
reused. Unknown mappings are reported as unknown, never guessed. Physical button
calibration and live on-device gameplay testing remain separate tasks.

## Limits

- Unconfigured GitHub games are attempted, not guaranteed. Unsupported build
  systems, Rust/.NET toolchains, private repositories, GitLab sources and commercial
  game data can need further integration or a specific recipe. Use the agent's
  failure report to improve the available toolchain or supply a reviewed recipe.
- A Linux ARM64 archive is a candidate, not proof of framebuffer compatibility.
  Games requiring X11/Wayland or a different GPU stack still need a port.
- Windows, generic architecture-unknown Linux, AppImage, and Flatpak downloads
  are not automatically selected in Knulli mode. PortMaster ZIPs are not generic
  Quiver packages; install those through PortMaster.
- Knulli mode disables desktop self-updates, tray support, Wine, and Steam
  shortcuts. Update the launcher by replacing the Ports package instead.
- The framebuffer host uses the gamepad, not a desktop mouse/keyboard backend.
  Other Knulli devices may need different display rotation, input, or libraries.

## Validation

```sh
dotnet test QuiverLauncher.Tests/QuiverLauncher.Tests.csproj \
  --filter 'FullyQualifiedName~Knulli|FullyQualifiedName~GameDownloadInstallServiceTests'
python3 -m unittest discover -s tools/knulli -p 'test_*.py'
```
