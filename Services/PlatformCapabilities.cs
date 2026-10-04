namespace QuiverLauncher.Services;

/// <summary>
/// Runtime feature switches for desktop vs Android.
/// </summary>
public static class PlatformCapabilities
{
    public static bool IsMobile => OperatingSystem.IsAndroid();

    public static bool IsDesktop => !IsMobile;

    public static string InstalledAppRemovalLabel => IsMobile ? "Uninstall" : "Delete";

    public static bool SupportsTray => !IsMobile && !KnulliRuntime.IsEnabled;

    public static bool SupportsVelopack => !IsMobile && !KnulliRuntime.IsEnabled;

    public static bool SupportsFolderInstall => !IsMobile;

    public static bool SupportsWine => OperatingSystem.IsLinux() && !IsMobile && !KnulliRuntime.IsEnabled;

    public static bool SupportsSteamShortcuts =>
        !IsMobile && !KnulliRuntime.IsEnabled && (OperatingSystem.IsWindows() || OperatingSystem.IsLinux());

    public static bool SupportsModsFolder => !IsMobile;

    public static bool SupportsGamepadSdl => !IsMobile;
}
