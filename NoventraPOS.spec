# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

def get_migrations_datas():
    migration_datas = []
    base_dir = os.path.abspath('migrations')
    for root, dirs, files in os.walk(base_dir):
        if '__pycache__' in dirs:
            dirs.remove('__pycache__')
        for f in files:
            if f.endswith(('.pyc', '.pyo')):
                continue
            src = os.path.join(root, f)
            rel_dir = os.path.relpath(root, os.path.abspath('.'))
            migration_datas.append((src, rel_dir))
    return migration_datas

datas = [
    ('app/templates', 'app/templates'),
    ('app/static', 'app/static'),
] + get_migrations_datas() + collect_data_files('alembic') + collect_data_files('flask_migrate')

hiddenimports = [
    'waitress',
    'oracledb',
    'alembic',
    'alembic.context',
    'alembic.config',
    'alembic.runtime',
    'alembic.runtime.environment',
    'alembic.runtime.migration',
    'alembic.script',
    'alembic.script.base',
    'alembic.script.revision',
    'alembic.operations',
    'alembic.operations.ops',
    'alembic.operations.toimpl',
    'alembic.ddl',
    'alembic.ddl.impl',
    'alembic.ddl.oracle',
    'flask_migrate',
    'flask',
    'sqlalchemy',
    'sqlalchemy.dialects.oracle',
    'sqlalchemy.dialects.oracle.cx_oracle',
    'sqlalchemy.dialects.oracle.oracledb',
    'PIL',
    'reportlab',
    'openpyxl',
    'werkzeug',
    'jinja2',
    'logging',
    'logging.config',
    'logging.handlers',
    'click',
    'app',
    'app.models',
] + collect_submodules('app') + collect_submodules('cryptography') + collect_submodules('oracledb') + collect_submodules('flask_migrate') + collect_submodules('alembic')


a = Analysis(
    ['entrypoint.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'matplotlib', 'scipy', 'pandas', 'IPython'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NoventraPOS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='NoventraPOS',
)
