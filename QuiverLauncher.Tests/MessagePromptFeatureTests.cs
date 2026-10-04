using Avalonia.Controls;
using Avalonia.Headless.XUnit;
using Avalonia.Threading;
using FluentAssertions;
using QuiverLauncher.Services;
using QuiverLauncher.ViewModels;
using QuiverLauncher.Views;

namespace QuiverLauncher.Tests;

public class MessagePromptFeatureTests
{
    [AvaloniaFact]
    public async Task License_prompt_defaults_to_cancel_and_scrolls_without_selecting_accept()
    {
        var session = new LauncherSession();
        var view = new MessagePromptView();
        view.Configure(session);
        var window = new Window { Content = view, Width = 720, Height = 720 };
        try
        {
            window.Show();
            var text = string.Join("\n", Enumerable.Repeat("Full license text must remain readable.", 100));
            var answer = view.ShowAsync(text, "Review source license", true, preferCancelDefault: true,
                acceptLabel: "Continue", rejectLabel: "Cancel", scrollBody: true);
            window.UpdateLayout();
            Dispatcher.UIThread.RunJobs();
            var cancel = view.FindControl<Button>("MessagePromptNoButton")!;
            cancel.Content.Should().Be("Cancel");
            cancel.IsFocused.Should().BeTrue();
            view.Model.Body.Should().Be(text);
            var scroll = view.FindControl<ScrollViewer>("MessagePromptScroll")!;
            view.Navigate(NavigationDirection.Down).Should().BeTrue();
            scroll.Offset.Y.Should().BeGreaterThan(0);
            cancel.IsFocused.Should().BeTrue();
            view.Confirm();
            (await answer).Should().Be(MessagePromptResult.No);

            var acceptance = view.ShowAsync("Confirm permitted use and PUBLIC fork/artifacts.",
                "License acceptance", true, preferCancelDefault: true,
                acceptLabel: "Accept & build", rejectLabel: "Cancel", scrollBody: true);
            Dispatcher.UIThread.RunJobs();
            cancel.IsFocused.Should().BeTrue();
            view.FindControl<Button>("MessagePromptYesButton")!.Content.Should().Be("Accept & build");
            view.Navigate(NavigationDirection.Left);
            view.Confirm();
            (await acceptance).Should().Be(MessagePromptResult.Yes);
            var ordinary = view.ShowAsync("Ordinary question", "Confirm", true);
            view.Model.AcceptLabel.Should().Be("Yes");
            view.Model.RejectLabel.Should().Be("No");
            view.Model.ScrollBody.Should().BeFalse();
            view.Cancel();
            (await ordinary).Should().Be(MessagePromptResult.No);
        }
        finally { window.Close(); await session.DisposeAsync(); }
    }

    [Fact]
    public async Task Prompts_preserve_queue_order_and_close_all_waiters_on_cancellation()
    {
        var model = new MessagePromptViewModel();
        using var cancellation = new CancellationTokenSource();
        var secondOpened = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        model.Opened += _ => { if (model.Title == "Second") secondOpened.TrySetResult(); };
        var first = model.ShowAsync("First body", "First", true, false, true, cancellation.Token);
        var second = model.ShowAsync("Second body", "Second", true, false, true, cancellation.Token);
        model.Title.Should().Be("First");
        model.Complete(MessagePromptResult.No);
        (await first).Should().Be(MessagePromptResult.No);
        await secondOpened.Task;
        model.Body.Should().Be("Second body");
        var third = model.ShowAsync("Third body", "Third", true, false, true, cancellation.Token);
        cancellation.Cancel();
        (await second).Should().Be(MessagePromptResult.Cancel);
        (await third).Should().Be(MessagePromptResult.Cancel);
        model.IsOpen.Should().BeFalse();
        model.Title.Should().Be("Second");
    }
    [AvaloniaFact]
    public async Task Prompt_navigation_returns_choice_and_restores_underlying_editor_focus()
    {
        var session = new LauncherSession();
        var view = new MessagePromptView();
        view.Configure(session);
        var field = new TextBox { Text = "Editing" };
        var window = new Window { Content = new Grid { Children = { field, view } }, Width = 700, Height = 500 };
        try
        {
            window.Show();
            field.Focus();
            var answer = view.ShowAsync("Save changes?", "Confirm", true, includeCancel: true);
            view.Model.IsOpen.Should().BeTrue();
            view.Navigate(NavigationDirection.Right).Should().BeTrue();
            view.Confirm().Should().BeTrue();
            (await answer).Should().Be(MessagePromptResult.No);
            field.IsFocused.Should().BeTrue();
            view.Model.IsOpen.Should().BeFalse();
            var pending = view.ShowAsync("Continue?", "Closing host", true);
            await session.DisposeAsync();
            (await pending).Should().Be(MessagePromptResult.Cancel);
        }
        finally { window.Close(); await session.DisposeAsync(); }
    }
}
