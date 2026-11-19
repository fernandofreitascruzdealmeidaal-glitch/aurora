"""Painel: situação, séries, resumo, padrões, CSV, token."""

from __future__ import annotations

from tests.conftest import AGORA, leitura


def _preencher(amb, n=60, passo_s=60, co2=lambda i: 600 + 10 * i):
    lote = [
        leitura(i, idade_s=i * passo_s, co2_ppm=co2(i), temperatura_c=22 + (i % 5) / 10)
        for i in range(n)
    ]
    assert amb.enviar(lote).json()["aceitas"] == n


def test_dispositivos_online_com_avisos(amb):
    amb.enviar([leitura(1, co2_ppm=1600, temperatura_c=24)])
    [d] = amb.cliente.get("/api/v1/dispositivos").json()
    assert d["id"] == "sala-101" and d["online"] is True
    assert d["ultima_leitura"]["co2_ppm"] == 1600
    [aviso] = d["avisos"]
    assert aviso["metrica"] == "co2_ppm" and aviso["nivel"] == "critico"
    assert "janelas" in aviso["mensagem"]


def test_dispositivo_fica_offline(amb):
    amb.enviar([leitura()])
    amb.relogio.t += 3600
    [d] = amb.cliente.get("/api/v1/dispositivos").json()
    assert d["online"] is False


def test_dispositivo_sem_dados(amb):
    [d] = amb.cliente.get("/api/v1/dispositivos").json()
    assert d["online"] is False and d["ultima_leitura"] is None and d["avisos"] == []


def test_serie_agrupa_por_intervalo(amb):
    _preencher(amb, n=60)
    r = amb.cliente.get("/api/v1/dispositivos/sala-101/serie?horas=2&intervalo_min=10").json()
    assert r["intervalo_s"] == 600
    assert sum(p["n"] for p in r["pontos"]) == 60
    p = r["pontos"][0]
    assert p["co2_ppm"]["min"] <= p["co2_ppm"]["media"] <= p["co2_ppm"]["max"]


def test_serie_limita_pontos(amb):
    r = amb.cliente.get("/api/v1/dispositivos/sala-101/serie?horas=744&intervalo_min=1").json()
    assert 744 * 3600 / r["intervalo_s"] <= 2000


def test_serie_ignora_metrica_ausente(amb):
    amb.enviar([{"id_leitura": "x", "idade_s": 0, "co2_ppm": 700}])
    [p] = amb.cliente.get("/api/v1/dispositivos/sala-101/serie").json()["pontos"]
    assert p["temperatura_c"] is None and p["co2_ppm"]["media"] == 700


def test_resumo(amb):
    _preencher(amb, n=60)  # co2 de 600 a 1190 → 7 acima de 1120 (1130…1190)
    r = amb.cliente.get("/api/v1/dispositivos/sala-101/resumo?horas=2").json()
    co2 = r["metricas"]["co2_ppm"]
    assert r["leituras"] == 60
    assert co2["min"] == 600 and co2["max"] == 1190 and co2["leituras"] == 60
    assert co2["fora_da_referencia_pct"] == round(100 * 7 / 60, 1)


def test_resumo_vazio(amb):
    r = amb.cliente.get("/api/v1/dispositivos/sala-101/resumo").json()
    assert r["leituras"] == 0 and r["metricas"]["co2_ppm"] is None


def test_padroes_por_hora_local(amb):
    # AGORA = 10:00 em Brasília. Leituras de 1 h atrás → hora 9.
    amb.enviar([leitura(i, idade_s=3600 - i * 60, co2_ppm=900) for i in range(5)])
    r = amb.cliente.get("/api/v1/dispositivos/sala-101/padroes?dias=1").json()
    assert r["fuso"] == "America/Sao_Paulo"
    horas = {h["hora"]: h for h in r["horas"]}
    assert len(horas) == 24
    assert horas[9]["co2_ppm"] == 900 and horas[9]["leituras"] == 5
    assert horas[3]["co2_ppm"] is None and horas[3]["leituras"] == 0


