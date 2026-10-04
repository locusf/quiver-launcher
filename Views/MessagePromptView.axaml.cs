using NavigationDirection = QuiverLauncher.Services.NavigationDirection;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Threading;
using Avalonia.VisualTree;
using QuiverLauncher.Services;
using QuiverLauncher.ViewModels;

namespace QuiverLauncher.Views;
public partial class MessagePromptView : UserControl, IFeatureNavigationHandler
{
    public MessagePromptViewModel Model { get; } = new();

    private LauncherSession _session = null!;
    private int _index;
    private Control? _previousFocus;
    public MessagePromptView()
    {
        InitializeComponent();
        DataContext = Model;
        Model.Opened += FocusPrompt;
        AddHandler(InputElement.GotFocusEvent, Prompt_GotFocus, RoutingStrategies.Bubble);
    }

    public void Configure(LauncherSession session) => _session = session;
    private void FocusPrompt(bool preferCancel)
    {
        MessagePromptScroll.Offset = default;
        _previousFocus = TopLevel.GetTopLevel(this)?.FocusManager?.GetFocusedElement() as Control;
        var target = !Model.IsQuestion ? MessagePromptOkButton : preferCancel ? MessagePromptNoButton : MessagePromptYesButton;
        _index = Controls().IndexOf(target);
        UpdateSelectionHighlight();
        if (!target.Focus())
        {
            Dispatcher.UIThread.Post(() =>
            {
                if (Model.IsOpen && !Controls().Any(control => control.IsFocused))
                    RestoreFocus();
            }, DispatcherPriority.Loaded);
        }
    }

    public async Task<MessagePromptResult> ShowAsync(string message, string title, bool isQuestion, bool preferCancelDefault = false, bool includeCancel = false,
        string acceptLabel = "Yes", string rejectLabel = "No", bool scrollBody = false)
    {
        if (!Dispatcher.UIThread.CheckAccess())
            return await Dispatcher.UIThread.InvokeAsync(() => ShowAsync(message, title, isQuestion, preferCancelDefault, includeCancel, acceptLabel, rejectLabel, scrollBody));
        try
        {
            return await Model.ShowAsync(message, title, isQuestion, preferCancelDefault, includeCancel, _session.Token, acceptLabel, rejectLabel, scrollBody);
        }
        finally
        {
            if (!Model.IsOpen)
                UpdateSelectionHighlight();
        }
    }

    // Visibility bindings may not have updated yet when a prompt opens.
    private List<Control> Controls() => !Model.IsQuestion ? [MessagePromptOkButton] :
        Model.IncludeCancel ? [MessagePromptYesButton, MessagePromptNoButton, MessagePromptCancelButton] :
        [MessagePromptYesButton, MessagePromptNoButton];

    private void UpdateSelectionHighlight()
    {
        var controls = Controls();
        var selected = controls[Math.Clamp(_index, 0, controls.Count - 1)];
        foreach (var button in new[] { MessagePromptYesButton, MessagePromptNoButton, MessagePromptCancelButton, MessagePromptOkButton })
            button.Classes.Set(GamepadFocusChrome.FocusedClassName,
                Model.IsOpen && (Model.ScrollBody || GamepadFocusChrome.IsActive) && ReferenceEquals(button, selected));
    }

    private void Prompt_GotFocus(object? sender, FocusChangedEventArgs e)
    {
        if (!Model.IsOpen)
            return;
        var index = GamepadControlActivation.IndexOfControlContainingFocus(Controls(), e.Source);
        if (index < 0)
            return;
        _index = index;
        UpdateSelectionHighlight();
    }

    public void Complete(MessagePromptResult result)
    {
        Model.Complete(result);
        UpdateSelectionHighlight();
        var previous = _previousFocus;
        _previousFocus = null;
        if (!_session.IsClosed && previous?.IsEffectivelyVisible == true && previous.IsEnabled && TopLevel.GetTopLevel(previous) != null)
            previous.Focus();
    }

    public bool Dismiss()
    {
        if (!Model.IsOpen)
            return false;
        Complete(Model.IncludeCancel ? MessagePromptResult.Cancel : Model.IsQuestion ? MessagePromptResult.No : MessagePromptResult.Yes);
        return true;
    }

    public bool Navigate(NavigationDirection direction)
    {
        if (!Model.IsOpen)
            return false;
        if (Model.ScrollBody && direction is NavigationDirection.Up or NavigationDirection.Down)
        {
            var offset = MessagePromptScroll.Offset;
            MessagePromptScroll.Offset = new Avalonia.Vector(offset.X,
                Math.Clamp(offset.Y + (direction == NavigationDirection.Up ? -80 : 80),
                    0, Math.Max(0, MessagePromptScroll.Extent.Height - MessagePromptScroll.Viewport.Height)));
            return true;
        }
        var controls = Controls();
        var focused = controls.FindIndex(c => c.IsFocused);
        if (focused >= 0)
            _index = focused;
        var delta = direction is NavigationDirection.Left or NavigationDirection.Up ? -1 : 1;
        _index = Math.Clamp(_index + delta, 0, controls.Count - 1);
        UpdateSelectionHighlight();
        controls[_index].Focus();
        return true;
    }

    public bool Confirm()
    {
        if (!Model.IsOpen)
            return false;
        var controls = Controls();
        var button = controls.OfType<Button>().FirstOrDefault(c => c.IsFocused) ?? (Button)controls[Math.Clamp(_index, 0, controls.Count - 1)];
        GamepadControlActivation.ActivateButton(button);
        return true;
    }

    public bool Cancel() => Dismiss();
    public bool Options() => Model.IsOpen;
    public void RestoreFocus()
    {
        var controls = Controls();
        if (Model.IsOpen && controls.Count > 0)
        {
            UpdateSelectionHighlight();
            controls[Math.Clamp(_index, 0, controls.Count - 1)].Focus();
        }
    }

    private void MessagePromptYesButton_Click(object? sender, RoutedEventArgs e) => Complete(MessagePromptResult.Yes);
    private void MessagePromptNoButton_Click(object? sender, RoutedEventArgs e) => Complete(MessagePromptResult.No);
    private void MessagePromptCancelButton_Click(object? sender, RoutedEventArgs e) => Complete(MessagePromptResult.Cancel);
    private void MessagePromptOkButton_Click(object? sender, RoutedEventArgs e) => Complete(MessagePromptResult.Yes);
    private void MessagePromptDimmer_PointerPressed(object? sender, PointerPressedEventArgs e)
    {
        Dismiss();
        e.Handled = true;
    }
}
