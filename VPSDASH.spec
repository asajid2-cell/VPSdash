# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('templates_web', 'templates_web'),
        ('defaults', 'defaults'),
        ('static', 'static'),
        ('assets', 'assets'),
    ],
    hiddenimports=['flask', 'jinja2', 'werkzeug', 'sqlalchemy', 'psycopg', 'boto3'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VPSdash',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='assets/icons/vpsdash_app.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='VPSdash',
)
