"""Base de sustentabilidade: notícias, eventos, feiras, voluntariado, vagas, cursos,
datas ambientais e dicas — tudo com curadoria da equipe.

Regra de ouro: nada aparece no site sem status "aprovado". Conteúdo coletado
automaticamente entra como "pendente" e só é publicado depois que alguém confere.
"""

from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from aurora.catalogo import REGIOES, STATUS, TIPOS, TIPOS_AGENDA, normalizar
from aurora.db import transacao

CAMINHO_SEMENTE = Path(__file__).parent / "dados" / "semente.json"
CAMPOS_TEXTO = ("titulo", "resumo", "fonte", "instituicao", "cidade", "local", "publico")
MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
DIAS_SEMANA = {"seg": 0, "ter": 1, "qua": 2, "qui": 3, "sex": 4, "sab": 5, "dom": 6}


class ItemNaoEncontrado(LookupError):
    pass


class ErroDeConteudo(ValueError):
    pass


# ---------------------------------------------------------------- validação


def _checar_tipo(v: str) -> str:
    if v not in TIPOS:
        raise ValueError(f"tipo deve ser um de: {', '.join(TIPOS)}")
    return v


def _checar_regiao(v: str) -> str:
    if v and v not in REGIOES:
        raise ValueError(f"região deve ser uma de: {', '.join(REGIOES)}")
    return v


class ItemEntrada(BaseModel):
    """Um item da base. Tudo que entra (manual, coleta ou semente) passa por aqui."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tipo: str
    titulo: str = Field(min_length=3, max_length=200)
    resumo: str = Field(default="", max_length=1200)
    url: HttpUrl | None = None
    fonte: str = Field(default="", max_length=120)
    instituicao: str = Field(default="", max_length=120)
    cidade: str = Field(default="", max_length=80)
    regiao: str = ""
    local: str = Field(default="", max_length=160)
    publico: str = Field(default="", max_length=160)
    inicio: date | None = None
    fim: date | None = None
    data_confirmada: bool = True
    recorrencia: str = Field(default="", max_length=120)
    gratuito: bool | None = None
    verificado_em: date | None = None

    _tipo = field_validator("tipo")(_checar_tipo)
    _regiao = field_validator("regiao")(_checar_regiao)

    @field_validator("url")
    @classmethod
    def _url(cls, v: HttpUrl | None) -> HttpUrl | None:
        if v is not None and len(str(v)) > 500:
            raise ValueError("endereço longo demais")
        return v

    @model_validator(mode="after")
    def _datas(self) -> ItemEntrada:
        if self.fim and not self.inicio:
            raise ValueError("informe o início antes do fim")
        if self.fim and self.inicio and self.fim < self.inicio:
            raise ValueError("o fim não pode ser antes do início")
        return self


class AlteracaoItem(BaseModel):
    """Campos que a equipe pode mudar num item existente (todos opcionais)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["pendente", "aprovado", "rejeitado"] | None = None
    tipo: str | None = None
    titulo: str | None = Field(default=None, min_length=3, max_length=200)
    resumo: str | None = Field(default=None, max_length=1200)
    cidade: str | None = Field(default=None, max_length=80)
    regiao: str | None = None
    local: str | None = Field(default=None, max_length=160)
    inicio: date | None = None
    fim: date | None = None
    data_confirmada: bool | None = None


# ---------------------------------------------------------------- itens


def _texto_busca(campos: dict[str, Any]) -> str:
    partes = [str(campos.get(c) or "") for c in (*CAMPOS_TEXTO, "regiao")]
    partes.append(TIPOS.get(campos.get("tipo") or "", ""))  # "Voluntariado", não "voluntariado"
    return normalizar(" ".join(partes))


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def criar_item(
    conn: sqlite3.Connection,
    dados: ItemEntrada,
    agora: float,
    *,
    status: str = "pendente",
    origem: str = "manual",
    chave: str | None = None,
    publicado_em: int | None = None,
) -> int | None:
    """Cria o item. Devolve o id, ou None se já existia um com a mesma `chave`."""
    if status not in STATUS:
        raise ErroDeConteudo(f"status inválido: {status}")
    campos = dados.model_dump()
    linha = {
        **{c: campos[c] for c in (*CAMPOS_TEXTO, "tipo", "regiao", "recorrencia")},
        "url": str(dados.url) if dados.url else None,
        "inicio": _iso(dados.inicio),
        "fim": _iso(dados.fim),
        "data_confirmada": int(dados.data_confirmada),
        "gratuito": None if dados.gratuito is None else int(dados.gratuito),
        "verificado_em": _iso(dados.verificado_em),
        "status": status,
        "origem": origem,
        "chave": chave,
        "publicado_em": publicado_em,
        "criado_em": int(agora),
        "atualizado_em": int(agora),
        "busca": _texto_busca(campos),
    }
    colunas = ", ".join(linha)
    marcas = ", ".join(f":{c}" for c in linha)
    with transacao(conn):
        cur = conn.execute(
            f"INSERT OR IGNORE INTO itens ({colunas}) VALUES ({marcas})",  # noqa: S608 — colunas fixas
            linha,
        )
    return cur.lastrowid if cur.rowcount else None


