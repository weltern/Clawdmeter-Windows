#!/usr/bin/env bash
# Build script for Clawdmeter on Linux. Produces dist/Clawdmeter (+ .sha256).
#
# Build on Ubuntu 22.04 (glibc 2.35) for the widest compatibility floor — the
# resulting binary runs on any distro with glibc >= the build host's. Do NOT
# build on 24.04 (glibc 2.39): that silently drops Ubuntu 22.04 LTS and Debian 12.
#
# One-time system deps (the CI workflow installs these; on a dev box run once):
#   sudo apt install -y python3-venv libpython3.10 \
#     libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 \
#     libxkbcommon-x11-0 libglib2.0-0 libgl1 libegl1 libdbus-1-3 libfontconfig1
set -euo pipefail
cd "$(dirname "$0")"

PYBIN="${PYTHON:-python3}"
if [ ! -d .venv ]; then
    "$PYBIN" -m venv .venv
fi
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install pyinstaller==6.20.0

./.venv/bin/pyinstaller --clean --noconfirm Clawdmeter.spec

BIN="dist/Clawdmeter"

# Assemble the SAME artifact CI ships (mirrors build.yml "Package tarball"): the
# shipped Linux asset is the .tar.gz, not the bare binary, so hash THAT in the
# "<hash>  <name>" form the in-app updater (update_check.extract_sha256) reads
# back from the release notes. Building the tarball here keeps a local release
# consistent with CI instead of leaving a bare-binary hash that doesn't match.
VER="$(grep -oE 'APP_VERSION *= *"[^"]+"' src/app_settings.py | grep -oE '[0-9][^"]*')"
NAME="Clawdmeter-${VER:-dev}-linux-x86_64"
rm -rf "pkg/$NAME" && mkdir -p "pkg/$NAME"
cp "$BIN" packaging/clawdmeter.desktop packaging/install.sh "pkg/$NAME/"
cp assets/icon.png "pkg/$NAME/clawdmeter.png"
chmod +x "pkg/$NAME/Clawdmeter" "pkg/$NAME/install.sh"
tar czf "$NAME.tar.gz" -C pkg "$NAME"
sha256sum "$NAME.tar.gz" | tee "$NAME.tar.gz.sha256"

# --- package: AppImage -------------------------------------------------------
# Shipped ALONGSIDE the tarball, not instead of it. They fail in opposite
# directions: the tarball's install.sh registers a desktop entry but needs a
# terminal; an AppImage is one double-clickable file that leaves nothing behind
# but does NOT appear in the applications menu without a helper. Neither
# supersedes the other, so users get both.
#
# This does NOT widen compatibility. An AppImage bundles the app's libraries,
# not glibc, so the "build on the oldest distro you support" rule at the top of
# this file applies to it exactly as it does to the tarball.
#
# Skipped (with a warning, not an error) if appimagetool can't be fetched, so a
# no-network build still produces the tarball.
APPIMAGETOOL_VERSION="1.9.1"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"

build_appimage() {
    local tool="build/appimagetool-${APPIMAGETOOL_VERSION}"
    if [ ! -x "$tool" ]; then
        mkdir -p build
        # Pinned to a tagged release, never `continuous`: a rolling tag means two
        # builds of the same commit can differ, which is not acceptable for a
        # release artifact. Verified by hash for the same reason.
        curl -fsSL -o "$tool" "$APPIMAGETOOL_URL" || return 1
        echo "${APPIMAGETOOL_SHA256}  $tool" | sha256sum -c - >/dev/null || {
            echo "appimagetool checksum MISMATCH — refusing to use it" >&2
            rm -f "$tool"; return 1; }
        chmod +x "$tool"
    fi

    local appdir="build/Clawdmeter.AppDir"
    rm -rf "$appdir"
    mkdir -p "$appdir/usr/bin" \
             "$appdir/usr/share/applications" \
             "$appdir/usr/share/icons/hicolor/512x512/apps"

    cp "$BIN" "$appdir/usr/bin/Clawdmeter"
    chmod +x "$appdir/usr/bin/Clawdmeter"

    # The AppDir spec wants the desktop file and its icon at the ROOT (exactly
    # one .desktop there), with the FHS copies underneath as convention. The icon
    # filename must match the desktop file's Icon= key, which carries no
    # extension. assets/icon.png really is 512x512, so that hicolor path is
    # honest.
    cp packaging/clawdmeter.desktop "$appdir/clawdmeter.desktop"
    cp packaging/clawdmeter.desktop "$appdir/usr/share/applications/clawdmeter.desktop"
    cp assets/icon.png "$appdir/clawdmeter.png"
    cp assets/icon.png "$appdir/usr/share/icons/hicolor/512x512/apps/clawdmeter.png"
    # .DirIcon is what thumbnailers and file managers read. appimagetool will
    # generate it, but doing it here keeps the AppDir valid on its own.
    cp assets/icon.png "$appdir/.DirIcon"
    # NO AppStream metainfo, deliberately. appimagetool wants it at
    # usr/share/metainfo/<desktop-basename>.appdata.xml, but then runs
    # `appstreamcli validate`, which requires the FILENAME to equal the component
    # id -- and a valid id is reverse-DNS (com.clawdmeter.Clawdmeter). Satisfying
    # both means renaming clawdmeter.desktop to com.clawdmeter.Clawdmeter.desktop,
    # which is not a packaging-only change: run_at_startup.py writes the autostart
    # entry by that name and main.py hands it to setDesktopFileName for Wayland
    # icon matching, both with tests. Not worth a runtime change for an optional
    # catalogue file. Omitting it costs one warning line and nothing else.
    # packaging/clawdmeter.appdata.xml is written and ready if that rename ever
    # happens (e.g. alongside a Flatpak, which wants the same convention).

    cat > "$appdir/AppRun" <<'APPRUN'
#!/bin/sh
# Resolve our own location so the payload is found wherever the AppImage mounts.
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/Clawdmeter" "$@"
APPRUN
    chmod +x "$appdir/AppRun"

    # APPIMAGE_EXTRACT_AND_RUN: appimagetool is itself an AppImage, and this
    # runs it without FUSE — needed in containers and CI. Unrelated to the
    # OUTPUT, which embeds the type2 runtime with libfuse linked statically, so
    # the finished AppImage needs neither libfuse2 nor libfuse3 on the user's
    # machine. That is the single most common AppImage complaint and it is only
    # avoided by using this tool rather than the archived AppImageKit.
    ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$tool" "$appdir" "$APPIMAGE" || return 1
    sha256sum "$APPIMAGE" | tee "$APPIMAGE.sha256" >/dev/null
}

APPIMAGE="Clawdmeter-${VER:-dev}-x86_64.AppImage"
if build_appimage; then
    APPIMAGE_LINE="AppImage: $APPIMAGE ($(du -m "$APPIMAGE" | cut -f1) MB, sha $(cut -d' ' -f1 "$APPIMAGE.sha256"))"
else
    APPIMAGE_LINE="AppImage: SKIPPED (appimagetool unavailable) — the tarball above is unaffected"
    rm -f "$APPIMAGE" "$APPIMAGE.sha256"
fi

echo ""
echo "Built:   $BIN"
echo "Package: $NAME.tar.gz"
echo "SHA-256: $(cut -d' ' -f1 "$NAME.tar.gz.sha256")"
echo "Size:    $(du -m "$NAME.tar.gz" | cut -f1) MB"
echo "$APPIMAGE_LINE"
