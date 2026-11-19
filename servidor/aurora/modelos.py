"""Formato dos dados que chegam dos sensores, com validação rigorosa."""

from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from aurora.limites import FAIXAS_FISICAS

MAX_LEITURAS_POR_LOTE = 500
PADRAO_ID = r"^[A-Za-z0-9_.:-]+$"


def _faixa(metrica: str) -> dict[str, float]:
    minimo, maximo = FAIXAS_FISICAS[metrica]
    return {"ge": minimo, "le": maximo}


class LeituraEntrada(BaseModel):
    """Uma medição. Informe o horário de UMA das duas formas:

    - `medido_em`: data e hora com fuso (ex.: "2026-11-19T10:30:00-03:00"), ou
    - `idade_s`: há quantos segundos a medição foi feita. Útil quando o sensor
      não sabe a hora certa (sem internet/NTP): o servidor calcula o horário.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=False)

    id_leitura: str = Field(min_length=1, max_length=64, pattern=PADRAO_ID)
    medido_em: AwareDatetime | None = None
    idade_s: int | None = Field(default=None, ge=0)
    temperatura_c: float | None = Field(default=None, **_faixa("temperatura_c"))
    umidade_pct: float | None = Field(default=None, **_faixa("umidade_pct"))
    co2_ppm: float | None = Field(default=None, **_faixa("co2_ppm"))

    @model_validator(mode="after")
    def _coerente(self) -> LeituraEntrada:
        if (self.medido_em is None) == (self.idade_s is None):
            raise ValueError("informe exatamente um entre 'medido_em' e 'idade_s'")
        if self.temperatura_c is None and self.umidade_pct is None and self.co2_ppm is None:
            raise ValueError("a leitura não tem nenhuma medição")
        return self


class LoteEntrada(BaseModel):
    """Envelope do envio. Cada leitura é validada separadamente (ver servico.py),
    para que uma leitura com defeito não impeça as outras de serem guardadas."""

    model_config = ConfigDict(extra="forbid")

    leituras: list[Any] = Field(min_length=1, max_length=MAX_LEITURAS_POR_LOTE)


class Rejeicao(BaseModel):
    indice: int
    id_leitura: str | None
    motivo: str


class ResultadoLote(BaseModel):
    aceitas: int
    duplicadas: int
    rejeitadas: list[Rejeicao]
