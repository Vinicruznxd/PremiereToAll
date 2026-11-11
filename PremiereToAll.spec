# PySide6 6.11 uses the Windows ICU API with unversioned symbols. PyInstaller
# can pick an incompatible ICU from another application on PATH (e.g. Poppler).
# Use Windows' own system ICU instead of redistributing/shadowing it.
from pathlib import Path

a = Analysis(['main.py'], pathex=[], binaries=[],
             datas=[('premieretoall/defaults.json','premieretoall'),('premieretoall/effect_catalog.json','premieretoall')],
             hiddenimports=[], hookspath=[], runtime_hooks=[], excludes=[], noarchive=False)
# This Widgets application does not use Qt PDF, QML or Virtual Keyboard.
# Do not ship their automatically collected plugins and transitive runtimes.
unused = {'icuuc.dll', 'icudt78.dll', 'qpdf.dll', 'qtvirtualkeyboardplugin.dll',
          'qt6pdf.dll', 'qt6virtualkeyboard.dll', 'qt6quick.dll', 'qt6qml.dll',
          'qt6qmlmeta.dll', 'qt6qmlmodels.dll', 'qt6qmlworkerscript.dll'}
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in unused]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PremiereToAll',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PremiereToAll')
