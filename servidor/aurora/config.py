"""Configuração lida de variáveis de ambiente, com valores padrão seguros.

Todas as variáveis começam com AURORA_. Um valor inválido derruba o servidor na
partida com uma mensagem clara — melhor falhar cedo do que rodar mal configurado.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ErroDeConfiguracao(ValueError):
    """Uma variável de ambiente tem valor inválido."""


def _inteiro(nome: str, padrao: int, minimo: int, maximo: int) -> int:
    bruto = os.environ.get(nome)
    if bruto is None or bruto.strip() == "":
        return padrao
    try:
        valor = int(bruto)
    except ValueError as exc:
        raise ErroDeConfiguracao(
            f"{nome} precisa ser um número inteiro (recebi {bruto!r})"
        ) from exc
    if not minimo <= valor <= maximo:
        raise ErroDeConfiguracao(f"{nome} precisa estar entre {minimo} e {maximo} (recebi {valor})")
    return valor


@dataclass(frozen=True)
class Configuracao:
    caminho_banco: str = "aurora.db"
    token_painel: str | None = None  # se definido, o painel das salas exige esse token
    token_admin: str | None = None  # sem ele, a área da equipe fica desligada
    fuso: str = "America/Sao_Paulo"
    max_bytes_requisicao: int = 1_000_000
    tolerancia_futuro_s: int = 300  # relógio do dispositivo pode estar até 5 min adiantado
    idade_maxima_s: int = 7 * 24 * 3600  # leituras com mais de 7 dias são recusadas
    intervalo_esperado_s: int = 60  # de quanto em quanto tempo o sensor envia
    coleta_a_cada_min: int = 0  # 0 = coleta automática desligada (use `aurora coletar`)
    # Chat com o Claude (desligado sem chave). A chave vem da variável ou do arquivo do painel.
    claude_chave: str | None = None
    claude_arquivo: str | None = None
    claude_modelo: str = "claude-haiku-4-5"
    claude_limite_dia: int = 150  # mensagens por dia, somando todo mundo
    claude_limite_visitante: int = 15  # mensagens por aparelho a cada 10 minutos

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.fuso)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ErroDeConfiguracao(f"Fuso horário desconhecido: {self.fuso!r}") from exc
        if self.token_painel is not None and len(self.token_painel) < 12:
            raise ErroDeConfiguracao("AURORA_TOKEN_PAINEL precisa ter pelo menos 12 caracteres")
        if self.token_admin is not None and len(self.token_admin) < 16:
            raise ErroDeConfiguracao("AURORA_TOKEN_ADMIN precisa ter pelo menos 16 caracteres")
        if self.coleta_a_cada_min and not 15 <= self.coleta_a_cada_min <= 1440:
            raise ErroDeConfiguracao("AURORA_COLETA_MIN deve ser 0 (desligado) ou de 15 a 1440")
        if self.token_admin is not None and self.token_admin == self.token_painel:
            raise ErroDeConfiguracao("AURORA_TOKEN_ADMIN deve ser diferente de AURORA_TOKEN_PAINEL")

    @classmethod
    def do_ambiente(cls) -> Configuracao:
        token = os.environ.get("AURORA_TOKEN_PAINEL") or None
        return cls(
            caminho_banco=os.environ.get("AURORA_BANCO", "aurora.db"),
            token_painel=token,
            token_admin=os.environ.get("AURORA_TOKEN_ADMIN") or None,
            fuso=os.environ.get("AURORA_FUSO", "America/Sao_Paulo"),
            max_bytes_requisicao=_inteiro("AURORA_MAX_BYTES", 1_000_000, 1_000, 10_000_000),
            tolerancia_futuro_s=_inteiro("AURORA_TOLERANCIA_FUTURO_S", 300, 0, 3600),
            idade_maxima_s=_inteiro("AURORA_IDADE_MAXIMA_S", 7 * 24 * 3600, 60, 90 * 24 * 3600),
            intervalo_esperado_s=_inteiro("AURORA_INTERVALO_S", 60, 5, 3600),
            coleta_a_cada_min=_inteiro("AURORA_COLETA_MIN", 0, 0, 1440),
            claude_chave=os.environ.get("AURORA_CLAUDE_CHAVE") or None,
            claude_arquivo=os.environ.get("AURORA_CLAUDE_ARQUIVO") or None,
            claude_modelo=os.environ.get("AURORA_CLAUDE_MODELO") or "claude-haiku-4-5",
            claude_limite_dia=_inteiro("AURORA_CLAUDE_LIMITE_DIA", 150, 0, 10_000),
            claude_limite_visitante=_inteiro("AURORA_CLAUDE_LIMITE_VISITANTE", 15, 1, 200),
        )