def alterar_item(conn: sqlite3.Connection, item_id: int, alt: AlteracaoItem, agora: float) -> None:
    atual = obter_item(conn, item_id, publico=False)
    mudancas = alt.model_dump(exclude_unset=True)
    if not mudancas:
        return
    # Revalida o item completo com as mudanças aplicadas.
    base = {k: atual[k] for k in ItemEntrada.model_fields if k in atual}
    base.update({k: v for k, v in mudancas.items() if k != "status"})
    try:
        valido = ItemEntrada.model_validate(base)
    except ValueError as exc:
        raise ErroDeConteudo(str(exc)) from exc
    sets = {k: v for k, v in mudancas.items()}
    for k in ("inicio", "fim"):
        if k in sets:
            sets[k] = _iso(sets[k])
    if "data_confirmada" in sets:
        sets["data_confirmada"] = int(sets["data_confirmada"])
    sets["busca"] = _texto_busca(valido.model_dump())
    sets["atualizado_em"] = int(agora)
    atribuicoes = ", ".join(f"{k} = :{k}" for k in sets)
    with transacao(conn):
        conn.execute(
            f"UPDATE itens SET {atribuicoes} WHERE id = :id",  # noqa: S608 — chaves validadas
            {**sets, "id": item_id},
        )


def remover_item(conn: sqlite3.Connection, item_id: int) -> None:
    with transacao(conn):
        cur = conn.execute("DELETE FROM itens WHERE id = ?", (item_id,))
    if cur.rowcount == 0:
        raise ItemNaoEncontrado(item_id)


def _publico(linha: sqlite3.Row, interno: bool = False) -> dict[str, Any]:
    d = dict(linha)
    d.pop("busca", None)
    d["data_confirmada"] = bool(d["data_confirmada"])
    d["gratuito"] = None if d["gratuito"] is None else bool(d["gratuito"])
    d["tipo_nome"] = TIPOS.get(d["tipo"], d["tipo"])
    if not interno:
        for campo in ("status", "origem", "chave", "criado_em", "atualizado_em"):
            d.pop(campo, None)
    return d


def obter_item(conn: sqlite3.Connection, item_id: int, publico: bool = True) -> dict[str, Any]:
    linha = conn.execute(
        "SELECT * FROM itens WHERE id = ? AND (status = 'aprovado' OR ?)", (item_id, not publico)
    ).fetchone()
    if linha is None:
        raise ItemNaoEncontrado(item_id)
    return _publico(linha, interno=not publico)


def _escapar_like(termo: str) -> str:
    return termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def listar_itens(
    conn: sqlite3.Connection,
    hoje: date,
    *,
    tipos: list[str] | None = None,
    regiao: str | None = None,
    busca: str | None = None,
    quando: Literal["todos", "futuros", "passados"] = "todos",
    status: str = "aprovado",
    limite: int = 50,
    deslocamento: int = 0,
    interno: bool = False,
) -> dict[str, Any]:
    """Lista com filtros. Futuros = sem data (programa permanente) ou que ainda não terminaram."""
    condicoes = ["status = :status"]
    params: dict[str, Any] = {"status": status, "hoje": hoje.isoformat()}
    if tipos:
        validos = [t for t in tipos if t in TIPOS]
        if not validos:
            return {"total": 0, "itens": []}
        marcas = []
        for i, t in enumerate(validos):
            params[f"t{i}"] = t
            marcas.append(f":t{i}")
        condicoes.append(f"tipo IN ({', '.join(marcas)})")
    if regiao:
        # "Todo o estado" e "On-line" servem para qualquer região.
        condicoes.append("(regiao = :regiao OR regiao IN ('Todo o estado', 'On-line', 'Nacional'))")
        params["regiao"] = regiao
    for i, termo in enumerate(normalizar(busca).split()[:8]):
        params[f"b{i}"] = f"%{_escapar_like(termo)}%"
        condicoes.append(f"busca LIKE :b{i} ESCAPE '\\'")
    if quando == "futuros":
        condicoes.append("(inicio IS NULL OR COALESCE(fim, inicio) >= :hoje)")
    elif quando == "passados":
        condicoes.append("COALESCE(fim, inicio) < :hoje")

    onde = " AND ".join(condicoes)
    ordem = (
        "CASE WHEN inicio IS NULL THEN 1 ELSE 0 END, inicio, titulo"
        if quando == "futuros"
        else "COALESCE(publicado_em, criado_em) DESC, id DESC"
    )
    total = conn.execute(f"SELECT COUNT(*) FROM itens WHERE {onde}", params).fetchone()[0]  # noqa: S608
    linhas = conn.execute(
        f"SELECT * FROM itens WHERE {onde} ORDER BY {ordem} LIMIT :lim OFFSET :off",  # noqa: S608
        {**params, "lim": limite, "off": deslocamento},
    ).fetchall()
    return {"total": total, "itens": [_publico(lin, interno=interno) for lin in linhas]}


