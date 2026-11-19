from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from aurora import db, servico
from aurora.api import criar_app
from aurora.config import Configuracao

# 19/11/2026 13:00 UTC = 10:00 em Brasília
AGORA = 1_795_093_200.0


class Relogio:
    def __init__(self, t: float = AGORA) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@dataclass
class Ambiente:
    cliente: TestClient
    cfg: Configuracao
    relogio: Relogio
    chave: str
    dispositivo: str = "sala-101"

    def enviar(self, leituras, chave: str | None = None, dispositivo: str | None = None):
        return self.cliente.post(
            "/api/v1/leituras",
            json={"leituras": leituras},
            headers={
                "X-Dispositivo": dispositivo or self.dispositivo,
                "X-Chave": self.chave if chave is None else chave,
            },
        )


def _montar(tmp_path, **extra) -> Ambiente:
    cfg = Configuracao(caminho_banco=str(tmp_path / "teste.db"), **extra)
    relogio = Relogio()
    app = criar_app(cfg, relogio=relogio)
    with db.abrir(cfg.caminho_banco) as conn:
        novo = servico.registrar_dispositivo(conn, "sala-101", "Sala 101", relogio())
    return Ambiente(TestClient(app, raise_server_exceptions=False), cfg, relogio, novo.chave)


@pytest.fixture
def amb(tmp_path) -> Ambiente:
    return _montar(tmp_path)


@pytest.fixture
def amb_protegido(tmp_path) -> Ambiente:
    return _montar(tmp_path, token_painel="segredo-do-painel-123")


def leitura(i: int = 0, **valores):
    base = {
        "id_leitura": f"b1-{i}",
        "idade_s": 0,
        "temperatura_c": 24.5,
        "umidade_pct": 55.0,
        "co2_ppm": 650.0,
    }
    base.update(valores)
    return {k: v for k, v in base.items() if v is not ...}
