# coding: utf-8
"""
AES-GCM 加解密 —— 剪贴板同步协议 7.3 的线格式实现。

线格式（与 Android 端必须逐字节一致）：
    key       : AES-256，32 字节。手机在 pair_req 里以 base64 明文发送一次，
                双方各自持久化保存。
    plaintext : 剪贴板文本的 UTF-8 字节。
    encrypt   : nonce = 12 字节真随机；
                blob  = nonce(12) || ciphertext || tag(16)；
                data  = base64(blob)  —— 即 clip 消息里的 "data" 字段值。
    decrypt   : base64 解码后按 12 / 中间 / 末16 字节切分，用 AES-GCM
                decrypt_and_verify 还原，tag 校验失败则整条消息丢弃。

注意：协议设计文档 7.3 只写了 "nonce(96bit) + ciphertext"，tag 的位置与拼接
顺序由本模块明确为 nonce || ciphertext || tag(16)。Android 实现必须采用
完全相同的拼接顺序，否则解密失败。
"""
from __future__ import annotations

try:
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes
    _AVAILABLE = True
except ImportError:  # pycryptodome 未安装：服务降级，剪贴板同步不可用
    _AVAILABLE = False

NONCE_LEN = 12
TAG_LEN = 16
KEY_LEN = 32


def available() -> bool:
    """AES-GCM 是否可用（pycryptodome 是否已安装）"""
    return _AVAILABLE


def _require():
    if not _AVAILABLE:
        raise RuntimeError("pycryptodome 未安装，AES-GCM 不可用（pip install pycryptodome）")


def encrypt(key: bytes, plaintext: bytes) -> bytes:
    """
    加密，返回 blob = nonce(12) + ciphertext + tag(16)。

    Args:
        key: 32 字节 AES-256 密钥
        plaintext: 明文字节（调用方负责 UTF-8 编码）
    """
    _require()
    if len(key) != KEY_LEN:
        raise ValueError(f"密钥长度必须为 {KEY_LEN} 字节，实际 {len(key)}")
    nonce = get_random_bytes(NONCE_LEN)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return nonce + ciphertext + tag


def decrypt(key: bytes, blob: bytes) -> bytes:
    """
    解密，tag 校验失败时抛出 ValueError。

    Args:
        key: 32 字节 AES-256 密钥
        blob: nonce(12) + ciphertext + tag(16)
    Returns:
        明文字节（调用方负责 UTF-8 解码）
    """
    _require()
    if len(key) != KEY_LEN:
        raise ValueError(f"密钥长度必须为 {KEY_LEN} 字节，实际 {len(key)}")
    if len(blob) < NONCE_LEN + TAG_LEN:
        raise ValueError(f"密文过短：{len(blob)} 字节")
    nonce = blob[:NONCE_LEN]
    tag = blob[-TAG_LEN:]
    ciphertext = blob[NONCE_LEN:-TAG_LEN]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ciphertext, tag)
