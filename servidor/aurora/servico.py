"""Regras da Aurora: guardar leituras, consultar histórico, resumir e achar padrões.

Nada aqui conhece HTTP: as funções recebem uma conexão e devolvem dados. Assim dá para
testar cada regra isoladamente e reaproveitar no terminal (cli.py).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from aurora import seguranca
from aurora.config import Configuracao
from aurora.db import transacao
from aurora.limites import METRICAS, REFERENCIAS, avaliar_leitura
from aurora.modelos import LeituraEntrada, Rejeicao, ResultadoLote

PADRAO_ID_DISPOSITIVO = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ErroDeDados(ValueError):
    """Pedido com dados inválidos (ex.: ID de dispositivo mal formado)."""


class NaoEncontrado(LookupError):
    """Dispositivo inexistente."""


def iso(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- dispositivos


@dataclass(frozen=True)
class DispositivoNovo:
    id: str
    sala: str
    chave: str  # mostrada só uma vez


def validar_id_dispositivo(dispositivo_id: str) -> str:
    if not PADRAO_ID_DISPOSITIVO.fullmatch(dispositivo_id or ""):
        raise ErroDeDados(
            "ID do dispositivo deve ter de 1 a 64 caracteres: letras minúsculas, números, "
            "'-' ou '_' (ex.: sala-101)"
        )
    return dispositivo_id


def registrar_dispositivo(
    conn: sqlite3.Connection, dispositivo_id: str, sala: str, agora: float
) -> DispositivoNovo:
    validar_id_dispositivo(dispositivo_id)
    sala = (sala or "").strip()
    if not 1 <= len(sala) <= 100:
        raise ErroDeDados("O nome da sala deve ter de 1 a 100 caracteres")
    chave = seguranca.gerar_chave()
    try:
        with transacao(conn):
            conn.execute(
                "INSERT INTO dispositivos (id, sala, hash_chave, criado_em) VALUES (?, ?, ?, ?)",
                (dispositivo_id, sala, seguranca.hash_chave(chave), int(agora)),
            )
    except sqlite3.IntegrityError as exc:
        raise ErroDeDados(f"Já existe um dispositivo com o ID {dispositivo_id!r}") from exc
    return DispositivoNovo(dispositivo_id, sala, chave)


def trocar_chave(conn: sqlite3.Connection, dispositivo_id: str) -> str:
    chave = seguranca.gerar_chave()
    with transacao(conn):
        cur = conn.execute(
            "UPDATE dispositivos SET hash_chave = ? WHERE id = ?",
            (seguranca.hash_chave(chave), dispositivo_id),
        )
    if cur.rowcount == 0:
        raise NaoEncontrado(dispositivo_id)
    return chave


def remover_dispositivo(conn: sqlite3.Connection, dispositivo_id: str) -> None:
    with transacao(conn):
        cur = conn.execute("DELETE FROM dispositivos WHERE id = ?", (dispositivo_id,))
    if cur.rowcount == 0:
        raise NaoEncontrado(dispositivo_id)


def exigir_dispositivo(conn: sqlite3.Connection, dispositivo_id: str) -> sqlite3.Row:
    linha = conn.execute(
        "SELECT id, sala, criado_em, ultimo_contato FROM dispositivos WHERE id = ?",
        (dispositivo_id,),
    ).fetchone()
    if linha is None:
        raise NaoEncontrado(dispositivo_id)
    return linha


def autenticar(conn: sqlite3.Connection, dispositivo_id: str | None, chave: str | None) -> bool:
    linha = None
    if dispositivo_id:
        linha = conn.execute(
            "SELECT hash_chave FROM dispositivos WHERE id = ?", (dispositivo_id,)
        ).fetchone()
    return seguranca.chave_confere(chave, linha["hash_chave"] if linha else None)


# ---------------------------------------------------------------- recebimento


def _motivo(erro: ValidationError) -> str:
    partes = []
    for e in erro.errors()[:3]:
        campo = ".".join(str(p) for p in e.get("loc", ())) or "leitura"
        partes.append(f"{campo}: {e.get('msg', 'inválido')}")
    return "; ".join(partes)


def ingerir(
    conn: sqlite3.Connection,
    dispositivo_id: str,
    itens: list[Any],
    agora: float,
    cfg: Configuracao,
) -> ResultadoLote:
    """Valida cada leitura separadamente e grava as boas numa única transação.

    - Leitura inválida: recusada com motivo; as demais seguem.
    - Leitura repetida (mesmo id_leitura): ignorada sem erro — reenviar é seguro.
    """
    rejeitadas: list[Rejeicao] = []
    linhas: list[tuple[Any, ...]] = []
    recebido = int(agora)

    for indice, bruto in enumerate(itens):
        id_bruto = bruto.get("id_leitura") if isinstance(bruto, dict) else None
        id_texto = id_bruto if isinstance(id_bruto, str) else None
        try:
            leitura = LeituraEntrada.model_validate(bruto)
        except ValidationError as exc:
            rejeitadas.append(Rejeicao(indice=indice, id_leitura=id_texto, motivo=_motivo(exc)))
            continue

        if leitura.medido_em is not None:
            medido = leitura.medido_em.timestamp()
        else:
            medido = agora - (leitura.idade_s or 0)

        if medido > agora + cfg.tolerancia_futuro_s:
            rejeitadas.append(
                Rejeicao(
                    indice=indice,
                    id_leitura=leitura.id_leitura,
                    motivo="horário no futuro — o relógio do dispositivo está adiantado?",
                )
            )
            continue
        if medido < agora - cfg.idade_maxima_s:
            rejeitadas.append(
                Rejeicao(
                    indice=indice, id_leitura=leitura.id_leitura, motivo="leitura antiga demais"
                )
            )
            continue

        linhas.append(
            (
                dispositivo_id,
                leitura.id_leitura,
                int(min(medido, agora)),  # pequeno adiantamento tolerado vira "agora"
                recebido,
                leitura.temperatura_c,
                leitura.umidade_pct,
                leitura.co2_ppm,
            )
        )

    with transacao(conn):
        antes = conn.total_changes
        conn.executemany(
            """INSERT OR IGNORE INTO leituras
               (dispositivo_id, id_leitura, medido_em, recebido_em,
                temperatura_c, umidade_pct, co2_ppm)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            linhas,
        )
        aceitas = conn.total_changes - antes
        conn.execute(
            "UPDATE dispositivos SET ultimo_contato = ? WHERE id = ?", (recebido, dispositivo_id)
        )

    return ResultadoLote(aceitas=aceitas, duplicadas=len(linhas) - aceitas, rejeitadas=rejeitadas)


