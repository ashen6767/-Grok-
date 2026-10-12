# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).resolve()
a = Analysis(
    [str(ROOT / 'app.py')],
    pathex=[str(ROOT)], binaries=[], datas=[(str(ROOT / 'extension'), 'extension'),
                                            (str(ROOT / 'assets' / 'app_icon.ico'), 'assets'),
                                            (str(ROOT / 'assets' / 'app_icon.png'), 'assets'),
                                            (str(ROOT / 'assets' / 'brand_sky.png'), 'assets'),
                                            (str(ROOT / 'assets' / 'brand_sun.png'), 'assets'),
                                            (str(ROOT / 'assets' / 'browser_extension_tutorial.png'), 'assets')], hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtNetwork', 'PySide6.QtOpenGL', 'PySide6.QtPdf', 'PySide6.QtQml',
              'PySide6.QtQuick', 'PySide6.QtSvg', 'PySide6.QtVirtualKeyboard'],
    noarchive=False, optimize=0,
)

unused = {
    'qt6network.dll', 'qt6opengl.dll', 'qt6pdf.dll', 'qt6qml.dll', 'qt6qmlmeta.dll',
    'qt6qmlmodels.dll', 'qt6qmlworkerscript.dll', 'qt6quick.dll', 'qt6svg.dll',
    'qt6virtualkeyboard.dll', 'opengl32sw.dll', 'qtnetwork.pyd', 'qtsvg.pyd',
}
def keep(entry):
    path = entry[0].replace('\\', '/').lower(); name = path.rsplit('/', 1)[-1]
    if name in {'icuuc.dll', 'icudt78.dll'}: return False
    if path.startswith('pyside6/'):
        if name in unused: return False
        if path.startswith(('pyside6/plugins/generic/', 'pyside6/plugins/networkinformation/',
                            'pyside6/plugins/platforminputcontexts/', 'pyside6/plugins/tls/')): return False
        if path.startswith('pyside6/plugins/imageformats/') and name not in {
            'qico.dll', 'qjpeg.dll', 'qgif.dll', 'qtiff.dll', 'qwebp.dll', 'qwbmp.dll', 'qtga.dll'
        }: return False
        if path.startswith('pyside6/plugins/platforms/') and name not in {'qwindows.dll', 'qoffscreen.dll'}: return False
        if path.startswith('pyside6/translations/') and not name.endswith(('_zh_cn.qm', '_en.qm')): return False
    return True
a.binaries[:] = [e for e in a.binaries if keep(e)]
a.datas[:] = [e for e in a.datas if keep(e)]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='老七的Grok视频助手', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, runtime_tmpdir=None,
          console=False, disable_windowed_traceback=False,
          icon=str(ROOT / 'assets' / 'app_icon.ico'), version=str(ROOT / 'version_info.txt'))
