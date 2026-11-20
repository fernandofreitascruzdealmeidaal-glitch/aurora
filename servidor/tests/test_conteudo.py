"""Base de sustentabilidade: semente, filtros, curadoria, datas e área da equipe."""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from aurora import conteudo, db
from aurora.api import criar_app
from aurora.catalogo import REGIOES, TIPOS, normalizar, sobre_sustentabilidade
from aurora.config import Configuracao, ErroDeConfiguracao
from tests.conftest import AGORA, Relogio

HOJE = date(2026, 11, 19)
TOKEN = "token-da-equipe-aurora-123"
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Mutirao de plantio no Cerrado</title><link>https://exemplo.org/a</link>
<description>Voluntarios plantam mudas</description></item>
<item><title>Resultado do futebol</title><link>https://exemplo.org/b</link></item>
</channel></rss>"""


def baixador_falso(url: str) -> bytes:
    return RSS


@pytest.fixture
def equipe(tmp_path):
    cfg = Configuracao(caminho_banco=str(tmp_path / "e.db"), token_admin=TOKEN)
    app = criar_app(cfg, relogio=Relogio(), baixador=baixador_falso)
    cliente = TestClient(app, raise_server_exceptions=False)
    cliente.headers["Authorization"] = f"Bearer {TOKEN}"
    cliente.cfg = cfg  # type: ignore[attr-defined]
    return cliente


def _item(**k):
    base = {"tipo": "evento", "titulo": "Mutirão no parque", "regiao": "Zona da Mata"}
    base.update(k)
    return conteudo.ItemEntrada.model_validate(base)


# ------------------------------------------------------------------ semente


def test_semente_carrega_sozinha_e_nao_duplica(amb):
    with db.abrir(amb.cfg.caminho_banco) as conn:
        antes = conn.execute("SELECT COUNT(*) FROM itens").fetchone()[0]
        assert antes >= 40
        assert conteudo.importar_semente(conn, AGORA) == {
            "itens": 0,
            "datas": 0,
            "dicas": 0,
            "fontes": 0,
        }
        assert conn.execute("SELECT COUNT(*) FROM itens").fetchone()[0] == antes


def test_semente_tem_qualidade():
    dados = json.loads(conteudo.CAMINHO_SEMENTE.read_text(encoding="utf-8"))
    chaves = [i["chave"] for i in dados["itens"]]
    assert len(chaves) == len(set(chaves))
    for i in dados["itens"]:
        assert i["url"].startswith("https://") or i["url"].startswith("http://"), i["chave"]
        assert i["verificado_em"] and i["resumo"], i["chave"]
        conteudo.ItemEntrada.model_validate(
            {k: v for k, v in i.items() if k not in ("chave", "publicado_em")}
        )
    tipos = {i["tipo"] for i in dados["itens"]}
    assert {"feira_ciencias", "voluntariado", "vaga", "curso", "parque", "noticia"} <= tipos
    for d in dados["datas"]:
        conteudo.validar_regra(d["regra"])


# ------------------------------------------------------------------ público


def test_publico_so_ve_aprovados(amb):
    with db.abrir(amb.cfg.caminho_banco) as conn:
        pend = conteudo.criar_item(conn, _item(titulo="Item escondido xyz"), AGORA)
    r = amb.cliente.get("/api/v1/conteudo/itens?busca=escondido").json()
    assert r["total"] == 0
    assert amb.cliente.get(f"/api/v1/conteudo/itens/{pend}").status_code == 404


def test_item_publico_nao_expoe_campos_internos(amb):
    r = amb.cliente.get("/api/v1/conteudo/itens?limite=1").json()["itens"][0]
    assert "status" not in r and "chave" not in r and "busca" not in r
    assert amb.cliente.get(f"/api/v1/conteudo/itens/{r['id']}").json()["titulo"] == r["titulo"]


def test_busca_sem_acento_e_segura(amb):
    c = amb.cliente
    assert c.get("/api/v1/conteudo/itens?busca=ciencias").json()["total"] >= 5
    assert c.get("/api/v1/conteudo/itens?busca=CIÊNCIAS").json()["total"] >= 5
    assert c.get("/api/v1/conteudo/itens?busca=%25").json()["total"] == 0  # '%' não vira coringa
    assert c.get("/api/v1/conteudo/itens?busca=_").json()["total"] == 0
    assert c.get("/api/v1/conteudo/itens?busca=voluntariado").json()["total"] >= 5  # pelo tipo
    assert c.get("/api/v1/conteudo/itens?busca=x'; DROP TABLE itens;--").status_code == 200
    assert c.get("/api/v1/conteudo/itens").json()["total"] >= 40


def test_filtros(amb):
    c = amb.cliente
    feiras = c.get("/api/v1/conteudo/itens?tipo=feira_ciencias&limite=200").json()
    assert feiras["total"] >= 10 and all(i["tipo"] == "feira_ciencias" for i in feiras["itens"])
    mata = c.get("/api/v1/conteudo/itens?regiao=Zona da Mata&limite=200").json()["itens"]
    assert all(
        i["regiao"] in ("Zona da Mata", "Todo o estado", "On-line", "Nacional") for i in mata
    )
    assert any(i["regiao"] == "Zona da Mata" for i in mata)
    assert c.get("/api/v1/conteudo/itens?tipo=inexistente").status_code == 422
    assert c.get("/api/v1/conteudo/itens?regiao=Marte").status_code == 422
    assert c.get("/api/v1/conteudo/itens?limite=999").status_code == 422


def test_paginacao(amb):
    p1 = amb.cliente.get("/api/v1/conteudo/itens?limite=10&pagina=1").json()
    p2 = amb.cliente.get("/api/v1/conteudo/itens?limite=10&pagina=2").json()
    assert p1["total"] == p2["total"]
    assert not {i["id"] for i in p1["itens"]} & {i["id"] for i in p2["itens"]}


def test_futuros_e_passados(amb):
    with db.abrir(amb.cfg.caminho_banco) as conn:
        conteudo.criar_item(
            conn, _item(titulo="Feira antiga", inicio="2025-03-01"), AGORA, status="aprovado"
        )
        conteudo.criar_item(
            conn,
            _item(titulo="Feira longa", inicio="2026-11-01", fim="2026-11-30"),
            AGORA,
            status="aprovado",
        )
    fut = amb.cliente.get("/api/v1/conteudo/itens?quando=futuros&limite=200").json()["itens"]
    titulos = [i["titulo"] for i in fut]
    assert "Feira antiga" not in titulos
    assert "Feira longa" in titulos  # começou mas ainda não acabou
    datas = [i["inicio"] for i in fut if i["inicio"]]
    assert datas == sorted(datas)
    assert all(i["inicio"] is not None for i in fut[: len(datas)])  # sem data vão para o fim
    passados = amb.cliente.get("/api/v1/conteudo/itens?quando=passados").json()["itens"]
    assert [i["titulo"] for i in passados] == ["Feira antiga"]


def test_pagina_inicial(amb):
    r = amb.cliente.get("/api/v1/conteudo/inicio").json()
    assert r["hoje"] == "2026-11-19"
    assert r["proximos_eventos"] and all(i["inicio"] >= "2026-11-19" for i in r["proximos_eventos"])
    assert r["proximas_datas"][0]["nome"] == "Dia Mundial do Solo"
    assert r["proximas_datas"][0]["faltam_dias"] == 16
    assert r["noticias"] and r["oportunidades"] and r["dica_do_dia"]
    # o que estudantes podem fazer vem antes do que é só para adultos
    assert not any("adult" in (i["publico"] or "").lower() for i in r["oportunidades"])


def test_catalogo_e_dicas(amb):
    cat = amb.cliente.get("/api/v1/conteudo/catalogo").json()
    assert cat["tipos"] == TIPOS and cat["regioes"] == list(REGIOES)
    dicas = amb.cliente.get("/api/v1/conteudo/dicas").json()
    assert len(dicas) >= 15
    agua = amb.cliente.get("/api/v1/conteudo/dicas?categoria=Água").json()
    assert agua and all(d["categoria"] == "Água" for d in agua)


def test_paginas_do_site(amb):
    for rota, texto in (("/", "site.js"), ("/salas", "painel.js"), ("/equipe", "equipe.js")):
        r = amb.cliente.get(rota)
        assert r.status_code == 200 and texto in r.text, rota
    for arquivo in ("site.js", "site.css", "equipe.js"):
        assert amb.cliente.get(f"/static/{arquivo}").status_code == 200


# ------------------------------------------------------------------ datas


@pytest.mark.parametrize(
    ("regra", "a_partir", "esperado"),
    [
        ("fixa:12-05", date(2026, 11, 19), date(2026, 12, 5)),
        ("fixa:03-22", date(2026, 11, 19), date(2027, 3, 22)),
        ("fixa:11-19", date(2026, 11, 19), date(2026, 11, 19)),  # hoje conta
        ("ordinal:-1:sab:3", date(2026, 11, 19), date(2027, 3, 27)),  # Hora do Planeta 2027
        ("ordinal:3:sab:9", date(2026, 11, 19), date(2027, 9, 18)),  # Dia Mundial da Limpeza
        ("ordinal:3:sab:9", date(2026, 9, 1), date(2026, 9, 19)),
        ("fixa:02-29", date(2027, 3, 1), date(2028, 2, 29)),  # 29/02 só em ano bissexto
    ],
)
def test_regras_de_data(regra, a_partir, esperado):
    assert conteudo._proxima_ocorrencia(regra, a_partir) == esperado


def test_regra_invalida():
    with pytest.raises(conteudo.ErroDeConteudo):
        conteudo.validar_regra("lua-cheia")


def test_datas_api(amb):
    d = amb.cliente.get("/api/v1/conteudo/datas?dias=30").json()
    assert [x["nome"] for x in d] == ["Dia Mundial do Solo"]
    todas = amb.cliente.get("/api/v1/conteudo/datas").json()
    assert len(todas) >= 20 and todas == sorted(todas, key=lambda x: x["data"])


# ------------------------------------------------------------------ validação de itens


@pytest.mark.parametrize(
    "ruim",
    [
        {"tipo": "festa"},
        {"titulo": "ab"},
        {"url": "javascript:alert(1)"},
        {"url": "ftp://x.org/a"},
        {"regiao": "Marte"},
        {"inicio": "2026-11-10", "fim": "2026-11-01"},
        {"fim": "2026-11-01"},
        {"inicio": "2026-02-30"},
        {"campo_extra": 1},
        {"resumo": "x" * 1201},
    ],
)
def test_item_invalido(ruim):
    with pytest.raises(ValueError):
        _item(**ruim)


def test_texto_com_html_fica_como_texto(amb):
    with db.abrir(amb.cfg.caminho_banco) as conn:
        conteudo.criar_item(
            conn, _item(titulo="<script>alert(1)</script> teste"), AGORA, status="aprovado"
        )
    r = amb.cliente.get("/api/v1/conteudo/itens?busca=script").json()["itens"][0]
    assert r["titulo"] == "<script>alert(1)</script> teste"  # o site exibe com textContent


def test_normalizar_e_palavras_chave():
    assert normalizar("  Feira de  CIÊNCIAS ") == "feira de ciencias"
    assert normalizar(None) == ""
    assert sobre_sustentabilidade("Mutirão de reciclagem")
    assert sobre_sustentabilidade("título", "Projeto na Mata Atlântica")
    assert not sobre_sustentabilidade("Resultado do futebol")
    assert not sobre_sustentabilidade("Bolsa de valores sobe")


# ------------------------------------------------------------------ área da equipe


def test_equipe_desligada_sem_token_configurado(amb):
    r = amb.cliente.get("/api/v1/equipe/itens")
    assert r.status_code == 403 and "AURORA_TOKEN_ADMIN" in r.json()["detail"]


def test_equipe_exige_token(equipe):
    for cabecalho in ({}, {"Authorization": "Bearer errado-errado-errado"}, {"Authorization": "x"}):
        r = TestClient(equipe.app).get("/api/v1/equipe/itens", headers=cabecalho)
        assert r.status_code == 401


def test_fluxo_de_curadoria(equipe):
    novo = equipe.post(
        "/api/v1/equipe/itens?publicar=false",
        json={
            "tipo": "voluntariado",
            "titulo": "Plantio na escola Zeferino",
            "regiao": "Central",
            "url": "https://exemplo.org/plantio",
            "inicio": "2026-12-05",
        },
    )
    assert novo.status_code == 201
    item = novo.json()
    assert item["status"] == "pendente"
    assert equipe.get(f"/api/v1/conteudo/itens/{item['id']}").status_code == 404

    pend = equipe.get("/api/v1/equipe/itens?status=pendente").json()
    assert any(i["id"] == item["id"] for i in pend["itens"])

    r = equipe.patch(
        f"/api/v1/equipe/itens/{item['id']}", json={"status": "aprovado", "regiao": "Zona da Mata"}
    )
    assert r.status_code == 200 and r.json()["regiao"] == "Zona da Mata"
    assert equipe.get(f"/api/v1/conteudo/itens/{item['id']}").status_code == 200
    assert equipe.get("/api/v1/conteudo/itens?busca=zeferino plantio").json()["total"] == 1

    assert (
        equipe.patch(f"/api/v1/equipe/itens/{item['id']}", json={"fim": "2026-12-01"}).status_code
        == 400
    )  # fim antes do início
    assert (
        equipe.patch(f"/api/v1/equipe/itens/{item['id']}", json={"tipo": "festa"}).status_code
        == 400
    )
    assert equipe.patch(f"/api/v1/equipe/itens/{item['id']}", json={}).status_code == 200

    assert equipe.delete(f"/api/v1/equipe/itens/{item['id']}").status_code == 204
    assert equipe.delete(f"/api/v1/equipe/itens/{item['id']}").status_code == 404
    assert (
        equipe.patch("/api/v1/equipe/itens/999999", json={"status": "aprovado"}).status_code == 404
    )


def test_equipe_publica_direto(equipe):
    r = equipe.post(
        "/api/v1/equipe/itens",
        json={"tipo": "noticia", "titulo": "Notícia da equipe", "regiao": "Central"},
    )
    assert r.json()["status"] == "aprovado" and r.json()["publicado_em"] == int(AGORA)
    aprov = equipe.get("/api/v1/equipe/itens?status=aprovado&limite=200").json()["itens"]
    assert all("origem" in i for i in aprov)


def test_fontes_e_coleta_pela_api(equipe):
    resumo = equipe.get("/api/v1/equipe/resumo").json()
    assert len(resumo["fontes"]) == 3
    ativas = [f for f in resumo["fontes"] if f["ativa"]]
    assert len(ativas) == 2

    r = equipe.post(
        "/api/v1/equipe/fontes",
        json={"nome": "Teste", "url": "https://exemplo.org/feed", "regiao_padrao": "Central"},
    )
    assert r.status_code == 201
    assert (
        equipe.post(
            "/api/v1/equipe/fontes", json={"nome": "Dup", "url": "https://exemplo.org/feed"}
        ).status_code
        == 400
    )
    assert (
        equipe.post(
            "/api/v1/equipe/fontes", json={"nome": "Ruim", "url": "javascript:x"}
        ).status_code
        == 422
    )

    teste = equipe.post("/api/v1/equipe/fontes/testar", json={"url": "https://exemplo.org/feed"})
    assert teste.json()["ok"] and teste.json()["total"] == 2
    assert teste.json()["sobre_sustentabilidade"] == 1

    coleta = equipe.post("/api/v1/equipe/coletar").json()
    assert sum(x["novos"] for x in coleta) == 1  # mesma notícia em 3 fontes ativas: entra 1 vez
    pend = equipe.get("/api/v1/equipe/itens?status=pendente").json()["itens"]
    assert [i["titulo"] for i in pend] == ["Mutirao de plantio no Cerrado"]
    assert pend[0]["origem"] == "coleta"

    fid = r.json()["id"]
    assert equipe.patch(f"/api/v1/equipe/fontes/{fid}", json={"ativa": False}).json() == {
        "ativa": False
    }
    assert equipe.patch("/api/v1/equipe/fontes/9999", json={"ativa": True}).status_code == 404


def test_config_token_equipe():
    with pytest.raises(ErroDeConfiguracao):
        Configuracao(token_admin="curto")
    with pytest.raises(ErroDeConfiguracao):
        Configuracao(token_admin="mesmo-token-12345", token_painel="mesmo-token-12345")
    with pytest.raises(ErroDeConfiguracao):
        Configuracao(coleta_a_cada_min=5)
    assert Configuracao(coleta_a_cada_min=60).coleta_a_cada_min == 60


def test_cli_semente(tmp_path, monkeypatch, capsys):
    from aurora import cli

    monkeypatch.setenv("AURORA_BANCO", str(tmp_path / "s.db"))
    assert cli.main(["importar-semente"]) == 0
    assert "itens" in capsys.readouterr().out
    assert cli.main(["importar-semente"]) == 0
    assert "0 itens" in capsys.readouterr().out


def test_migracao_de_banco_v1(tmp_path):
    """Um banco criado na versão 1 (só sensores) ganha as tabelas novas sem perder nada."""
    caminho = tmp_path / "v1.db"
    with db.abrir(caminho) as conn:
        conn.executescript(db.ESQUEMA_V1)
        conn.execute("PRAGMA user_version = 1")
        conn.execute("INSERT INTO dispositivos VALUES ('s1', 'S1', ?, 0, NULL)", ("a" * 64,))
    db.inicializar(caminho)
    with db.abrir(caminho) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.VERSAO_ESQUEMA == 3
        assert conn.execute("SELECT COUNT(*) FROM dispositivos").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM itens").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chat_uso").fetchone()[0] == 0


def test_app_instalavel(amb):
    sw = amb.cliente.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert sw.headers["cache-control"] == "no-cache"
    # só conteúdo público e arquivos do site vão para a memória; envios nunca
    assert 'url.pathname.startsWith("/api/v1/conteudo/")' in sw.text
    assert 'pedido.method !== "GET"' in sw.text
    m = amb.cliente.get("/static/manifest.webmanifest")
    assert m.status_code == 200
    assert m.headers["content-type"].startswith("application/manifest+json")
    manifesto = m.json()
    assert manifesto["display"] == "standalone" and manifesto["start_url"].startswith("/")
    tamanhos = {i["sizes"] for i in manifesto["icons"]}
    assert {"192x192", "512x512"} <= tamanhos
    assert any(i.get("purpose") == "maskable" for i in manifesto["icons"])
    for icone in manifesto["icons"]:
        r = amb.cliente.get(icone["src"])
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    pagina = amb.cliente.get("/").text
    assert 'rel="manifest"' in pagina and "apple-touch-icon" in pagina
