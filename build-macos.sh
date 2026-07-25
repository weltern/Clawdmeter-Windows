#!/usr/bin/env bash
# Build script for Clawdmeter on macOS. Produces dist/Clawdmeter.app, then a
# dist/Clawdmeter-macos.zip (+ .sha256) suitable for distribution/testing.
#
# Must run ON macOS — PyInstaller's BUNDLE step (the .app) only works there.
#
# ARCHITECTURE: this aims for a single universal2 .app (arm64 + x86_64), which
# is what Apple recommends and what Firefox/Slack ship — one download, and the
# user never has to know which CPU their Mac has. It happens automatically when
# the interpreter carries both slices: every one of our compiled dependencies
# (PySide6, pyobjc) already ships universal2 wheels, so the interpreter is the
# only thing that decides. `uv`-managed CPython is single-arch, so install the
# python.org build, which is universal2:
#
#     curl -LO https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg
#     sudo installer -pkg python-3.12.10-macos11.pkg -target /
#
# It is picked up automatically from /Library/Frameworks. Without it the build
# still works and simply produces a single-arch .app for this machine.
#
# Uses `uv` when available; falls back to a plain `python3 -m venv`. Override
# the interpreter with PYTHON=/path/to/python3.
set -euo pipefail
cd "$(dirname "$0")"

# --- pick an interpreter ----------------------------------------------------
# True when the given Mach-O carries BOTH the arm64 and x86_64 slices.
# Deliberately grep and not `case`: macOS ships bash 3.2, whose parser closes a
# $( ) at the first unbalanced ')' — so a case pattern inside a command
# substitution is a syntax error there.
_is_universal() {
    _iu="$(lipo -archs "$1" 2>/dev/null)" || return 1
    printf '%s' "$_iu" | grep -q arm64 && printf '%s' "$_iu" | grep -q x86_64
}

# Prefer a universal2 python.org framework build so the .app gets both slices.
if [ -z "${PYTHON:-}" ]; then
    for _fw in /Library/Frameworks/Python.framework/Versions/3.1[3210]/bin/python3; do
        [ -x "$_fw" ] || continue
        if _is_universal "$_fw"; then
            PYTHON="$_fw"
            echo "==> Using universal2 interpreter: $PYTHON"
            break
        fi
    done
fi

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

# --- architecture: universal2 when every input has both slices ---------------
# PyInstaller validates arch slices strictly and aborts the whole build if any
# collected binary is missing one, so check first and report WHICH dependency
# is single-arch rather than letting it fail deep in the collect phase.
if _is_universal "$(command -v python)"; then
    _bad=0
    _badlist=""
    for _f in $(find .venv/lib/python*/site-packages \
                     \( -name '*.so' -o -name '*.dylib' \) 2>/dev/null); do
        if ! _is_universal "$_f"; then
            _bad=$((_bad + 1))
            if [ "$_bad" -le 5 ]; then
                _badlist="$_badlist    $_f ($(lipo -archs "$_f" 2>/dev/null))
"
            fi
        fi
    done
    if [ "$_bad" -eq 0 ]; then
        export CLAWD_TARGET_ARCH=universal2
        echo "==> Building universal2 (arm64 + x86_64)"
    else
        echo "==> $_bad single-arch dependencies — building for this machine only:"
        printf '%s' "$_badlist"
    fi
else
    echo "==> Interpreter is $(lipo -archs "$(command -v python)" 2>/dev/null) only — building single-arch."
    echo "    For a universal .app, install the python.org universal2 build (see header)."
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
echo "Arch:    $(lipo -archs "$APP/Contents/MacOS/Clawdmeter" 2>/dev/null || \
                 file "$APP/Contents/MacOS/Clawdmeter" | sed 's/.*: //')"
if [ -n "$DMG" ] && [ -f "$DMG" ]; then
    echo "Disk image: $DMG ($(du -m "$DMG" | cut -f1) MB)"
    echo "DMG SHA-256: $(cut -d' ' -f1 "$DMG.sha256")"
fi
