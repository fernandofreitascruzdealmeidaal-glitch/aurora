"""Coleta de notícias: leitura de feeds, filtros, segurança e tolerância a falhas."""

from __future__ import annotations

import pytest

from aurora import coleta, conteudo, db
from tests.conftest import AGORA

# 19/11/2026 em formato RSS
DATA_RSS = "Thu, 19 Nov 2026 10:00:00 -0300"

RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Fonte</title>
<item>
  <title>Escolas de BH fazem mutirão de reciclagem</title>
  <link>https://noticias.exemplo.org/reciclagem?utm_source=rss&amp;id=7#topo</link>
  <description><![CDATA[<p>Estudantes <b>separam</b> resíduos.</p>
    <script>alert(1)</script>]]></description>
  <pubDate>{DATA_RSS}</pubDate>
</item>
<item>
  <title>Campeonato de futebol começa</title>
  <link>https://noticias.exemplo.org/futebol</link>
  <pubDate>{DATA_RSS}</pubDate>
</item>
<item>
  <title>Plantio de mudas antigo</title>
  <link>https://noticias.exemplo.org/antigo</link>
  <pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate>
</item>
<item>
  <title>Link perigoso sobre meio ambiente</title>
  <link>javascript:alert(1)</link>
</item>
<item><title>x</title><link>https://noticias.exemplo.org/curto</link></item>
</channel></rss>""".encode()

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>Cerrado ganha nova unidade de conservacao</title>
<link rel="alternate" href="https://atom.exemplo.org/cerrado"/>
<link rel="enclosure" href="https://atom.exemplo.org/foto.jpg"/>
<summary>Area protegida</summary><updated>2026-11-18T10:00:00Z</updated></entry>
</feed>"""

RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>R</title></channel>
<item><title>Qualidade da agua no Rio das Velhas</title><link>https://rdf.exemplo.org/1</link>
<dc:date>2026-11-17T08:00:00-03:00</dc:date></item>
</rdf:RDF>"""

BOMBA = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title></item></channel></rss>"""

XXE = b"""<?xml version="1.0"?>
<!DOCTYPE x [<!ENTITY segredo SYSTEM "file:///etc/passwd">]>
<rss><channel><item><title>&segredo;</title></item></channel></rss>"""


def _fonte(conn, nome="Fonte teste", url="https://feed.exemplo.org/rss", exigir=True):
    fid = conteudo.criar_fonte(
        conn,
        conteudo.FonteEntrada(
            nome=nome, url=url, regiao_padrao="Metropolitana de BH", exigir_palavras=exigir
        ),
    )
    return conn.execute("SELECT * FROM fontes WHERE id = ?", (fid,)).fetchone()


@pytest.fixture
def conn(tmp_path):
    caminho = tmp_path / "c.db"
    db.inicializar(caminho)
    with db.abrir(caminho) as c:
        yield c


# ------------------------------------------------------------------ leitura de feeds


def test_rss():
    e = coleta.interpretar_feed(RSS)
    assert len(e) == 5
    assert e[0].titulo == "Escolas de BH fazem mutirão de reciclagem"
    assert e[0].resumo == "Estudantes separam resíduos."  # sem HTML e sem script
    assert e[0].publicado_em == int(AGORA)


def test_atom():
    [e] = coleta.interpretar_feed(ATOM)
    assert e.link == "https://atom.exemplo.org/cerrado"  # ignora o link da foto
    assert e.publicado_em is not None and e.resumo == "Area protegida"


def test_rdf():
    [e] = coleta.interpretar_feed(RDF)
    assert e.link == "https://rdf.exemplo.org/1" and e.publicado_em is not None


@pytest.mark.parametrize(
    "ruim", [b"", b"nao e xml", b"<html><body>pagina</body></html>", BOMBA, XXE, b"<rss><a></rss>"]
)
def test_feed_invalido_ou_malicioso(ruim):
    with pytest.raises(coleta.ErroDeColeta):
        coleta.interpretar_feed(ruim)


def test_datas_estranhas_viram_nulo():
    assert coleta._data("") is None
    assert coleta._data("ontem à tarde") is None
    assert coleta._data("2026-11-18T10:00:00") is not None  # sem fuso: assume UTC


def test_limpar_html():
    assert coleta.limpar_html(None) == ""
    assert coleta.limpar_html("<style>p{}</style>Olá&nbsp;<i>mundo</i>") == "Olá mundo"
    longo = coleta.limpar_html("palavra " * 200, limite=50)
    assert len(longo) <= 50 and longo.endswith("…")


def test_normalizar_url():
    assert coleta.normalizar_url("HTTPS://Site.org/a?utm_source=x&id=1&UTM_medium=y#frag") == (
        "https://site.org/a?id=1"
    )


# ------------------------------------------------------------------ coleta


