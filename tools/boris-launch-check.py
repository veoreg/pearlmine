#!/usr/bin/env python3
"""Start a real launcher, wait for its visible window, screenshot, close it politely, report leftovers."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image

user32=ctypes.windll.user32
ctypes.windll.shcore.SetProcessDpiAwareness(1)
WM_CLOSE=0x10
HWND_TOPMOST,HWND_NOTOPMOST=-1,-2
NOSIZE_NOMOVE=0x3


def windows(prefix):
    found=[]
    @ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def visit(hwnd,_):
        if user32.IsWindowVisible(hwnd):
            buffer=ctypes.create_unicode_buffer(256);user32.GetWindowTextW(hwnd,buffer,256)
            if buffer.value.startswith(prefix):found.append((hwnd,buffer.value))
        return True
    user32.EnumWindows(visit,0);return found


def capture(hwnd):
    """Only this window's own pixels: other programs on the desktop can never end up in the picture."""
    gdi=ctypes.windll.gdi32
    rect=wintypes.RECT();user32.GetWindowRect(hwnd,ctypes.byref(rect));width,height=rect.right-rect.left,rect.bottom-rect.top
    for name in ("GetWindowDC","CreateCompatibleDC","CreateCompatibleBitmap","SelectObject"):
        getattr(user32 if name=="GetWindowDC" else gdi,name).restype=wintypes.HANDLE
    user32.GetWindowDC.argtypes=[wintypes.HWND];gdi.CreateCompatibleDC.argtypes=[wintypes.HANDLE]
    gdi.CreateCompatibleBitmap.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_int];gdi.SelectObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    gdi.GetBitmapBits.argtypes=[wintypes.HANDLE,ctypes.c_long,ctypes.c_void_p];gdi.DeleteObject.argtypes=[wintypes.HANDLE];gdi.DeleteDC.argtypes=[wintypes.HANDLE]
    user32.PrintWindow.argtypes=[wintypes.HWND,wintypes.HANDLE,wintypes.UINT];user32.ReleaseDC.argtypes=[wintypes.HWND,wintypes.HANDLE]
    source=user32.GetWindowDC(hwnd);target=gdi.CreateCompatibleDC(source);bitmap=gdi.CreateCompatibleBitmap(source,width,height);gdi.SelectObject(target,bitmap)
    user32.PrintWindow(hwnd,target,2)
    buffer=ctypes.create_string_buffer(width*height*4);gdi.GetBitmapBits(bitmap,len(buffer),buffer)
    gdi.DeleteObject(bitmap);gdi.DeleteDC(target);user32.ReleaseDC(hwnd,source)
    return Image.frombuffer("RGB",(width,height),buffer,"raw","BGRX",0,1)


def owned(root):
    script=f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{root.name}*' -or $_.CommandLine -match 'boris_pearl|PearlMine' }} | Where-Object {{ $_.CommandLine -notmatch 'boris-launch-check|Get-CimInstance' }} | ForEach-Object {{ $_.Name + ' ' + $_.ProcessId }}"
    return subprocess.run(["powershell","-NoProfile","-Command",script],capture_output=True,text=True).stdout.split("\n")


def main():
    launcher,title,shot=Path(sys.argv[1]).resolve(),sys.argv[2],sys.argv[3]
    args=sys.argv[4:]
    report=dict(launcher=launcher.name,args=args)
    if windows(title):raise SystemExit("A window with this title is already open; not touching it.")
    command=["cmd","/c",str(launcher),*args] if launcher.suffix==".cmd" else [str(launcher),*args]
    process=subprocess.Popen(command,cwd=launcher.parent)
    started=time.monotonic();hwnd=None
    while time.monotonic()-started<60:
        rows=windows(title)
        if rows:hwnd,report["title"]=rows[0];break
        time.sleep(.2)
    if not hwnd:
        report["window"]=False
        if process.poll() is None:process.terminate()
        print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(1)
    report["window"]=True;report["seconds_to_window"]=round(time.monotonic()-started,1)
    time.sleep(float(10))  # Let automatic discovery fill the table.
    capture(hwnd).save(shot)
    closed=time.monotonic();user32.PostMessageW(hwnd,WM_CLOSE,0,0)
    while time.monotonic()-closed<30 and windows(title):time.sleep(.2)
    report["closed_after_s"]=round(time.monotonic()-closed,1);report["window_gone"]=not windows(title)
    time.sleep(2);report["launcher_exit"]=process.poll()
    report["leftover_processes"]=[row.strip() for row in owned(launcher.parent) if row.strip()]
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
