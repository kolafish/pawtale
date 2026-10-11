"""Signature checks and 安全模式 AES for the mini program message push (消息推送)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


# WeChat pads to the 32-byte key size, not the 16-byte AES block.
_PAD_BLOCK = 32


class WeChatMessageError(Exception):
    pass


def push_signature(*parts: str) -> str:
    """SHA1 of the parts sorted and joined: signature (token, timestamp, nonce) or msg_signature (+ Encrypt)."""
    return hashlib.sha1("".join(sorted(parts)).encode("utf-8")).hexdigest()


def signature_matches(expected: str, *parts: str) -> bool:
    return hmac.compare_digest(push_signature(*parts), expected or "")


def _cipher(encoding_aes_key: str) -> Cipher:
    key = base64.b64decode(encoding_aes_key + "=")
    if len(key) != 32:
        raise WeChatMessageError("EncodingAESKey 格式错误")
    return Cipher(algorithms.AES(key), modes.CBC(key[:16]))


def decrypt_message(encoding_aes_key: str, encrypted: str, appid: str) -> str:
    decryptor = _cipher(encoding_aes_key).decryptor()
    try:
        padded = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
    except ValueError as exc:
        raise WeChatMessageError("消息解密失败") from exc
    pad = padded[-1] if padded else 0
    if not 1 <= pad <= _PAD_BLOCK:
        raise WeChatMessageError("消息解密失败")
    full = padded[:-pad]
    (length,) = struct.unpack(">I", full[16:20])
    message, sender_appid = full[20:20 + length], full[20 + length:]
    if sender_appid.decode("utf-8") != appid:
        raise WeChatMessageError("消息 AppID 不匹配")
    return message.decode("utf-8")


def encrypt_message(encoding_aes_key: str, message: str, appid: str, *, random_prefix: bytes | None = None) -> str:
    body = message.encode("utf-8")
    full = (random_prefix or os.urandom(16)) + struct.pack(">I", len(body)) + body + appid.encode("utf-8")
    pad = _PAD_BLOCK - len(full) % _PAD_BLOCK
    encryptor = _cipher(encoding_aes_key).encryptor()
    return base64.b64encode(encryptor.update(full + bytes([pad]) * pad) + encryptor.finalize()).decode("ascii")


def encrypted_reply(
    *, token: str, encoding_aes_key: str, appid: str, message: str, timestamp: int, nonce: str,
    random_prefix: bytes | None = None,
) -> dict:
    encrypted = encrypt_message(encoding_aes_key, message, appid, random_prefix=random_prefix)
    return {
        "Encrypt": encrypted,
        "MsgSignature": push_signature(token, str(timestamp), nonce, encrypted),
        "TimeStamp": timestamp,
        "Nonce": nonce,
    }
