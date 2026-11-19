"""Coleta automática de notícias por RSS/Atom — com segurança e sem publicar nada sozinha.

Cuidados:
- Só acessa endereços públicos da internet (nunca a rede interna da escola), inclusive
  depois de redirecionamentos — evita que alguém use o servidor para espiar a rede local.
- Lê XML com `defusedxml`, que bloqueia ataques conhecidos (ex.: "bilhão de risadas").
- Limite de tamanho (2 MB) e de tempo (15 s) por fonte.
- Tira todo o HTML dos textos: no site só aparece texto puro.
- Tudo entra como "pendente". Uma pessoa da equipe aprova antes de publicar.
- Fonte que falha 10 vezes seguidas é desativada e o erro fica registrado.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring
from pydantic import ValidationError

from aurora.catalogo import sobre_sustentabilidade
from aurora.conteudo import ItemEntrada, criar_item
from aurora.db import transacao

MAX_BYTES = 2_000_000
TEMPO_LIMITE_S = 15
MAX_ENTRADAS_POR_FONTE = 100
IDADE_MAXIMA_DIAS = 45
FALHAS_PARA_DESATIVAR = 10
AGENTE = "AuroraBot/0.2 (projeto escolar de sustentabilidade; contato pelo site)"

Baixador = Callable[[str], bytes]


class ErroDeColeta(Exception):
    """Falha ao buscar ou interpretar uma fonte."""


# ---------------------------------------------------------------- rede segura


def validar_url_publica(url: str) -> None:
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https"):
        raise ErroDeColeta("só endereços http:// ou https://")
    if not partes.hostname:
        raise ErroDeColeta("endereço sem nome de servidor")
    try:
        enderecos = socket.getaddrinfo(partes.hostname, partes.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ErroDeColeta(f"não foi possível encontrar o servidor {partes.hostname}") from exc
    for *_, sockaddr in enderecos:
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global:
            raise ErroDeColeta("endereço da rede interna não é permitido")


class _RedirecionamentoSeguro(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        validar_url_publica(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def baixar(url: str) -> bytes:  # pragma: no cover — acessa a internet de verdade
    validar_url_publica(url)
    abridor = urllib.request.build_opener(_RedirecionamentoSeguro)
    pedido = urllib.request.Request(  # noqa: S310 — esquema validado acima
        url,
        headers={
            "User-Agent": AGENTE,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
    )
    try:
        with abridor.open(pedido, timeout=TEMPO_LIMITE_S) as resposta:
            dados = resposta.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ErroDeColeta(f"o servidor respondeu {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ErroDeColeta(f"falha de conexão: {exc}") from exc
    if len(dados) > MAX_BYTES:
        raise ErroDeColeta("feed grande demais")
    return dados


# ---------------------------------------------------------------- texto


class _SoTexto(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._ignorar += 1
        elif tag in ("p", "br", "li", "div"):
            self.partes.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._ignorar:
            self._ignorar -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignorar:
            self.partes.append(data)


def limpar_html(texto: str | None, limite: int = 600) -> str:
    if not texto:
        return ""
    leitor = _SoTexto()
    leitor.feed(unescape(texto))
    leitor.close()
    limpo = " ".join("".join(leitor.partes).split())
    if len(limpo) > limite:
        limpo = limpo[: limite - 1].rsplit(" ", 1)[0] + "…"
    return limpo


def normalizar_url(url: str) -> str:
    """Remove parâmetros de rastreio (utm_*) e o '#...' para reconhecer a mesma notícia."""
    p = urlsplit(url.strip())
    consulta = "&".join(
        par for par in p.query.split("&") if par and not par.lower().startswith("utm_")
    )
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, consulta, ""))


# ---------------------------------------------------------------- feeds


@dataclass
class Entrada:
    titulo: str
    link: str
    resumo: str
    publicado_em: int | None


def _nome(el: Element) -> str:
    return el.tag.rsplit("}", 1)[-1].lower()


def _filho(el: Element, *nomes: str) -> Element | None:
    for f in el:
        if _nome(f) in nomes:
            return f
    return None


def _texto(el: Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _data(texto: str) -> int | None:
    texto = texto.strip()
    if not texto:
        return None
    try:
        d = parsedate_to_datetime(texto)  # formato RSS: "Tue, 18 Nov 2026 10:00:00 -0300"
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(texto.replace("Z", "+00:00"))  # formato Atom
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def interpretar_feed(dados: bytes) -> list[Entrada]:
    try:
        raiz = fromstring(dados)
    except (ParseError, DefusedXmlException, ValueError) as exc:
        raise ErroDeColeta(f"o conteúdo não é um feed válido ({type(exc).__name__})") from exc

    nome = _nome(raiz)
    entradas: list[Entrada] = []
    if nome in ("rss", "rdf"):
        # RSS 2.0: <rss><channel><item>…  |  RSS 1.0 (RDF): <rdf:RDF><item>…
        canal = _filho(raiz, "channel")
        pai = canal if nome == "rss" and canal is not None else raiz
        itens = [e for e in pai if _nome(e) == "item"]
        for it in itens:
            entradas.append(
                Entrada(
                    titulo=limpar_html(_texto(_filho(it, "title")), 200),
                    link=_texto(_filho(it, "link")) or _texto(_filho(it, "guid")),
                    resumo=limpar_html(_texto(_filho(it, "description", "summary", "encoded"))),
                    publicado_em=_data(_texto(_filho(it, "pubdate", "date"))),
                )
            )
    elif nome == "feed":
        for it in (e for e in raiz if _nome(e) == "entry"):
            link = ""
            for f in it:
                if _nome(f) == "link" and f.get("rel", "alternate") == "alternate":
                    link = f.get("href", "")
                    break
            entradas.append(
                Entrada(
                    titulo=limpar_html(_texto(_filho(it, "title")), 200),
                    link=link,
                    resumo=limpar_html(_texto(_filho(it, "summary", "content"))),
                    publicado_em=_data(_texto(_filho(it, "published", "updated"))),
                )
            )
    else:
        raise ErroDeColeta(f"formato não reconhecido (<{nome}>); use um endereço RSS ou Atom")
    return entradas


# ---------------------------------------------------------------- coleta


def _registrar_falha(conn: sqlite3.Connection, fonte: sqlite3.Row, erro: str, agora: float) -> None:
    falhas = fonte["falhas_seguidas"] + 1
    with transacao(conn):
        conn.execute(
            """UPDATE fontes SET ultima_coleta = ?, ultimo_erro = ?, falhas_seguidas = ?,
                      ativa = CASE WHEN ? >= ? THEN 0 ELSE ativa END
               WHERE id = ?""",
            (int(agora), erro[:300], falhas, falhas, FALHAS_PARA_DESATIVAR, fonte["id"]),
        )


def coletar_fonte(
    conn: sqlite3.Connection, fonte: sqlite3.Row, agora: float, baixador: Baixador = baixar
) -> dict[str, Any]:
    resultado: dict[str, Any] = {"fonte": fonte["nome"], "novos": 0, "ignorados": 0, "erro": None}
    try:
        entradas = interpretar_feed(baixador(fonte["url"]))
    except ErroDeColeta as exc:
        _registrar_falha(conn, fonte, str(exc), agora)
        resultado["erro"] = str(exc)
        return resultado

    corte = agora - IDADE_MAXIMA_DIAS * 86400
    for e in entradas[:MAX_ENTRADAS_POR_FONTE]:
        if not e.link.startswith(("http://", "https://")) or len(e.titulo) < 3:
            resultado["ignorados"] += 1
            continue
        if e.publicado_em is not None and e.publicado_em < corte:
            resultado["ignorados"] += 1
            continue
        if fonte["exigir_palavras"] and not sobre_sustentabilidade(e.titulo, e.resumo):
            resultado["ignorados"] += 1
            continue
        link = normalizar_url(e.link)
        try:
            item = ItemEntrada(
                tipo=fonte["tipo_padrao"],
                titulo=e.titulo,
                resumo=e.resumo,
                url=link,
                fonte=fonte["nome"][:120],
                regiao=fonte["regiao_padrao"],
            )
        except ValidationError:
            resultado["ignorados"] += 1
            continue
        chave = "url:" + hashlib.sha256(link.encode()).hexdigest()
        publicado = min(e.publicado_em or int(agora), int(agora))
        if criar_item(conn, item, agora, origem="coleta", chave=chave, publicado_em=publicado):
            resultado["novos"] += 1

    with transacao(conn):
        conn.execute(
            "UPDATE fontes SET ultima_coleta = ?, ultimo_erro = NULL, falhas_seguidas = 0 "
            "WHERE id = ?",
            (int(agora), fonte["id"]),
        )
    return resultado


def coletar_todas(
    conn: sqlite3.Connection, agora: float, baixador: Baixador = baixar
) -> list[dict[str, Any]]:
    fontes = conn.execute("SELECT * FROM fontes WHERE ativa = 1 ORDER BY id").fetchall()
    return [coletar_fonte(conn, f, agora, baixador) for f in fontes]


def testar_fonte(url: str, baixador: Baixador = baixar) -> dict[str, Any]:
    """Busca o feed sem gravar nada e mostra uma amostra — para conferir antes de cadastrar."""
    try:
        entradas = interpretar_feed(baixador(url))
    except ErroDeColeta as exc:
        return {"ok": False, "erro": str(exc), "amostra": []}
    relevantes = [e for e in entradas if sobre_sustentabilidade(e.titulo, e.resumo)]
    return {
        "ok": True,
        "erro": None,
        "total": len(entradas),
        "sobre_sustentabilidade": len(relevantes),
        "amostra": [{"titulo": e.titulo, "link": e.link} for e in (relevantes or entradas)[:5]],
    }
