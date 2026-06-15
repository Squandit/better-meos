# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the operator app.

Build:  venv\\Scripts\\pyinstaller better-meos.spec
Output: dist\\better-meos.exe  (one file; double-click to run -- it starts the
server and opens the browser at the event start page).
"""

block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    # Bundle the templates + static assets (entry page, images, sw.js, css/js).
    datas=[('templates', 'templates'), ('static', 'static')],
    # Runtime-served deps + ones imported lazily that PyInstaller can't see.
    hiddenimports=['waitress', 'pyngrok'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='better-meos',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