# ---------------------------------------------------------------- consultas


def _leitura_publica(linha: sqlite3.Row) -> dict[str, Any]:
    valores = {m: linha[m] for m in METRICAS}
    return {"medido_em": iso(linha["medido_em"]), **valores}


def status_dispositivos(
    conn: sqlite3.Connection, agora: float, cfg: Configuracao
) -> list[dict[str, Any]]:
    limite_online = max(3 * cfg.intervalo_esperado_s, 180)
    resultado = []
    for d in conn.execute("SELECT id, sala, ultimo_contato FROM dispositivos ORDER BY sala, id"):
        ultima = conn.execute(
            """SELECT medido_em, temperatura_c, umidade_pct, co2_ppm FROM leituras
               WHERE dispositivo_id = ? ORDER BY medido_em DESC LIMIT 1""",
            (d["id"],),
        ).fetchone()
        contato = d["ultimo_contato"]
        resultado.append(
            {
                "id": d["id"],
                "sala": d["sala"],
                "ultimo_contato": iso(contato),
                "online": contato is not None and agora - contato <= limite_online,
                "ultima_leitura": _leitura_publica(ultima) if ultima else None,
                "avisos": avaliar_leitura({m: ultima[m] for m in METRICAS}) if ultima else [],
            }
        )
    return resultado


def listar_leituras(
    conn: sqlite3.Connection, dispositivo_id: str, inicio: int, fim: int, limite: int
) -> list[dict[str, Any]]:
    exigir_dispositivo(conn, dispositivo_id)
    linhas = conn.execute(
        """SELECT medido_em, temperatura_c, umidade_pct, co2_ppm FROM leituras
           WHERE dispositivo_id = ? AND medido_em BETWEEN ? AND ?
           ORDER BY medido_em DESC LIMIT ?""",
        (dispositivo_id, inicio, fim, limite),
    ).fetchall()
    return [_leitura_publica(linha) for linha in linhas]


def serie(
    conn: sqlite3.Connection, dispositivo_id: str, inicio: int, fim: int, balde_s: int
) -> list[dict[str, Any]]:
    """Médias, mínimos e máximos agrupados em intervalos (para os gráficos)."""
    exigir_dispositivo(conn, dispositivo_id)
    colunas = ", ".join(
        f"AVG({m}) AS {m}_media, MIN({m}) AS {m}_min, MAX({m}) AS {m}_max" for m in METRICAS
    )
    sql = f"""SELECT (medido_em / :b) * :b AS balde, COUNT(*) AS n, {colunas}
              FROM leituras
              WHERE dispositivo_id = :d AND medido_em BETWEEN :i AND :f
              GROUP BY balde ORDER BY balde"""  # noqa: S608 — colunas vêm de constante, não do usuário
    linhas = conn.execute(sql, {"b": balde_s, "d": dispositivo_id, "i": inicio, "f": fim})
    saida = []
    for linha in linhas:
        ponto: dict[str, Any] = {"inicio": iso(linha["balde"]), "n": linha["n"]}
        for m in METRICAS:
            media = linha[f"{m}_media"]
            ponto[m] = (
                None
                if media is None
                else {
                    "media": round(media, 2),
                    "min": linha[f"{m}_min"],
                    "max": linha[f"{m}_max"],
                }
            )
        saida.append(ponto)
    return saida


