"""Chaves dos dispositivos.

A chave só é mostrada uma vez, na criação. No banco fica apenas o hash SHA-256 —
quem copiar o arquivo do banco não consegue se passar por um sensor. Como a chave é
longa e aleatória (256 bits), SHA-256 é suficiente (não é uma senha humana).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_HASH_FALSO = "0" * 64  # usado quando o dispositivo não existe, para o tempo de resposta ser igual


def gerar_chave() -> str:
    return secrets.token_urlsafe(32)


def hash_chave(chave: str) -> str:
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def chave_confere(chave: str | None, hash_guardado: str | None) -> bool:
    calculado = hash_chave(chave or "")
    esperado = hash_guardado or _HASH_FALSO
    iguais = hmac.compare_digest(calculado, esperado)
    return iguais and chave is not None and hash_guardado is not None


def token_confere(recebido: str | None, esperado: str) -> bool:
    if recebido is None:
        return False
    return hmac.compare_digest(recebido.encode("utf-8"), esperado.encode("utf-8"))
