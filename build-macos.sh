#!/usr/bin/env bash
# Build script for Clawdmeter on macOS. Produces dist/Clawdmeter.app, then a
# dist/Clawdmeter-macos.zip (+ .sha256) suitable for distribution/testing.
#
# Must run ON macOS — PyInstaller's BUNDLE step (the .app) only works there.
# The build VM is Intel x86_64, so this yields an x86_64 .app that also runs on
# Apple Silicon via Rosetta 2. A native arm64/universal2 build needs arm64
# Python + PySide6, which requires real Apple Silicon hardware (see the plan).
#
# Uses `uv` when available (matches the test-VM setup: sudo-free uv + Python
# 3.12); falls back to a plain `python3 -m venv`. Override the interpreter with
# PYTHON=/path/to/python3.
set -euo pipefail
cd "$(dirname "$0")"

# --- venv + deps ------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
    echo "==> Using uv"
    uv venv --python "${PYTHON:-3.12}" .venv 2>/dev/null || uv venv .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    uv pip install --upgrade pip
    uv pip install -r requirements.txt
    uv pip install pyinstaller==6.20.0
else
    echo "==> uv not found; using python venv"
    PYBIN="${PYTHON:-python3}"
    if [ ! -d .venv ]; then
        "$PYBIN" -m venv .venv
    fi
    ./.venv/bin/pip install --upgrade pip
    ./.venv/bin/pip install -r requirements.txt
    ./.venv/bin/pip install pyinstaller==6.20.0
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# --- icon: generate assets/icon.icns from icon.png if missing ---------------
# The spec picks up assets/icon.icns automatically when present; the menu-bar
# tray icon is separate (a runtime PNG), so a missing .icns only means the .app's
# Finder/Get-Info icon is generic — not fatal.
if [ ! -f assets/icon.icns ] && [ -f assets/icon.png ] \
   && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
    echo "==> Generating assets/icon.icns from assets/icon.png"
    ICONSET="$(mktemp -d)/Clawdmeter.iconset"
    mkdir -p "$ICONSET"
    for sz in 16 32 128 256 512; do
        sips -z "$sz" "$sz" assets/icon.png \
            --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null
        sips -z "$((sz * 2))" "$((sz * 2))" assets/icon.png \
            --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o assets/icon.icns || \
        echo "    (iconutil failed — building without a bundle icon)"
fi

# --- build ------------------------------------------------------------------
pyinstaller --clean --noconfirm Clawdmeter.spec

APP="dist/Clawdmeter.app"
if [ ! -d "$APP" ]; then
    echo "ERROR: $APP was not produced — check the PyInstaller output above." >&2
    exit 1
fi

# --- ad-hoc sign ------------------------------------------------------------
# Unsigned apps get "damaged/can't be opened" on some macOS versions even when
# built locally. An ad-hoc signature (identity "-") fixes local launch. It does
# NOT satisfy Gatekeeper for a *downloaded* app — that still needs right-click ->
# Open (or a real Developer ID signature + notarization). Locally built = no
# quarantine attribute, so it just runs.
echo "==> Ad-hoc signing $APP"
codesign --force --deep --sign - "$APP" || \
    echo "    (codesign failed — the app may need right-click -> Open)"

# --- package: zip + sha256 --------------------------------------------------
ZIP="dist/Clawdmeter-macos.zip"
rm -f "$ZIP"
# ditto preserves the bundle's symlinks/permissions better than `zip`.
ditto -c -k --keepParent "$APP" "$ZIP"
shasum -a 256 "$ZIP" | awk '{print $1"  Clawdmeter-macos.zip"}' > "$ZIP.sha256"

# --- package: drag-to-install .dmg ------------------------------------------
# The disk image is what a user actually downloads: open it and you get a window
# with Clawdmeter on the left and an Applications alias on the right — drag
# across to install, the way VS Code / Slack / Docker ship.
#
# dmgbuild (not create-dmg) because it writes the window's .DS_Store directly
# instead of driving Finder over AppleScript, so this works on a headless/SSH
# build box. Set SKIP_DMG=1 to build just the .app + zip.
DMG="dist/Clawdmeter.dmg"
if [ "${SKIP_DMG:-0}" = "1" ]; then
    echo "==> SKIP_DMG=1 — not building the disk image"
    DMG=""
else
    echo "==> Building $DMG"
    # The [badge_icons] extra pulls pyobjc-framework-Quartz. Without it dmgbuild
    # SILENTLY skips badge_icon and the mounted volume gets the generic white
    # drive icon instead of Clawd — verified the hard way.
    pip install --quiet "dmgbuild[badge_icons]>=1.6" 2>/dev/null || \
        uv pip install --quiet "dmgbuild[badge_icons]>=1.6" || true

    # HiDPI background: one TIFF holding the 1x and 2x pages. Without tiffutil
    # dmg_settings.py falls back to the plain 1x PNG.
    if command -v tiffutil >/dev/null 2>&1 \
       && [ -f packaging/dmg-background.png ] \
       && [ -f packaging/dmg-background@2x.png ]; then
        tiffutil -cathidpicheck packaging/dmg-background.png \
            "packaging/dmg-background@2x.png" \
            -out packaging/dmg-background.tiff >/dev/null 2>&1 || \
            echo "    (tiffutil failed — falling back to the 1x background)"
    fi

    rm -f "$DMG"
    if dmgbuild -s packaging/dmg_settings.py -D app="$APP" -D root="$PWD" \
                "Clawdmeter" "$DMG"; then
        shasum -a 256 "$DMG" | awk '{print $1"  Clawdmeter.dmg"}' > "$DMG.sha256"
        # dmgbuild degrades quietly when an optional piece is missing; say so
        # rather than shipping a half-styled image without noticing.
        if ! hdiutil imageinfo "$DMG" -plist >/dev/null 2>&1; then
            echo "    WARNING: $DMG did not verify with hdiutil"
        fi
    else
        echo "ERROR: dmgbuild failed — the .app and .zip above are still good." >&2
        DMG=""
    fi
    # Regenerable artifact; keep the tree clean for the next build.
    rm -f packaging/dmg-background.tiff
fi

echo ""
echo "Built:   $APP"
echo "Zipped:  $ZIP"
echo "SHA-256: $(cut -d' ' -f1 "$ZIP.sha256")"
echo "Size:    $(du -m "$ZIP" | cut -f1) MB"
echo "Arch:    $(file "$APP/Contents/MacOS/Clawdmeter" | sed 's/.*: //')"
if [ -n "$DMG" ] && [ -f "$DMG" ]; then
    echo "Disk image: $DMG ($(du -m "$DMG" | cut -f1) MB)"
    echo "DMG SHA-256: $(cut -d' ' -f1 "$DMG.sha256")"
fi
