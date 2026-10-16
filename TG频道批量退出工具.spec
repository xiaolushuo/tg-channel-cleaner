# -*- mode: python ; coding: utf-8 -*-

# 自动从源码读取版本号，让产物文件名带版本（如 TG频道批量退出工具.v1.7.6.exe）
import re as _re
with open('tg_channel_leave.py', encoding='utf-8') as _f:
    _src = _f.read()
APP_VERSION = _re.search(r'APP_VERSION\s*=\s*"([^"]+)"', _src).group(1)
EXE_NAME = 'TG频道批量退出工具.%s' % APP_VERSION

a = Analysis(
    ['tg_channel_leave.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['telethon.tl.types', 'telethon.tl.functions', 'telethon.tl.custom', 'telethon.errors', 'telethon.crypto', 'python_socks', 'python_socks.async_.asyncio', 'python_socks.async_.asyncio._proxy', 'python_socks.async_.asyncio._stream'],
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
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