def resumo(conn: sqlite3.Connection, dispositivo_id: str, inicio: int, fim: int) -> dict[str, Any]:
    """Estatísticas do período e % do tempo fora da faixa de conforto."""
    exigir_dispositivo(conn, dispositivo_id)
    partes = []
    for m in METRICAS:
        ref = REFERENCIAS[m]
        cond = []
        if ref.atencao_acima is not None:
            cond.append(f"{m} > {ref.atencao_acima}")
        if ref.atencao_abaixo is not None:
            cond.append(f"{m} < {ref.atencao_abaixo}")
        fora = " OR ".join(cond) or "0"
        partes.append(
            f"MIN({m}) AS {m}_min, MAX({m}) AS {m}_max, AVG({m}) AS {m}_media, "
            f"COUNT({m}) AS {m}_n, SUM(CASE WHEN {fora} THEN 1 ELSE 0 END) AS {m}_fora"
        )
    sql = f"""SELECT COUNT(*) AS total, {", ".join(partes)} FROM leituras
              WHERE dispositivo_id = ? AND medido_em BETWEEN ? AND ?"""  # noqa: S608 — constantes
    linha = conn.execute(sql, (dispositivo_id, inicio, fim)).fetchone()
    metricas = {}
    for m in METRICAS:
        n = linha[f"{m}_n"]
        metricas[m] = (
            None
            if not n
            else {
                "min": linha[f"{m}_min"],
                "max": linha[f"{m}_max"],
                "media": round(linha[f"{m}_media"], 2),
                "leituras": n,
                "fora_da_referencia_pct": round(100.0 * (linha[f"{m}_fora"] or 0) / n, 1),
            }
        )
    return {
        "inicio": iso(inicio),
        "fim": iso(fim),
        "leituras": linha["total"],
        "metricas": metricas,
    }


def padrao_por_hora(
    conn: sqlite3.Connection, dispositivo_id: str, inicio: int, fim: int, fuso: str
) -> list[dict[str, Any]]:
    """Média de cada medição por hora do dia (horário local).

    Mostra padrões como "o CO₂ sobe sempre no fim da manhã" — útil para decidir
    quando abrir as janelas ou fazer intervalos.
    """
    exigir_dispositivo(conn, dispositivo_id)
    tz = ZoneInfo(fuso)
    somas = [{m: 0.0 for m in METRICAS} for _ in range(24)]
    contagens = [{m: 0 for m in METRICAS} for _ in range(24)]
    linhas = conn.execute(
        """SELECT medido_em, temperatura_c, umidade_pct, co2_ppm FROM leituras
           WHERE dispositivo_id = ? AND medido_em BETWEEN ? AND ?""",
        (dispositivo_id, inicio, fim),
    )
    for linha in linhas:
        hora = datetime.fromtimestamp(linha["medido_em"], tz=tz).hour
        for m in METRICAS:
            if linha[m] is not None:
                somas[hora][m] += linha[m]
                contagens[hora][m] += 1
    return [
        {
            "hora": h,
            **{
                m: round(somas[h][m] / contagens[h][m], 2) if contagens[h][m] else None
                for m in METRICAS
            },
            "leituras": max(contagens[h].values()),
        }
        for h in range(24)
    ]


def linhas_csv(
    conn: sqlite3.Connection, dispositivo_id: str, inicio: int, fim: int, fuso: str
) -> Iterator[list[str]]:
    exigir_dispositivo(conn, dispositivo_id)
    tz = ZoneInfo(fuso)
    yield ["medido_em_utc", "medido_em_local", "temperatura_c", "umidade_pct", "co2_ppm"]
    cursor = conn.execute(
        """SELECT medido_em, temperatura_c, umidade_pct, co2_ppm FROM leituras
           WHERE dispositivo_id = ? AND medido_em BETWEEN ? AND ? ORDER BY medido_em""",
        (dispositivo_id, inicio, fim),
    )
    for linha in cursor:
        local = datetime.fromtimestamp(linha["medido_em"], tz=tz).strftime("%Y-%m-%d %H:%M:%S")
        yield [
            iso(linha["medido_em"]) or "",
            local,
            *("" if linha[m] is None else f"{linha[m]:g}" for m in METRICAS),
        ]


def apagar_antigas(conn: sqlite3.Connection, antes_de: int) -> int:
    with transacao(conn):
        cur = conn.execute("DELETE FROM leituras WHERE medido_em < ?", (antes_de,))
    return cur.rowcount
