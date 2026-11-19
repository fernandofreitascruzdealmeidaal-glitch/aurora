"""API HTTP da Aurora (FastAPI).

Rotas principais:
  POST /api/v1/leituras                       sensores enviam medições (chave do dispositivo)
  GET  /api/v1/dispositivos                   situação atual de cada sala
  GET  /api/v1/dispositivos/{id}/serie        dados agrupados para gráficos
  GET  /api/v1/dispositivos/{id}/resumo       mínimos, máximos, médias, % fora da referência
  GET  /api/v1/dispositivos/{id}/padroes      média por hora do dia
  GET  /api/v1/dispositivos/{id}/leituras     leituras brutas
  GET  /api/v1/dispositivos/{id}/exportar.csv planilha
  GET  /saude                                 o servidor e o banco estão funcionando?
  GET  /                                      painel
"""

import csv
import io
import json
import logging
import math
import mimetypes
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl, ValidationError
from starlette.concurrency import run_in_threadpool

from aurora import __version__, coleta, conteudo, db, servico
from aurora.catalogo import REGIOES, TIPOS
from aurora.config import Configuracao
from aurora.limites import referencias_publicas
from aurora.modelos import LoteEntrada, ResultadoLote
from aurora.seguranca import token_confere

log = logging.getLogger("aurora")
mimetypes.add_type("application/manifest+json", ".webmanifest")
PASTA_ESTATICA = Path(__file__).parent / "static"
MAX_PONTOS_GRAFICO = 2000

IdDispositivo = Annotated[str, PathParam(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")]
Horas = Annotated[int, Query(ge=1, le=24 * 31, description="Período, em horas, até agora")]


def _texto_erro(erro: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e.get('loc', ())) or 'corpo'}: {e.get('msg')}"
        for e in erro.errors()[:5]
    )


async def _ler_corpo_limitado(request: Request, maximo: int) -> bytes:
    declarado = request.headers.get("content-length")
    if declarado is not None:
        try:
            if int(declarado) > maximo:
                raise HTTPException(413, f"Envio grande demais (máximo {maximo} bytes)")
        except ValueError as exc:
            raise HTTPException(400, "Cabeçalho Content-Length inválido") from exc
    partes = bytearray()
    async for pedaco in request.stream():
        partes.extend(pedaco)
        if len(partes) > maximo:
            raise HTTPException(413, f"Envio grande demais (máximo {maximo} bytes)")
    return bytes(partes)


class PedidoTeste(BaseModel):
    url: HttpUrl


class PedidoAtivar(BaseModel):
    ativa: bool


def _laco_de_coleta(
    cfg: Configuracao,
    relogio: Callable[[], float],
    parar: threading.Event,
    baixador: coleta.Baixador,
) -> None:  # pragma: no cover — roda em segundo plano
    while not parar.wait(cfg.coleta_a_cada_min * 60):
        try:
            with db.abrir(cfg.caminho_banco) as conn:
                for r in coleta.coletar_todas(conn, relogio(), baixador):
                    log.info("coleta %s: %s novos, erro=%s", r["fonte"], r["novos"], r["erro"])
        except Exception:
            log.exception("Falha na coleta automática (o servidor continua funcionando)")


