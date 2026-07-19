#!/usr/bin/env bash
# Install Clawdmeter for the current user (no root needed). Run from inside the
# extracted tarball directory: ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR"

install -m 755 Clawdmeter "$BIN_DIR/Clawdmeter"
install -m 644 clawdmeter.png "$ICON_DIR/clawdmeter.png"

# Point the menu entry's Exec at the installed binary.
sed "s|^Exec=.*|Exec=$BIN_DIR/Clawdmeter|" clawdmeter.desktop > "$APP_DIR/clawdmeter.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DIR" 2>/dev/null || true

echo "Installed:  $BIN_DIR/Clawdmeter"
echo "Menu entry: $APP_DIR/clawdmeter.desktop"
case ":$PATH:" in
  *":$BIN_DIR:"*) : ;;
  *) echo "NOTE: $BIN_DIR is not on your PATH — add it, or launch from the menu entry." ;;
esac
echo ""
echo "If the app fails with a Qt 'xcb' platform-plugin error, install the one system lib it needs:"
echo "  Debian/Ubuntu:  sudo apt install libxcb-cursor0"
echo "  Fedora:         sudo dnf install xcb-util-cursor"
echo "  Arch:           sudo pacman -S xcb-util-cursor"
