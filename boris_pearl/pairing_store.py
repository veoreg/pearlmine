# SPDX-License-Identifier: Apache-2.0
"""User-bound Windows DPAPI for remembered pairing tokens; never plaintext export."""
import base64
import ctypes as C
import os
from pathlib import Path
import sys
from .remote_control import Pairing


def crypt(data,*,decrypt=False):
    if sys.platform!="win32":raise ValueError("На этой системе код подключения хранится только до закрытия окна.")
    class Blob(C.Structure):_fields_=[("size",C.c_uint),("data",C.POINTER(C.c_ubyte))]
    root=Path(os.environ.get("SystemRoot",r"C:\Windows"))/"System32"
    library=C.WinDLL(str(root/"crypt32.dll"),use_last_error=True);kernel=C.WinDLL(str(root/"kernel32.dll"))
    function=library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes=[C.POINTER(Blob),C.c_void_p if decrypt else C.c_wchar_p,C.c_void_p,C.c_void_p,C.c_void_p,C.c_uint,C.POINTER(Blob)]
    function.restype=C.c_int;kernel.LocalFree.argtypes=[C.c_void_p];kernel.LocalFree.restype=C.c_void_p
    raw=(C.c_ubyte*len(data)).from_buffer_copy(data);source=Blob(len(data),raw);result=Blob()
    if not function(C.byref(source),None if decrypt else "PearlMine pairing",None,None,None,1,C.byref(result)):
        raise OSError("Не удалось открыть защищённый код подключения для этого пользователя Windows.")
    try:return C.string_at(result.data,result.size)
    finally:kernel.LocalFree(C.cast(result.data,C.c_void_p))


def seal(pairing):
    return dict(name=pairing.name,url=pairing.url,fingerprint=pairing.fingerprint,
        protected_token=base64.b64encode(crypt(pairing.token.encode("ascii"))).decode("ascii"))


def unseal(record):
    if set(record)!={"name","url","fingerprint","protected_token"}:raise ValueError("Неверный сохранённый компьютер.")
    encoded=record["protected_token"]
    if not isinstance(encoded,str) or len(encoded)>16384:raise ValueError("Неверный защищённый код.")
    token=crypt(base64.b64decode(encoded,validate=True),decrypt=True).decode("ascii")
    return Pairing(record["name"],record["url"],record["fingerprint"],token)
