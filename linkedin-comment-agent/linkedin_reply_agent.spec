# -*- mode: python ; coding: utf-8 -*-
import os
import sys

# Get the directory containing the spec file
spec_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()

# Collect local browsers if they exist in the workspace under .local-browsers or ms-playwright
datas = []
for folder in ['.local-browsers', 'ms-playwright']:
    local_browsers = os.path.join(spec_dir, folder)
    if os.path.exists(local_browsers):
        datas.append((local_browsers, 'ms-playwright'))
        break

# Collect .env file if it exists in the workspace (packaged inside the executable)
env_file = os.path.join(spec_dir, '.env')
if os.path.exists(env_file):
    datas.append((env_file, '.'))

# Collect compiled static frontend assets
frontend_dir = os.path.join(spec_dir, 'frontend', 'out')
if os.path.exists(frontend_dir):
    datas.append((frontend_dir, 'frontend/out'))


a = Analysis(
    ['linkedin_reply_agent.py'],
    pathex=[spec_dir],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'gspread',
        'google.oauth2.service_account',
        'google.auth',
        'pydantic_settings',
    ],
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
    name='linkedin_reply_agent',
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
