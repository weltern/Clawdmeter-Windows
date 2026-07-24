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

echo ""
echo "Built:   $BIN"
echo "Package: $NAME.tar.gz"
echo "SHA-256: $(cut -d' ' -f1 "$NAME.tar.gz.sha256")"
echo "Size:    $(du -m "$NAME.tar.gz" | cut -f1) MB"
