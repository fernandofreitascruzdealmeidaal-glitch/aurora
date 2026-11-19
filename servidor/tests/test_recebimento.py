"""Envio de leituras: autenticação, validação, duplicidade, limites."""

from __future__ import annotations

import pytest

from aurora import db
from tests.conftest import AGORA, leitura


def _contar(amb) -> int:
    with db.abrir(amb.cfg.caminho_banco) as conn:
        return conn.execute("SELECT COUNT(*) FROM leituras").fetchone()[0]


def test_aceita_lote_valido(amb):
    r = amb.enviar([leitura(i) for i in range(3)])
    assert r.status_code == 200
    assert r.json() == {"aceitas": 3, "duplicadas": 0, "rejeitadas": []}
    assert _contar(amb) == 3


def test_reenvio_nao_duplica(amb):
    lote = [leitura(i) for i in range(5)]
    amb.enviar(lote)
    r = amb.enviar(lote)
    assert r.json()["aceitas"] == 0
    assert r.json()["duplicadas"] == 5
    assert _contar(amb) == 5


def test_duplicata_dentro_do_mesmo_lote(amb):
    r = amb.enviar([leitura(1), leitura(1)])
    assert r.json()["aceitas"] == 1 and r.json()["duplicadas"] == 1


@pytest.mark.parametrize(
    ("cabecalhos", "descricao"),
    [
        ({}, "sem cabeçalhos"),
        ({"X-Dispositivo": "sala-101"}, "sem chave"),
        ({"X-Dispositivo": "sala-101", "X-Chave": "errada"}, "chave errada"),
        ({"X-Dispositivo": "nao-existe", "X-Chave": "qualquer"}, "dispositivo inexistente"),
    ],
)
def test_recusa_sem_autenticacao(amb, cabecalhos, descricao):
    r = amb.cliente.post("/api/v1/leituras", json={"leituras": [leitura()]}, headers=cabecalhos)
    assert r.status_code == 401, descricao
    assert _contar(amb) == 0


def test_chave_de_outro_dispositivo_nao_serve(amb):
    from aurora import servico

    with db.abrir(amb.cfg.caminho_banco) as conn:
        outro = servico.registrar_dispositivo(conn, "sala-102", "Sala 102", AGORA)
    r = amb.enviar([leitura()], chave=outro.chave)  # chave da 102 usada como 101
    assert r.status_code == 401


@pytest.mark.parametrize(
    "ruim",
    [
        {"co2_ppm": 99999},  # impossível para o sensor
        {"co2_ppm": 10},
        {"umidade_pct": 120},
        {"temperatura_c": -80},
        {"temperatura_c": "quente"},
        {"temperatura_c": None, "umidade_pct": None, "co2_ppm": None},  # nada medido
        {"id_leitura": ""},
        {"id_leitura": "tem espaço"},
        {"id_leitura": "x" * 65},
        {"idade_s": -5},
        {"medido_em": "2026-11-19T10:00:00"},  # sem fuso: ambíguo
        {"medido_em": "2026-11-19T13:00:00Z"},  # medido_em E idade_s juntos
        {"campo_desconhecido": 1},
    ],
)
def test_recusa_leitura_invalida_mas_aceita_as_outras(amb, ruim):
    r = amb.enviar([leitura(1), leitura(2, **ruim), leitura(3)])
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["aceitas"] == 2
    assert len(corpo["rejeitadas"]) == 1
    assert corpo["rejeitadas"][0]["indice"] == 1
    assert corpo["rejeitadas"][0]["motivo"]