def criar_app(
    cfg: Configuracao | None = None,
    relogio: Callable[[], float] = time.time,
    baixador: coleta.Baixador = coleta.baixar,
) -> FastAPI:
    cfg = cfg or Configuracao.do_ambiente()
    db.inicializar(cfg.caminho_banco)
    with db.abrir(cfg.caminho_banco) as conn:
        if conn.execute("SELECT COUNT(*) FROM dicas").fetchone()[0] == 0:
            conteudo.importar_semente(conn, relogio())  # primeira vez: carrega o conteúdo inicial

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
        parar = threading.Event()
        if cfg.coleta_a_cada_min:  # pragma: no cover
            threading.Thread(
                target=_laco_de_coleta, args=(cfg, relogio, parar, baixador), daemon=True
            ).start()
        yield
        parar.set()

    app = FastAPI(
        title="Aurora",
        version=__version__,
        description=(
            "Base de sustentabilidade de Minas Gerais e monitoramento ambiental de salas de aula. "
            + referencias_publicas()["aviso"]
        ),
        lifespan=ciclo_de_vida,
    )
    app.state.cfg = cfg
    fuso = ZoneInfo(cfg.fuso)

    def hoje() -> date:
        return datetime.fromtimestamp(relogio(), tz=fuso).date()

    # ------------------------------------------------------------ dependências

    def conexao() -> Iterator[sqlite3.Connection]:
        with db.abrir(cfg.caminho_banco) as conn:
            yield conn

    def exigir_painel(authorization: Annotated[str | None, Header()] = None) -> None:
        if cfg.token_painel is None:
            return
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token_confere(token, cfg.token_painel):
            raise HTTPException(
                401, "Token do painel ausente ou inválido", headers={"WWW-Authenticate": "Bearer"}
            )

    def exigir_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        if cfg.token_admin is None:
            raise HTTPException(
                403, "Área da equipe desligada. Defina AURORA_TOKEN_ADMIN no servidor para ativar."
            )
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token_confere(token, cfg.token_admin):
            raise HTTPException(
                401, "Token da equipe ausente ou inválido", headers={"WWW-Authenticate": "Bearer"}
            )

    Conexao = Annotated[sqlite3.Connection, Depends(conexao)]
    Painel = [Depends(exigir_painel)]
    Equipe = [Depends(exigir_admin)]

    def periodo(horas: int) -> tuple[int, int]:
        agora = int(relogio())
        return agora - horas * 3600, agora

    # ------------------------------------------------------------ erros e cabeçalhos

    @app.exception_handler(servico.NaoEncontrado)
    async def _nao_encontrado(_: Request, exc: servico.NaoEncontrado) -> JSONResponse:
        return JSONResponse({"detail": f"Dispositivo não encontrado: {exc.args[0]}"}, 404)

    @app.exception_handler(servico.ErroDeDados)
    async def _dados(_: Request, exc: servico.ErroDeDados) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, 400)

    @app.exception_handler(conteudo.ItemNaoEncontrado)
    async def _item(_: Request, exc: conteudo.ItemNaoEncontrado) -> JSONResponse:
        return JSONResponse({"detail": "Item não encontrado"}, 404)

    @app.exception_handler(conteudo.ErroDeConteudo)
    async def _conteudo(_: Request, exc: conteudo.ErroDeConteudo) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, 400)

    @app.exception_handler(Exception)
    async def _inesperado(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Erro inesperado em %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Erro interno. Veja o registro (log) do servidor."}, 500)

    @app.middleware("http")
    async def _cabecalhos_seguranca(request: Request, chamar_proximo: Any) -> Any:
        resposta = await chamar_proximo(request)
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
            resposta.headers.setdefault("X-Frame-Options", "DENY")
            resposta.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
            )
        return resposta

    # ------------------------------------------------------------ rotas

    @app.get("/saude", tags=["sistema"])
    def saude(conn: Conexao) -> dict[str, str]:
        if not db.verificar(conn):
            raise HTTPException(503, "Banco de dados com problema de integridade")
        return {"status": "ok", "versao": __version__}

    @app.post("/api/v1/leituras", response_model=ResultadoLote, tags=["sensores"])
    async def receber_leituras(
        request: Request,
        x_dispositivo: Annotated[str | None, Header()] = None,
        x_chave: Annotated[str | None, Header()] = None,
    ) -> ResultadoLote:
        """Recebe um lote de leituras de um sensor.

        Cabeçalhos: `X-Dispositivo` (ID) e `X-Chave` (chave criada no cadastro).
        Resposta 200 = lote processado; o sensor pode apagar o lote do seu buffer.
        """

        def _autenticar() -> bool:
            with db.abrir(cfg.caminho_banco) as conn:
                return servico.autenticar(conn, x_dispositivo, x_chave)

        if not await run_in_threadpool(_autenticar):
            raise HTTPException(401, "Dispositivo ou chave inválidos")

        corpo = await _ler_corpo_limitado(request, cfg.max_bytes_requisicao)
        try:
            dados = json.loads(corpo)
        except (ValueError, RecursionError) as exc:
            raise HTTPException(400, "O corpo do envio não é um JSON válido") from exc
        try:
            lote = LoteEntrada.model_validate(dados)
        except ValidationError as exc:
            raise HTTPException(422, _texto_erro(exc)) from exc

        def _ingerir() -> ResultadoLote:
            with db.abrir(cfg.caminho_banco) as conn:
                return servico.ingerir(conn, str(x_dispositivo), lote.leituras, relogio(), cfg)

        resultado = await run_in_threadpool(_ingerir)
        if resultado.rejeitadas:
            log.warning(
                "%s: %d leitura(s) recusada(s); primeira: %s",
                x_dispositivo,
                len(resultado.rejeitadas),
                resultado.rejeitadas[0].motivo,
            )
        return resultado

    @app.get("/api/v1/referencias", tags=["painel"])
    def referencias() -> dict[str, Any]:
        return referencias_publicas()

    @app.get("/api/v1/dispositivos", dependencies=Painel, tags=["painel"])
    def dispositivos(conn: Conexao) -> list[dict[str, Any]]:
        return servico.status_dispositivos(conn, relogio(), cfg)

    @app.get("/api/v1/dispositivos/{dispositivo_id}/leituras", dependencies=Painel, tags=["painel"])
    def leituras(
        conn: Conexao,
        dispositivo_id: IdDispositivo,
        horas: Horas = 24,
        limite: Annotated[int, Query(ge=1, le=5000)] = 500,
    ) -> list[dict[str, Any]]:
        inicio, fim = periodo(horas)
        return servico.listar_leituras(conn, dispositivo_id, inicio, fim, limite)

    @app.get("/api/v1/dispositivos/{dispositivo_id}/serie", dependencies=Painel, tags=["painel"])
    def serie(
        conn: Conexao,
        dispositivo_id: IdDispositivo,
        horas: Horas = 24,
        intervalo_min: Annotated[int, Query(ge=1, le=1440)] = 10,
    ) -> dict[str, Any]:
        inicio, fim = periodo(horas)
        # Protege o navegador: nunca mais que MAX_PONTOS_GRAFICO pontos por gráfico.
        balde_s = max(intervalo_min * 60, math.ceil((fim - inicio) / MAX_PONTOS_GRAFICO))
        pontos = servico.serie(conn, dispositivo_id, inicio, fim, balde_s)
        return {"intervalo_s": balde_s, "pontos": pontos}

    @app.get("/api/v1/dispositivos/{dispositivo_id}/resumo", dependencies=Painel, tags=["painel"])
    def resumo(conn: Conexao, dispositivo_id: IdDispositivo, horas: Horas = 24) -> dict[str, Any]:
        inicio, fim = periodo(horas)
        return servico.resumo(conn, dispositivo_id, inicio, fim)

    @app.get("/api/v1/dispositivos/{dispositivo_id}/padroes", dependencies=Painel, tags=["painel"])
    def padroes(
        conn: Conexao,
        dispositivo_id: IdDispositivo,
        dias: Annotated[int, Query(ge=1, le=31)] = 7,
    ) -> dict[str, Any]:
        inicio, fim = periodo(dias * 24)
        return {
            "fuso": cfg.fuso,
            "horas": servico.padrao_por_hora(conn, dispositivo_id, inicio, fim, cfg.fuso),
        }

    @app.get(
        "/api/v1/dispositivos/{dispositivo_id}/exportar.csv", dependencies=Painel, tags=["painel"]
    )
    def exportar(
        conn: Conexao, dispositivo_id: IdDispositivo, horas: Horas = 24 * 7
    ) -> StreamingResponse:
        inicio, fim = periodo(horas)
        linhas = list(servico.linhas_csv(conn, dispositivo_id, inicio, fim, cfg.fuso))
        buffer = io.StringIO()
        buffer.write("﻿")  # BOM: o Excel abre os acentos corretamente
        csv.writer(buffer, delimiter=";").writerows(linhas)
        nome = f"aurora-{dispositivo_id}.csv"
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'},
        )

    # ------------------------------------------------------------ base de sustentabilidade

    def _tipos(tipo: str | None) -> list[str] | None:
        if not tipo:
            return None
        lista = [t.strip() for t in tipo.split(",") if t.strip()]
        invalidos = [t for t in lista if t not in TIPOS]
        if invalidos:
            raise HTTPException(422, f"Tipo desconhecido: {', '.join(invalidos)}")
        return lista

    def _regiao(regiao: str | None) -> str | None:
        if regiao and regiao not in REGIOES:
            raise HTTPException(422, "Região desconhecida")
        return regiao or None

    Busca = Annotated[str | None, Query(max_length=100)]
    Limite = Annotated[int, Query(ge=1, le=200)]
    Pagina = Annotated[int, Query(ge=1, le=1000)]

    @app.get("/api/v1/conteudo/catalogo", tags=["conteúdo"])
    def catalogo() -> dict[str, Any]:
        return {"tipos": TIPOS, "regioes": REGIOES}

    @app.get("/api/v1/conteudo/inicio", tags=["conteúdo"])
    def pagina_inicial(conn: Conexao) -> dict[str, Any]:
        return conteudo.inicio(conn, hoje())

    @app.get("/api/v1/conteudo/itens", tags=["conteúdo"])
    def itens_publicos(
        conn: Conexao,
        tipo: Annotated[str | None, Query(max_length=200)] = None,
        regiao: Annotated[str | None, Query(max_length=60)] = None,
        busca: Busca = None,
        quando: Literal["todos", "futuros", "passados"] = "todos",
        limite: Limite = 30,
        pagina: Pagina = 1,
    ) -> dict[str, Any]:
        return conteudo.listar_itens(
            conn,
            hoje(),
            tipos=_tipos(tipo),
            regiao=_regiao(regiao),
            busca=busca,
            quando=quando,
            limite=limite,
            deslocamento=(pagina - 1) * limite,
        )

    @app.get("/api/v1/conteudo/itens/{item_id}", tags=["conteúdo"])
    def item_publico(conn: Conexao, item_id: Annotated[int, PathParam(ge=1)]) -> dict[str, Any]:
        return conteudo.obter_item(conn, item_id)

    @app.get("/api/v1/conteudo/datas", tags=["conteúdo"])
    def datas(
        conn: Conexao, dias: Annotated[int, Query(ge=1, le=366)] = 366
    ) -> list[dict[str, Any]]:
        return conteudo.datas_ambientais(conn, hoje(), dias)

    @app.get("/api/v1/conteudo/dicas", tags=["conteúdo"])
    def dicas(
        conn: Conexao, categoria: Annotated[str | None, Query(max_length=40)] = None
    ) -> list[dict[str, Any]]:
        return conteudo.listar_dicas(conn, categoria)

    # ------------------------------------------------------------ área da equipe

    @app.get("/api/v1/equipe/resumo", dependencies=Equipe, tags=["equipe"])
    def equipe_resumo(conn: Conexao) -> dict[str, Any]:
        return {**conteudo.contagens(conn), "fontes": conteudo.listar_fontes(conn)}

    @app.get("/api/v1/equipe/itens", dependencies=Equipe, tags=["equipe"])
    def equipe_itens(
        conn: Conexao,
        status: Literal["pendente", "aprovado", "rejeitado"] = "pendente",
        tipo: Annotated[str | None, Query(max_length=200)] = None,
        busca: Busca = None,
        limite: Limite = 50,
        pagina: Pagina = 1,
    ) -> dict[str, Any]:
        return conteudo.listar_itens(
            conn,
            hoje(),
            tipos=_tipos(tipo),
            busca=busca,
            status=status,
            limite=limite,
            deslocamento=(pagina - 1) * limite,
            interno=True,
        )

    @app.post("/api/v1/equipe/itens", dependencies=Equipe, tags=["equipe"], status_code=201)
    def equipe_criar(
        conn: Conexao, item: conteudo.ItemEntrada, publicar: bool = True
    ) -> dict[str, Any]:
        novo = conteudo.criar_item(
            conn,
            item,
            relogio(),
            status="aprovado" if publicar else "pendente",
            publicado_em=int(relogio()) if item.tipo == "noticia" else None,
        )
        return conteudo.obter_item(conn, int(novo or 0), publico=False)

    @app.patch("/api/v1/equipe/itens/{item_id}", dependencies=Equipe, tags=["equipe"])
    def equipe_alterar(
        conn: Conexao, item_id: Annotated[int, PathParam(ge=1)], alt: conteudo.AlteracaoItem
    ) -> dict[str, Any]:
        conteudo.alterar_item(conn, item_id, alt, relogio())
        return conteudo.obter_item(conn, item_id, publico=False)

    @app.delete(
        "/api/v1/equipe/itens/{item_id}", dependencies=Equipe, tags=["equipe"], status_code=204
    )
    def equipe_remover(conn: Conexao, item_id: Annotated[int, PathParam(ge=1)]) -> None:
        conteudo.remover_item(conn, item_id)

    @app.post("/api/v1/equipe/fontes", dependencies=Equipe, tags=["equipe"], status_code=201)
    def equipe_nova_fonte(conn: Conexao, fonte: conteudo.FonteEntrada) -> dict[str, int]:
        return {"id": conteudo.criar_fonte(conn, fonte)}

    @app.patch("/api/v1/equipe/fontes/{fonte_id}", dependencies=Equipe, tags=["equipe"])
    def equipe_ativar_fonte(
        conn: Conexao, fonte_id: Annotated[int, PathParam(ge=1)], pedido: PedidoAtivar
    ) -> dict[str, bool]:
        conteudo.ativar_fonte(conn, fonte_id, pedido.ativa)
        return {"ativa": pedido.ativa}

    @app.post("/api/v1/equipe/fontes/testar", dependencies=Equipe, tags=["equipe"])
    def equipe_testar_fonte(pedido: PedidoTeste) -> dict[str, Any]:
        return coleta.testar_fonte(str(pedido.url), baixador)

    @app.post("/api/v1/equipe/coletar", dependencies=Equipe, tags=["equipe"])
    def equipe_coletar(conn: Conexao) -> list[dict[str, Any]]:
        return coleta.coletar_todas(conn, relogio(), baixador)

    # ------------------------------------------------------------ páginas

    @app.get("/", include_in_schema=False)
    def site() -> FileResponse:
        return FileResponse(PASTA_ESTATICA / "index.html")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        # Na raiz para valer para o site todo; sem cache para atualizações chegarem logo.
        return FileResponse(
            PASTA_ESTATICA / "sw.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/salas", include_in_schema=False)
    def painel() -> FileResponse:
        return FileResponse(PASTA_ESTATICA / "salas.html")

    @app.get("/equipe", include_in_schema=False)
    def equipe() -> FileResponse:
        return FileResponse(PASTA_ESTATICA / "equipe.html")

    app.mount("/static", StaticFiles(directory=PASTA_ESTATICA), name="static")
    return app
