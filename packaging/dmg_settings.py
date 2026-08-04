# dmgbuild settings for the Clawdmeter drag-to-install disk image.
#
# Invoked by build-macos.sh:
#     dmgbuild -s packaging/dmg_settings.py -D app=dist/Clawdmeter.app \
#              "Clawdmeter" dist/Clawdmeter.dmg
#
# Why dmgbuild and not create-dmg: create-dmg drives Finder over AppleScript to
# lay the window out, which needs a logged-in GUI session and Automation
# permission — it does not work over a plain SSH build. dmgbuild writes the
# .DS_Store directly, so the image builds headlessly on a build box. Same
# result, no GUI.
#
# Geometry is mirrored in packaging/make_dmg_background.py — change both or the
# arrow in the artwork stops pointing at the Applications folder.

import os.path

application = defines.get("app", "dist/Clawdmeter.app")  # noqa: F821 (dmgbuild)
appname = os.path.basename(application)

# dmgbuild exec()s this file against its OWN module globals, so `__file__` here
# points at dmgbuild's core.py, not at us. Take the repo root from a -D define
# (build-macos.sh passes it) and fall back to the CWD it runs from.
_root = defines.get("root", os.getcwd())  # noqa: F821 (dmgbuild)
_here = os.path.join(_root, "packaging")


def _background():
    """Prefer the HiDPI two-page TIFF (built by tiffutil at package time), fall
    back to the plain 1x PNG so the image still builds without it."""
    for name in ("dmg-background.tiff", "dmg-background.png"):
        path = os.path.join(_here, name)
        if os.path.exists(path):
            return path
    return None


# --- image ------------------------------------------------------------------
format = "UDZO"          # compressed, read-only — the standard for distribution
size = None              # auto-size to the content

files = [application]
symlinks = {"Applications": "/Applications"}

# Volume icon: the mounted disk shows the Clawd icon in the Finder sidebar
# instead of the generic white drive.
_icns = os.path.join(_root, "assets", "icon.icns")
badge_icon = _icns if os.path.exists(_icns) else None

# --- window -----------------------------------------------------------------
background = _background()
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
sidebar_width = 180

# (x, y) of the window's top-left on screen, then its size. The size MUST match
# the background art's point size (600x400) or Finder tiles/crops it.
window_rect = ((220, 180), (600, 400))

arrange_by = None
grid_offset = (0, 0)
grid_spacing = 100
scroll_position = (0, 0)
label_pos = "bottom"
text_size = 13
icon_size = 128
show_icon_preview = False

# Icon centres, in background-image coordinates.
icon_locations = {
    appname: (150, 190),
    "Applications": (450, 190),
}