def test_recusa_nan_e_infinito(amb):
    corpo = (
        '{"leituras": [{"id_leitura": "a", "idade_s": 0, "co2_ppm": NaN},'
        ' {"id_leitura": "b", "idade_s": 0, "co2_ppm": Infinity}]}'
    )
    r = amb.cliente.post(
        "/api/v1/leituras",
        content=corpo,
        headers={
            "X-Dispositivo": "sala-101",
            "X-Chave": amb.chave,
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200
    assert r.json()["aceitas"] == 0 and len(r.json()["rejeitadas"]) == 2


def test_item_que_nao_e_objeto(amb):
    r = amb.enviar([leitura(1), 42, "texto", None])
    assert r.json()["aceitas"] == 1
    assert [x["indice"] for x in r.json()["rejeitadas"]] == [1, 2, 3]


def test_horario_no_futuro_recusado(amb):
    r = amb.enviar([leitura(1, idade_s=..., medido_em="2026-11-19T14:00:00Z")])  # +1h
    assert r.json()["aceitas"] == 0
    assert "futuro" in r.json()["rejeitadas"][0]["motivo"]


def test_pequeno_adiantamento_tolerado(amb):
    r = amb.enviar([leitura(1, idade_s=..., medido_em="2026-11-19T13:02:00Z")])  # +2 min
    assert r.json()["aceitas"] == 1


def test_leitura_antiga_demais(amb):
    r = amb.enviar([leitura(1, idade_s=8 * 24 * 3600)])
    assert r.json()["aceitas"] == 0
    assert "antiga" in r.json()["rejeitadas"][0]["motivo"]


def test_idade_calcula_horario(amb):
    amb.enviar([leitura(1, idade_s=600)])
    with db.abrir(amb.cfg.caminho_banco) as conn:
        medido = conn.execute("SELECT medido_em FROM leituras").fetchone()[0]
    assert medido == int(AGORA) - 600


def test_medido_em_com_fuso_de_brasilia(amb):
    amb.enviar([leitura(1, idade_s=..., medido_em="2026-11-19T09:30:00-03:00")])
    with db.abrir(amb.cfg.caminho_banco) as conn:
        medido = conn.execute("SELECT medido_em FROM leituras").fetchone()[0]
    assert medido == int(AGORA) - 1800


def test_leitura_parcial_aceita(amb):
    r = amb.enviar([{"id_leitura": "so-co2", "idade_s": 0, "co2_ppm": 800}])
    assert r.json()["aceitas"] == 1


@pytest.mark.parametrize(
    ("corpo", "status"),
    [
        (b"isto nao e json", 400),
        (b"[" * 100_000, 400),  # aninhamento absurdo
        (b'{"leituras": []}', 422),
        (b'{"leituras": "x"}', 422),
        (b'{"outra": 1}', 422),
        (b"[]", 422),
    ],
)
def test_corpo_malformado(amb, corpo, status):
    r = amb.cliente.post(
        "/api/v1/leituras",
        content=corpo,
        headers={"X-Dispositivo": "sala-101", "X-Chave": amb.chave},
    )
    assert r.status_code == status


def test_lote_grande_demais(amb):
    r = amb.enviar([leitura(i) for i in range(501)])
    assert r.status_code in (413, 422)


def test_corpo_acima_do_limite_de_bytes(amb):
    r = amb.cliente.post(
        "/api/v1/leituras",
        content=b"x" * (amb.cfg.max_bytes_requisicao + 1),
        headers={"X-Dispositivo": "sala-101", "X-Chave": amb.chave},
    )
    assert r.status_code == 413


def test_content_length_invalido(amb):
    from aurora.api import _ler_corpo_limitado

    class Falso:
        def __init__(self) -> None:
            self.headers = {"content-length": "abc"}

    import asyncio

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as erro:
        asyncio.run(_ler_corpo_limitado(Falso(), 10))
    assert erro.value.status_code == 400


def test_corpo_em_pedacos_acima_do_limite(amb):
    def pedacos():
        for _ in range(20):
            yield b"x" * 100_000

    r = amb.cliente.post(
        "/api/v1/leituras",
        content=pedacos(),
        headers={"X-Dispositivo": "sala-101", "X-Chave": amb.chave},
    )
    assert r.status_code == 413


def test_atualiza_ultimo_contato(amb):
    amb.enviar([leitura()])
    with db.abrir(amb.cfg.caminho_banco) as conn:
        contato = conn.execute("SELECT ultimo_contato FROM dispositivos").fetchone()[0]
    assert contato == int(AGORA)
