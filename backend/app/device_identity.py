from __future__ import annotations

import ctypes
import hashlib
import os
import secrets
from ctypes import wintypes
from pathlib import Path


SECRET_SIZE = 32
PRODUCT_NAMESPACE = b"AI_Customer/device/v1\0"
CRYPTPROTECT_UI_FORBIDDEN = 0x01
CRYPTPROTECT_LOCAL_MACHINE = 0x04


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def identity_path() -> Path:
    override = os.getenv("AI_CUSTOMER_DEVICE_IDENTITY_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    program_data = os.getenv("PROGRAMDATA", "").strip()
    if not program_data:
        raise RuntimeError("Windows 缺少 PROGRAMDATA，无法生成设备身份")
    return Path(program_data) / "AI_Customer" / "device_identity.bin"


def get_device_code(path: Path | None = None) -> str:
    # 设备码只暴露密钥摘要，不暴露 DPAPI 保护的随机密钥。
    secret = _load_or_create_secret(path or identity_path())
    digest = hashlib.sha256(PRODUCT_NAMESPACE + secret).hexdigest()[:32].upper()
    return "AI-CUS-" + "-".join(digest[index:index + 8] for index in range(0, 32, 8))


def _load_or_create_secret(path: Path) -> bytes:
    if path.is_file():
        try:
            secret = _unprotect(path.read_bytes())
            if len(secret) == SECRET_SIZE:
                return secret
        except OSError:
            pass
    # 外部机器的密文或损坏文件无法解密时，直接建立新的本机身份。
    secret = secrets.token_bytes(SECRET_SIZE)
    _write_protected_secret(path, secret)
    return secret


def _write_protected_secret(path: Path, secret: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再原子替换，避免进程中断留下半个身份文件。
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_bytes(_protect(secret))
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _input_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer


def _protect(data: bytes) -> bytes:
    crypt32, kernel32 = _windows_libraries()
    source, buffer = _input_blob(data)
    output = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "AI Customer device identity",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE,
        ctypes.byref(output),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
        del buffer


def _unprotect(data: bytes) -> bytes:
    crypt32, kernel32 = _windows_libraries()
    source, buffer = _input_blob(data)
    output = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
        del buffer


def _windows_libraries():
    if os.name != "nt":
        raise RuntimeError("设备身份仅支持 Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = (
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob), ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    )
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob), ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    )
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32