def contagens(conn: sqlite3.Connection) -> dict[str, Any]:
    por_status = dict(conn.execute("SELECT status, COUNT(*) FROM itens GROUP BY status").fetchall())
    por_tipo = dict(
        conn.execute(
            "SELECT tipo, COUNT(*) FROM itens WHERE status = 'aprovado' GROUP BY tipo"
        ).fetchall()
    )
    return {"por_status": por_status, "por_tipo": por_tipo}


# ---------------------------------------------------------------- datas ambientais


def _proxima_ocorrencia(regra: str, a_partir: date) -> date:
    """Calcula a próxima data (>= a_partir) de uma regra anual."""
    partes = regra.split(":")
    for ano in (a_partir.year, a_partir.year + 1):
        if partes[0] == "fixa":
            mes, dia = (int(x) for x in partes[1].split("-"))
            if mes == 2 and dia == 29 and not calendar.isleap(ano):
                continue
            candidata = date(ano, mes, dia)
        elif partes[0] == "ordinal":
            n, dia_semana, mes = int(partes[1]), DIAS_SEMANA[partes[2]], int(partes[3])
            dias = [
                date(ano, mes, d)
                for d in range(1, calendar.monthrange(ano, mes)[1] + 1)
                if date(ano, mes, d).weekday() == dia_semana
            ]
            candidata = dias[n - 1] if n > 0 else dias[n]
        else:
            raise ErroDeConteudo(f"regra de data desconhecida: {regra}")
        if candidata >= a_partir:
            return candidata
    raise ErroDeConteudo(f"regra sem ocorrência: {regra}")  # pragma: no cover


def validar_regra(regra: str) -> str:
    _proxima_ocorrencia(regra, date(2000, 1, 1))
    return regra


def datas_ambientais(conn: sqlite3.Connection, hoje: date, dias: int = 366) -> list[dict[str, Any]]:
    limite = hoje + timedelta(days=dias)
    saida = []
    for linha in conn.execute("SELECT nome, regra, descricao, fonte_url FROM datas_ambientais"):
        quando = _proxima_ocorrencia(linha["regra"], hoje)
        if quando <= limite:
            saida.append(
                {
                    "nome": linha["nome"],
                    "data": quando.isoformat(),
                    "faltam_dias": (quando - hoje).days,
                    "descricao": linha["descricao"],
                    "fonte_url": linha["fonte_url"],
                }
            )
    return sorted(saida, key=lambda d: (d["data"], d["nome"]))


# ---------------------------------------------------------------- dicas


def listar_dicas(conn: sqlite3.Connection, categoria: str | None = None) -> list[dict[str, Any]]:
    if categoria:
        linhas = conn.execute(
            "SELECT * FROM dicas WHERE categoria = ? ORDER BY id", (categoria,)
        ).fetchall()
    else:
        linhas = conn.execute("SELECT * FROM dicas ORDER BY categoria, id").fetchall()
    return [dict(lin) for lin in linhas]


def dica_do_dia(conn: sqlite3.Connection, hoje: date) -> dict[str, Any] | None:
    total = conn.execute("SELECT COUNT(*) FROM dicas").fetchone()[0]
    if not total:
        return None
    linha = conn.execute(
        "SELECT * FROM dicas ORDER BY id LIMIT 1 OFFSET ?", (hoje.toordinal() % total,)
    ).fetchone()
    return dict(linha)


# ---------------------------------------------------------------- página inicial


