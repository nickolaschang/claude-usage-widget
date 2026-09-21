#!/bin/sh
# Entry point for the Claude Code plugin on macOS and Linux. The plugin's skills call this and
# relay what it prints. Windows uses widget.ps1, which does the same things; run from Git Bash on
# Windows, this script simply hands over to it.
#
# Claude Code keeps an installed plugin in a cache folder whose path includes the version, so it
# moves on every update. The widget needs paths that stay put, because the status line setting
# stores an absolute path. So nothing runs from the plugin folder: every verb first copies the app
# to a stable folder and works from there.
#
#   start      copy the app into place and open the widget
#   stop       close the widget
#   status     what is installed, whether it is running, and the state of the limit feed
#   usage      print the usage numbers (no window)
#   setup      install the status line feed that powers the limit rows. Exit code 2 means the
#              user already has a status line of their own: ask them before using --force
#   uninstall  close the widget, remove our status line, delete the app copy
#   sync       only copy the app into place
set -eu

verb="${1:-status}"
force="${2:-}"
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(dirname -- "$here")

case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*)
        [ "$force" = "--force" ] && set -- "$verb" -Force
        exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$here/widget.ps1" "$@"
        ;;
    Darwin)
        default_home="$HOME/Library/Application Support/ClaudeUsageWidget"
        ;;
    *)
        default_home="${XDG_DATA_HOME:-$HOME/.local/share}/claude-usage-widget"
        ;;
esac
app="${CLAUDE_USAGE_WIDGET_APP_DIR:-$default_home/app}"      # tests point this at a temp folder

python=$(command -v python3 || command -v python || true)
if [ -z "$python" ]; then
    echo "Python 3.9 or newer is needed and was not found on PATH."
    exit 1
fi

plugin_version() {
    "$python" -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version", "unknown"))' \
        "$root/.claude-plugin/plugin.json" 2>/dev/null || echo unknown
}

sync_app() {
    mkdir -p "$app/python"
    for name in claude_usage.py claude_usage_widget.py ratelimit_feed.py install_statusline.py; do
        cp "$root/python/$name" "$app/python/$name"
    done
    cp "$root/pricing.json" "$root/LICENSE" "$app/"
    plugin_version > "$app/VERSION"
}

widget_pids() {
    # Only a Python that is running the script is the widget. Matching the path alone would also
    # catch an editor or a shell that merely mentions the file, and "stop" would kill it.
    # pgrep exits 1 when nothing matches; that is not an error here.
    pgrep -f "[Pp]ython[0-9.]* +$app/python/claude_usage_widget\.py" 2>/dev/null || true
}

case "$verb" in
    sync)
        sync_app
        echo "App copied to $app (version $(plugin_version))."
        ;;

    start)
        pids=$(widget_pids)
        if [ -n "$pids" ]; then
            echo "The widget is already running (pid $(echo "$pids" | head -n 1)). Nothing to do."
            exit 0
        fi
        sync_app
        if ! "$python" -c 'import tkinter' 2>/dev/null; then
            echo "The floating window needs tkinter, which this Python does not have."
            echo "  Debian/Ubuntu: sudo apt install python3-tk    Fedora: sudo dnf install python3-tkinter"
            echo "  Arch: sudo pacman -S tk                        macOS (Homebrew): brew install python-tk"
            echo "Or skip the window: '$python $app/python/claude_usage.py --format oneline' feeds SwiftBar, xbar, Argos, waybar or polybar."
            exit 3
        fi
        if [ "$(uname -s)" != "Darwin" ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
            echo "There is no desktop session here (no DISPLAY), so a window cannot open. Use the usage skill instead."
            exit 3
        fi
        nohup "$python" "$app/python/claude_usage_widget.py" > /dev/null 2>&1 &
        sleep 2
        pids=$(widget_pids)
        if [ -z "$pids" ]; then
            echo "The widget did not stay up. Try it in a terminal to see why: $python $app/python/claude_usage_widget.py"
            exit 1
        fi
        echo "Widget started (pid $(echo "$pids" | head -n 1), version $(plugin_version))."
        echo "Drag it anywhere, double-click to shrink it to a pill, right-click for the menu."
        echo "If the window manager mishandles the borderless window, stop it and run it with --decorated."
        ;;

    stop)
        pids=$(widget_pids)
        if [ -z "$pids" ]; then
            echo "The widget is not running."
            exit 0
        fi
        # shellcheck disable=SC2086
        kill $pids
        echo "Widget stopped."
        ;;

    usage)
        sync_app
        "$python" "$app/python/claude_usage.py"
        ;;

    status)
        echo "Plugin version : $(plugin_version)"
        if [ -f "$app/VERSION" ]; then
            echo "App copy       : $app ($(cat "$app/VERSION"))"
        else
            echo "App copy       : $app (not copied yet)"
        fi
        pids=$(widget_pids)
        if [ -n "$pids" ]; then
            echo "Widget         : running, pid $(echo "$pids" | head -n 1)"
        else
            echo "Widget         : not running"
        fi
        if [ -f "$app/python/install_statusline.py" ]; then
            "$python" "$app/python/install_statusline.py" check
        else
            echo "status line: unknown until the app is copied (run start or setup)"
        fi
        ;;

    setup)
        sync_app
        code=0
        if [ "$force" = "--force" ]; then
            "$python" "$app/python/install_statusline.py" install --force || code=$?
        else
            "$python" "$app/python/install_statusline.py" install || code=$?
        fi
        [ "$code" -eq 0 ] && echo "The limit rows appear within 30 seconds of your next Claude Code response."
        exit "$code"
        ;;

    uninstall)
        pids=$(widget_pids)
        if [ -n "$pids" ]; then
            # shellcheck disable=SC2086
            kill $pids
            echo "Widget stopped."
        fi
        if [ -f "$app/python/install_statusline.py" ]; then
            "$python" "$app/python/install_statusline.py" remove || true
        fi
        if [ -d "$app" ]; then
            rm -rf -- "$app"
            echo "Deleted the app copy at $app."
        fi
        echo "Window position, the limit feed and the engine cache stay in the widget's state folder. Delete it too for a clean slate."
        echo "To remove the plugin itself: /plugin uninstall usage-widget"
        ;;

    *)
        echo "Unknown verb '$verb'. Use one of: start stop status usage setup uninstall sync"
        exit 64
        ;;
esac
