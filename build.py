"""Build the Windows distribution with the installed Python environment."""
from pathlib import Path
import subprocess
import sys

base=Path(__file__).resolve().parent
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm',
    '--distpath',str(base/'dist'),'--workpath',str(base/'build'),str(base/'PremiereToAll.spec')],cwd=base,check=True)
