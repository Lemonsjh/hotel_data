"""本机 AES-GCM，只用于解开当前 Windows 用户自己的 Rezen 会话。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes


class _AuthInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.ULONG),
        ("dwInfoVersion", wintypes.ULONG),
        ("pbNonce", ctypes.c_void_p),
        ("cbNonce", wintypes.ULONG),
        ("pbAuthData", ctypes.c_void_p),
        ("cbAuthData", wintypes.ULONG),
        ("pbTag", ctypes.c_void_p),
        ("cbTag", wintypes.ULONG),
        ("pbMacContext", ctypes.c_void_p),
        ("cbMacContext", wintypes.ULONG),
        ("cbAAD", wintypes.ULONG),
        ("cbData", ctypes.c_ulonglong),
        ("dwFlags", wintypes.ULONG),
    ]


def _bcrypt():
    bcrypt = ctypes.WinDLL("bcrypt", use_last_error=True)
    void = ctypes.c_void_p
    ulong = wintypes.ULONG
    bcrypt.BCryptOpenAlgorithmProvider.argtypes = [ctypes.POINTER(void), wintypes.LPCWSTR, wintypes.LPCWSTR, ulong]
    bcrypt.BCryptOpenAlgorithmProvider.restype = ulong
    bcrypt.BCryptCloseAlgorithmProvider.argtypes = [void, ulong]
    bcrypt.BCryptCloseAlgorithmProvider.restype = ulong
    bcrypt.BCryptSetProperty.argtypes = [void, wintypes.LPCWSTR, void, ulong, ulong]
    bcrypt.BCryptSetProperty.restype = ulong
    bcrypt.BCryptGenerateSymmetricKey.argtypes = [void, ctypes.POINTER(void), void, ulong, void, ulong, ulong]
    bcrypt.BCryptGenerateSymmetricKey.restype = ulong
    bcrypt.BCryptDestroyKey.argtypes = [void]
    bcrypt.BCryptDestroyKey.restype = ulong
    operation = [void, void, ulong, void, void, ulong, void, ulong, ctypes.POINTER(ulong), ulong]
    bcrypt.BCryptEncrypt.argtypes = operation
    bcrypt.BCryptEncrypt.restype = ulong
    bcrypt.BCryptDecrypt.argtypes = operation
    bcrypt.BCryptDecrypt.restype = ulong
    return bcrypt


def _status(code: int, action: str) -> None:
    if code != 0:
        raise RuntimeError(f"本机{action}失败")


def _auth(nonce: bytes, tag: bytearray | None) -> _AuthInfo:
    info = _AuthInfo()
    info.cbSize = ctypes.sizeof(info)
    info.dwInfoVersion = 1
    info.pbNonce = ctypes.cast(nonce, ctypes.c_void_p).value
    info.cbNonce = len(nonce)
    if tag is not None:
        info.pbTag = ctypes.cast(tag, ctypes.c_void_p).value
        info.cbTag = len(tag)
    return info


def _transform(encrypt: bool, key: bytes, nonce: bytes, data: bytes, tag: bytes | None) -> tuple[bytes, bytes]:
    if len(nonce) != 12 or len(key) not in {16, 24, 32}:
        raise ValueError("会话解密参数无效")
    bcrypt = _bcrypt()
    algorithm = ctypes.c_void_p()
    handle = ctypes.c_void_p()
    _status(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(algorithm), "AES", None, 0), "初始化")
    try:
        mode = ctypes.create_unicode_buffer("ChainingModeGCM")
        _status(bcrypt.BCryptSetProperty(algorithm, "ChainingMode", mode, ctypes.sizeof(mode), 0), "初始化")
        secret = ctypes.create_string_buffer(key, len(key))
        _status(bcrypt.BCryptGenerateSymmetricKey(
            algorithm, ctypes.byref(handle), None, 0, secret, len(key), 0), "初始化")
        incoming = ctypes.create_string_buffer(data, len(data)) if data else None
        nonce_buffer = ctypes.create_string_buffer(nonce, len(nonce))
        tag_buffer = ctypes.create_string_buffer(16 if tag is None else bytes(tag), 16 if tag is None else len(tag))
        info = _auth(nonce_buffer, tag_buffer)
        written = wintypes.ULONG(0)
        operation = bcrypt.BCryptEncrypt if encrypt else bcrypt.BCryptDecrypt
        _status(operation(
            handle, incoming, len(data), ctypes.byref(info), None, 0,
            None, 0, ctypes.byref(written), 0), "解密" if not encrypt else "加密")
        output = ctypes.create_string_buffer(written.value)
        _status(operation(
            handle, incoming, len(data), ctypes.byref(info), None, 0,
            output, written.value, ctypes.byref(written), 0), "解密" if not encrypt else "加密")
        return output.raw[:written.value], tag_buffer.raw
    finally:
        if handle:
            bcrypt.BCryptDestroyKey(handle)
        bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)


def aes_gcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    return _transform(True, key, nonce, plaintext, None)


def aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes) -> bytes:
    if len(tag) != 16:
        raise ValueError("会话解密参数无效")
    plain, _tag = _transform(False, key, nonce, ciphertext, tag)
    return plain
