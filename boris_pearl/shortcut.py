# SPDX-License-Identifier: Apache-2.0
"""First-run convenience for the standalone build: Desktop and Start Menu shortcuts, nothing else."""
import os
from pathlib import Path
import subprocess
import sys

SCRIPT=r"""
$shell=New-Object -ComObject WScript.Shell
foreach ($folder in [Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs')) {
  $link=$shell.CreateShortcut((Join-Path $folder 'PearlMine.lnk'))
  $link.TargetPath=$env:PEARLMINE_EXE
  $link.WorkingDirectory=Split-Path $env:PEARLMINE_EXE
  $link.IconLocation=$env:PEARLMINE_EXE
  $link.Description='PearlMine'
  $link.Save()
}
"""


def create_once(state_dir):
    """Runs once per copy of the program. A moved copy repairs the links on its own next start."""
    if sys.platform!="win32" or not getattr(sys,"frozen",False):return False
    exe=str(Path(sys.executable).resolve())
    marker=Path(state_dir)/"shortcut-target.txt"
    try:
        if marker.is_file() and marker.read_text(encoding="utf-8")==exe:return False
        subprocess.run(["powershell","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-Command",SCRIPT],
            env=dict(os.environ,PEARLMINE_EXE=exe),creationflags=subprocess.CREATE_NO_WINDOW,timeout=20,check=True,capture_output=True)
        marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text(exe,encoding="utf-8")
        return True
    except (OSError,subprocess.SubprocessError):return False