def test_leituras_brutas_mais_recentes_primeiro(amb):
    _preencher(amb, n=10)
    r = amb.cliente.get("/api/v1/dispositivos/sala-101/leituras?limite=3").json()
    assert len(r) == 3
    assert r[0]["medido_em"] > r[1]["medido_em"] > r[2]["medido_em"]
    assert r[0]["medido_em"].endswith("Z")


def test_exportar_csv(amb):
    _preencher(amb, n=3)
    r = amb.cliente.get("/api/v1/dispositivos/sala-101/exportar.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    linhas = r.text.lstrip("﻿").strip().splitlines()
    assert linhas[0] == "medido_em_utc;medido_em_local;temperatura_c;umidade_pct;co2_ppm"
    assert len(linhas) == 4
    assert linhas[1].split(";")[1].startswith("2026-11-19 09:5")  # hora de Brasília


def test_dispositivo_inexistente_404(amb):
    for rota in ("serie", "resumo", "padroes", "leituras", "exportar.csv"):
        r = amb.cliente.get(f"/api/v1/dispositivos/nao-existe/{rota}")
        assert r.status_code == 404, rota


def test_parametros_fora_da_faixa(amb):
    assert amb.cliente.get("/api/v1/dispositivos/sala-101/serie?horas=0").status_code == 422
    assert amb.cliente.get("/api/v1/dispositivos/sala-101/serie?horas=99999").status_code == 422
    assert amb.cliente.get("/api/v1/dispositivos/sala-101/leituras?limite=0").status_code == 422
    assert amb.cliente.get("/api/v1/dispositivos/SALA!/resumo").status_code == 422


def test_referencias(amb):
    r = amb.cliente.get("/api/v1/referencias").json()
    assert "não são diagnóstico" in r["aviso"].lower() or "Não são diagnóstico" in r["aviso"]
    assert r["metricas"]["co2_ppm"]["atencao_acima"] == 1120


def test_saude(amb):
    r = amb.cliente.get("/saude")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_painel_html_e_cabecalhos(amb):
    r = amb.cliente.get("/")
    assert r.status_code == 200 and "Aurora" in r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert amb.cliente.get("/static/painel.js").status_code == 200
    assert amb.cliente.get("/static/painel.css").status_code == 200


# ------------------------------------------------------------------ token do painel


def test_painel_protegido_exige_token(amb_protegido):
    c = amb_protegido.cliente
    assert c.get("/api/v1/dispositivos").status_code == 401
    assert (
        c.get("/api/v1/dispositivos", headers={"Authorization": "Bearer errado"}).status_code == 401
    )
    assert c.get("/api/v1/dispositivos", headers={"Authorization": "Basic xyz"}).status_code == 401
    ok = c.get("/api/v1/dispositivos", headers={"Authorization": "Bearer segredo-do-painel-123"})
    assert ok.status_code == 200


def test_token_do_painel_nao_bloqueia_sensores_nem_saude(amb_protegido):
    assert amb_protegido.enviar([leitura()]).status_code == 200
    assert amb_protegido.cliente.get("/saude").status_code == 200
    assert amb_protegido.cliente.get("/api/v1/referencias").status_code == 200


def test_erro_inesperado_vira_500_sem_vazar_detalhes(amb, monkeypatch):
    from aurora import servico

    def explode(*_a, **_k):
        raise RuntimeError("segredo interno")

    monkeypatch.setattr(servico, "status_dispositivos", explode)
    r = amb.cliente.get("/api/v1/dispositivos")
    assert r.status_code == 500
    assert "segredo" not in r.text


def test_saude_detecta_banco_corrompido(amb, monkeypatch):
    from aurora import db

    monkeypatch.setattr(db, "verificar", lambda _c: False)
    assert amb.cliente.get("/saude").status_code == 503


def test_agora_fixo(amb):
    assert amb.relogio() == AGORA