def _para_estudantes_primeiro(itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Na página inicial, o que um estudante pode fazer aparece antes do que é só para adultos."""
    return sorted(itens, key=lambda i: "adult" in normalizar(i.get("publico")))


def inicio(conn: sqlite3.Connection, hoje: date) -> dict[str, Any]:
    agenda = listar_itens(conn, hoje, tipos=list(TIPOS_AGENDA), quando="futuros", limite=200)
    proximos = [i for i in agenda["itens"] if i["inicio"]][:6]
    return {
        "hoje": hoje.isoformat(),
        "proximos_eventos": proximos,
        "proximas_datas": datas_ambientais(conn, hoje, dias=75)[:6],
        "noticias": listar_itens(conn, hoje, tipos=["noticia"], limite=6)["itens"],
        "oportunidades": _para_estudantes_primeiro(
            listar_itens(
                conn, hoje, tipos=["voluntariado", "vaga", "curso"], quando="futuros", limite=200
            )["itens"]
        )[:6],
        "dica_do_dia": dica_do_dia(conn, hoje),
        "contagens": contagens(conn)["por_tipo"],
    }


# ---------------------------------------------------------------- fontes de notícias


class FonteEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    nome: str = Field(min_length=2, max_length=120)
    url: HttpUrl
    tipo_padrao: str = "noticia"
    regiao_padrao: str = ""
    exigir_palavras: bool = True
    ativa: bool = True
    observacao: str = Field(default="", max_length=300)

    _tipo = field_validator("tipo_padrao")(_checar_tipo)
    _regiao = field_validator("regiao_padrao")(_checar_regiao)


def criar_fonte(conn: sqlite3.Connection, f: FonteEntrada) -> int:
    try:
        with transacao(conn):
            cur = conn.execute(
                """INSERT INTO fontes (nome, url, tipo_padrao, regiao_padrao, exigir_palavras,
                                       ativa, observacao)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f.nome,
                    str(f.url),
                    f.tipo_padrao,
                    f.regiao_padrao,
                    int(f.exigir_palavras),
                    int(f.ativa),
                    f.observacao,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ErroDeConteudo("Já existe uma fonte com esse endereço") from exc
    return int(cur.lastrowid or 0)


def listar_fontes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    linhas = conn.execute("SELECT * FROM fontes ORDER BY ativa DESC, nome").fetchall()
    saida = []
    for lin in linhas:
        d = dict(lin)
        d["ativa"] = bool(d["ativa"])
        d["exigir_palavras"] = bool(d["exigir_palavras"])
        saida.append(d)
    return saida


def ativar_fonte(conn: sqlite3.Connection, fonte_id: int, ativa: bool) -> None:
    with transacao(conn):
        cur = conn.execute(
            "UPDATE fontes SET ativa = ?, falhas_seguidas = 0 WHERE id = ?", (int(ativa), fonte_id)
        )
    if cur.rowcount == 0:
        raise ItemNaoEncontrado(fonte_id)


# ---------------------------------------------------------------- semente


def importar_semente(
    conn: sqlite3.Connection, agora: float, caminho: Path = CAMINHO_SEMENTE
) -> dict[str, int]:
    """Carrega o conteúdo inicial pesquisado. Pode rodar várias vezes: nada duplica.

    Itens da semente já entram aprovados porque foram pesquisados e conferidos.
    """
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    novos = {"itens": 0, "datas": 0, "dicas": 0, "fontes": 0}

    for bruto in dados.get("itens", []):
        chave = "semente:" + bruto.pop("chave")
        publicado = bruto.pop("publicado_em", None)
        publicado_em = None
        if publicado:
            d = date.fromisoformat(publicado)
            publicado_em = int(
                datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp()
            )
        item = ItemEntrada.model_validate(bruto)
        if criar_item(
            conn,
            item,
            agora,
            status="aprovado",
            origem="semente",
            chave=chave,
            publicado_em=publicado_em,
        ):
            novos["itens"] += 1

    with transacao(conn):
        for d in dados.get("datas", []):
            cur = conn.execute(
                "INSERT OR IGNORE INTO datas_ambientais (nome, regra, descricao, fonte_url) "
                "VALUES (?, ?, ?, ?)",
                (d["nome"], validar_regra(d["regra"]), d.get("descricao", ""), d.get("fonte_url")),
            )
            novos["datas"] += cur.rowcount
        for d in dados.get("dicas", []):
            cur = conn.execute(
                "INSERT OR IGNORE INTO dicas (categoria, titulo, texto) VALUES (?, ?, ?)",
                (d["categoria"], d["titulo"], d["texto"]),
            )
            novos["dicas"] += cur.rowcount

    for f in dados.get("fontes", []):
        try:
            criar_fonte(conn, FonteEntrada.model_validate(f))
            novos["fontes"] += 1
        except ErroDeConteudo:
            pass  # já cadastrada
    return novos


def rotulo_mes(d: date) -> str:
    return f"{MESES[d.month - 1]}/{d.year}"