def test_coleta_filtra_e_fica_pendente(conn):
    fonte = _fonte(conn)
    r = coleta.coletar_fonte(conn, fonte, AGORA, lambda url: RSS)
    assert r == {"fonte": "Fonte teste", "novos": 1, "ignorados": 4, "erro": None}
    [item] = conn.execute("SELECT * FROM itens").fetchall()
    assert item["status"] == "pendente" and item["origem"] == "coleta"
    assert item["url"] == "https://noticias.exemplo.org/reciclagem?id=7"
    assert item["regiao"] == "Metropolitana de BH" and item["fonte"] == "Fonte teste"
    f = conn.execute("SELECT * FROM fontes").fetchone()
    assert f["ultima_coleta"] == int(AGORA) and f["ultimo_erro"] is None


def test_coleta_nao_duplica(conn):
    fonte = _fonte(conn)
    coleta.coletar_fonte(conn, fonte, AGORA, lambda url: RSS)
    r = coleta.coletar_fonte(conn, fonte, AGORA + 3600, lambda url: RSS)
    assert r["novos"] == 0
    assert conn.execute("SELECT COUNT(*) FROM itens").fetchone()[0] == 1


def test_fonte_sem_filtro_de_palavras(conn):
    fonte = _fonte(conn, exigir=False)
    r = coleta.coletar_fonte(conn, fonte, AGORA, lambda url: RSS)
    assert r["novos"] == 2  # reciclagem + futebol (fonte já é toda sobre o tema)


def test_data_no_futuro_vira_agora(conn):
    futuro = RSS.replace(DATA_RSS.encode(), b"Fri, 01 Jan 2100 00:00:00 +0000")
    coleta.coletar_fonte(conn, _fonte(conn), AGORA, lambda url: futuro)
    assert conn.execute("SELECT publicado_em FROM itens").fetchone()[0] == int(AGORA)


def test_falhas_seguidas_desativam_a_fonte(conn):
    _fonte(conn)

    def quebrado(url):
        raise coleta.ErroDeColeta("o servidor respondeu 500")

    for _ in range(coleta.FALHAS_PARA_DESATIVAR):
        fonte = conn.execute("SELECT * FROM fontes").fetchone()
        r = coleta.coletar_fonte(conn, fonte, AGORA, quebrado)
        assert r["erro"] == "o servidor respondeu 500"
    f = conn.execute("SELECT * FROM fontes").fetchone()
    assert f["ativa"] == 0 and f["falhas_seguidas"] == 10 and "500" in f["ultimo_erro"]
    assert coleta.coletar_todas(conn, AGORA, quebrado) == []  # desativada não é coletada


def test_sucesso_zera_as_falhas(conn):
    fonte = _fonte(conn)

    def quebrado(url):
        raise coleta.ErroDeColeta("fora do ar")

    coleta.coletar_fonte(conn, fonte, AGORA, quebrado)
    fonte = conn.execute("SELECT * FROM fontes").fetchone()
    coleta.coletar_fonte(conn, fonte, AGORA, lambda url: RSS)
    assert conn.execute("SELECT falhas_seguidas FROM fontes").fetchone()[0] == 0


def test_uma_fonte_quebrada_nao_para_as_outras(conn):
    _fonte(conn, nome="Boa", url="https://boa.exemplo.org/rss")
    _fonte(conn, nome="Ruim", url="https://ruim.exemplo.org/rss")

    def baixador(url):
        if "ruim" in url:
            return b"<html>erro</html>"
        return ATOM

    resultados = coleta.coletar_todas(conn, AGORA, baixador)
    assert {r["fonte"]: (r["novos"], bool(r["erro"])) for r in resultados} == {
        "Boa": (1, False),
        "Ruim": (0, True),
    }


def test_testar_fonte():
    ok = coleta.testar_fonte("https://x.org", lambda url: RSS)
    assert ok["ok"] and ok["total"] == 5 and ok["sobre_sustentabilidade"] >= 1
    ruim = coleta.testar_fonte("https://x.org", lambda url: b"lixo")
    assert not ruim["ok"] and ruim["erro"]


# ------------------------------------------------------------------ segurança de rede


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/feed",
        "http://localhost/feed",
        "http://10.0.0.5/feed",
        "http://192.168.0.10:8000/api",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/feed",
        "ftp://exemplo.org/feed",
        "file:///etc/passwd",
        "http:///sem-host",
        "http://nome-que-nao-existe.invalid/feed",
    ],
)
def test_bloqueia_rede_interna_e_esquemas_estranhos(url):
    with pytest.raises(coleta.ErroDeColeta):
        coleta.validar_url_publica(url)


def test_permite_endereco_publico():
    coleta.validar_url_publica("https://8.8.8.8/feed")  # IP público literal, sem precisar de DNS


def test_redirecionamento_para_rede_interna_e_bloqueado():
    with pytest.raises(coleta.ErroDeColeta):
        coleta._RedirecionamentoSeguro().redirect_request(
            None, None, 302, "Found", {}, "http://127.0.0.1/admin"
        )
