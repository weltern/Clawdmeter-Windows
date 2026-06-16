# PyInstaller spec for Clawdmeter-Windows.
# Build with:  pyinstaller Clawdmeter.spec
# Output:      dist/Clawdmeter.exe (single-file, no console)

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('assets/sprites', 'assets/sprites'),
        ('assets/icon.png', 'assets'),
        ('assets/icon.ico', 'assets'),
        # Font Awesome 6 Free (Solid) — tab/nav icons. Loaded via QFontDatabase
        # at startup; SIL OFL 1.1, license bundled alongside.
        ('assets/fonts/fa-solid-900.ttf', 'assets/fonts'),
        ('assets/fonts/LICENSE.txt', 'assets/fonts'),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # QtNetwork is bundled (NOT excluded): single_instance.py uses
        # QLocalServer/QLocalSocket for the single-instance guard.
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtWebEngineCore',
        'PySide6.QtMultimedia',
        'PySide6.QtPdf',
        'PySide6.Qt3DCore',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtOpenGL',
        'PySide6.QtSvg',
        'PySide6.QtPrintSupport',
        'PySide6.QtTest',
        'PySide6.QtSql',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --- Size pruning -----------------------------------------------------------
# The `excludes` above drop the PySide6 *Python* binding modules, but PyInstaller's
# PySide6 hook still collects the matching native Qt DLLs, every Qt plugin, and all
# translations. The app only uses QtCore/QtGui/QtWidgets/QtNetwork, so we strip the
# rest here. This takes the one-file exe from ~48 MB to ~27 MB. See the size notes
# in README/NOTICE if you change the imported Qt modules.

# 1. Native Qt DLLs the app never imports (QML/Quick/Pdf/OpenGL/Svg/VirtualKeyboard).
#    opengl32sw.dll is Qt's ~20 MB software-OpenGL fallback, unneeded for an opaque
#    Widgets UI. (If you reintroduce a WA_TranslucentBackground window, stop pruning
#    it — translucent compositing fails in the frozen build without this fallback.)
_DROP_QT_DLL = (
    'opengl32sw', 'Qt6Quick', 'Qt6Qml', 'Qt6Pdf', 'Qt6OpenGL',
    'Qt6Svg', 'Qt6VirtualKeyboard', 'Qt6QmlModels', 'Qt6QmlMeta',
    'Qt6QmlWorkerScript',
)
a.binaries = TOC([b for b in a.binaries
                  if not any(x.lower() in b[0].lower() for x in _DROP_QT_DLL)])

# 2. Accidental second OpenSSL build pulled from Git's mingw64\bin via PATH; it
#    duplicates Python's own libcrypto-3.dll/libssl-3.dll which _ssl/httpx use.
a.binaries = TOC([b for b in a.binaries
                  if 'mingw64' not in (b[1] or '').lower()
                  and b[0].split('\\')[-1].lower()
                      not in ('libcrypto-3-x64.dll', 'libssl-3-x64.dll')])

# 3. Image-format plugins the app never decodes. It loads PNG sprites/icons (PNG
#    support is built into Qt6Gui) and uses the .ico window icon, so keep only qico.
_DROP_IMG = (
    'qjpeg', 'qtiff', 'qgif', 'qwebp', 'qwbmp', 'qtga', 'qicns',
    'qsvg', 'qsvgicon', 'qpdf',
)
a.binaries = TOC([b for b in a.binaries
                  if not any(x in b[0].split('\\')[-1].lower() for x in _DROP_IMG)])

# 4. Extra Qt platform plugins; the Windows desktop build only needs qwindows.
_DROP_PLAT = ('qdirect2d.dll', 'qminimal.dll', 'qoffscreen.dll')
a.binaries = TOC([b for b in a.binaries
                  if b[0].split('\\')[-1].lower() not in _DROP_PLAT])

# 5. Qt's own UI translations (~6 MB of .qm). The app installs no QTranslator, so
#    these are never loaded; its UI strings are hardcoded English.
a.datas = [d for d in a.datas if 'translations' not in d[0].lower()]
# ---------------------------------------------------------------------------

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --- Windows version resource ------------------------------------------------
# Give Explorer's Properties > Details tab (and Task Manager / the SmartScreen
# prompt) a real File/Product version, name, and copyright. The version is
# parsed from APP_VERSION in src/app_settings.py so it always matches the in-app
# About box -- bump it there and rebuild; nothing here needs touching.
import re
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable,
    StringStruct, VarFileInfo, VarStruct,
)

with open('src/app_settings.py', encoding='utf-8') as _f:
    _m = re.search(r'APP_VERSION\s*=\s*["\']([0-9]+(?:\.[0-9]+)*)["\']', _f.read())
_ver_str = _m.group(1) if _m else '0.0.0'
_vtuple = tuple(([int(p) for p in _ver_str.split('.')] + [0, 0, 0, 0])[:4])

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_vtuple, prodvers=_vtuple),
    kids=[
        StringFileInfo([
            StringTable('040904B0', [
                StringStruct('CompanyName', 'Nick Welter'),
                StringStruct('FileDescription',
                             'Clawdmeter-Windows — Claude Code usage dashboard'),
                StringStruct('FileVersion', _ver_str),
                StringStruct('InternalName', 'Clawdmeter'),
                StringStruct('LegalCopyright',
                             '© 2026 Nick Welter · MIT licensed · '
                             'Clawd mascot © Anthropic PBC'),
                StringStruct('OriginalFilename', 'Clawdmeter.exe'),
                StringStruct('ProductName', 'Clawdmeter-Windows'),
                StringStruct('ProductVersion', _ver_str),
                StringStruct('Comments',
                             'Unofficial; not affiliated with Anthropic. '
                             'github.com/weltern/Clawdmeter-Windows'),
            ]),
        ]),
        VarFileInfo([VarStruct('Translation', [0x0409, 0x04B0])]),
    ],
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Clawdmeter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
    version=version_info,
)
