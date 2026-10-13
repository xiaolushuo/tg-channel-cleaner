# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['tg_channel_leave.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['telethon.tl.types', 'telethon.tl.functions', 'telethon.tl.functions.channels', 'telethon.tl.functions.messages', 'telethon.tl.custom', 'telethon.errors', 'telethon.crypto', 'python_socks', 'python_socks.async_.asyncio', 'python_socks.async_.asyncio._proxy', 'python_socks.async_.asyncio._stream'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TG频道批量退出工具_调试版',
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
