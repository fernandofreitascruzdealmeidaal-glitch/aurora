"""Banco de dados SQLite.

Escolhas para não perder dados:
- WAL: leituras e escritas ao mesmo tempo sem travar o painel.
- synchronous=NORMAL + WAL: seguro contra queda de energia do computador.
- CHECK no próprio banco: mesmo que um bug passe pela validação do Python, o banco
  recusa valores impossíveis.
- UNIQUE(dispositivo_id, id_leitura): reenviar a mesma leitura nunca duplica dados.
- Horários guardados como segundos UTC (inteiros): sem confusão de fuso nem de formato.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from aurora.limites import FAIXAS_FISICAS

VERSAO_ESQUEMA = 3


def _check(coluna: str) -> str:
    minimo, maximo = FAIXAS_FISICAS[coluna]
    return f"CHECK ({coluna} IS NULL OR ({coluna} BETWEEN {minimo} AND {maximo}))"


ESQUEMA_V1 = f"""
CREATE TABLE IF NOT EXISTS dispositivos (
    id             TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 64),
    sala           TEXT NOT NULL CHECK (length(sala) BETWEEN 1 AND 100),
    hash_chave     TEXT NOT NULL CHECK (length(hash_chave) = 64),
    criado_em      INTEGER NOT NULL,
    ultimo_contato INTEGER
);

CREATE TABLE IF NOT EXISTS leituras (
    id             INTEGER PRIMARY KEY,
    dispositivo_id TEXT NOT NULL REFERENCES dispositivos(id) ON DELETE CASCADE,
    id_leitura     TEXT NOT NULL,
    medido_em      INTEGER NOT NULL,
    recebido_em    INTEGER NOT NULL,
    temperatura_c  REAL {_check("temperatura_c")},
    umidade_pct    REAL {_check("umidade_pct")},
    co2_ppm        REAL {_check("co2_ppm")},
    CHECK (temperatura_c IS NOT NULL OR umidade_pct IS NOT NULL OR co2_ppm IS NOT NULL),
    UNIQUE (dispositivo_id, id_leitura)
);

CREATE INDEX IF NOT EXISTS idx_leituras_dispositivo_tempo
    ON leituras (dispositivo_id, medido_em);
"""


# Versão 2: base de conteúdo de sustentabilidade (notícias, eventos, oportunidades).
_DATA = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"  # AAAA-MM-DD
ESQUEMA_V2 = f"""
CREATE TABLE IF NOT EXISTS itens (
    id              INTEGER PRIMARY KEY,
    tipo            TEXT NOT NULL,
    titulo          TEXT NOT NULL CHECK (length(titulo) BETWEEN 3 AND 200),
    resumo          TEXT NOT NULL DEFAULT '' CHECK (length(resumo) <= 1200),
    url             TEXT CHECK (url IS NULL OR url LIKE 'http://%' OR url LIKE 'https://%'),
    fonte           TEXT NOT NULL DEFAULT '' CHECK (length(fonte) <= 120),
    instituicao     TEXT NOT NULL DEFAULT '' CHECK (length(instituicao) <= 120),
    cidade          TEXT NOT NULL DEFAULT '' CHECK (length(cidade) <= 80),
    regiao          TEXT NOT NULL DEFAULT '',
    local           TEXT NOT NULL DEFAULT '' CHECK (length(local) <= 160),
    publico         TEXT NOT NULL DEFAULT '' CHECK (length(publico) <= 160),
    inicio          TEXT CHECK (inicio IS NULL OR inicio GLOB '{_DATA}'),
    fim             TEXT CHECK (fim IS NULL OR fim GLOB '{_DATA}'),
    data_confirmada INTEGER NOT NULL DEFAULT 1 CHECK (data_confirmada IN (0, 1)),
    recorrencia     TEXT NOT NULL DEFAULT '' CHECK (length(recorrencia) <= 120),
    gratuito        INTEGER CHECK (gratuito IS NULL OR gratuito IN (0, 1)),
    status          TEXT NOT NULL DEFAULT 'pendente'
                    CHECK (status IN ('pendente', 'aprovado', 'rejeitado')),
    origem          TEXT NOT NULL DEFAULT 'manual'
                    CHECK (origem IN ('manual', 'coleta', 'semente')),
    chave           TEXT UNIQUE,
    publicado_em    INTEGER,
    verificado_em   TEXT,
    criado_em       INTEGER NOT NULL,
    atualizado_em   INTEGER NOT NULL,
    busca           TEXT NOT NULL DEFAULT '',
    CHECK (fim IS NULL OR inicio IS NULL OR fim >= inicio)
);
CREATE INDEX IF NOT EXISTS idx_itens_status_tipo ON itens (status, tipo, inicio);
CREATE INDEX IF NOT EXISTS idx_itens_publicado ON itens (status, publicado_em);

CREATE TABLE IF NOT EXISTS fontes (
    id               INTEGER PRIMARY KEY,
    nome             TEXT NOT NULL CHECK (length(nome) BETWEEN 2 AND 120),
    url              TEXT NOT NULL UNIQUE CHECK (url LIKE 'http://%' OR url LIKE 'https://%'),
    tipo_padrao      TEXT NOT NULL DEFAULT 'noticia',
    regiao_padrao    TEXT NOT NULL DEFAULT '',
    exigir_palavras  INTEGER NOT NULL DEFAULT 1 CHECK (exigir_palavras IN (0, 1)),
    ativa            INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0, 1)),
    ultima_coleta    INTEGER,
    ultimo_erro      TEXT,
    falhas_seguidas  INTEGER NOT NULL DEFAULT 0,
    observacao       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS datas_ambientais (
    id        INTEGER PRIMARY KEY,
    nome      TEXT NOT NULL UNIQUE,
    regra     TEXT NOT NULL,  -- "fixa:MM-DD" ou "ordinal:N:DIA_SEMANA:MES" (N=-1 = último)
    descricao TEXT NOT NULL DEFAULT '',
    fonte_url TEXT
);

CREATE TABLE IF NOT EXISTS dicas (
    id        INTEGER PRIMARY KEY,
    categoria TEXT NOT NULL,
    titulo    TEXT NOT NULL UNIQUE,
    texto     TEXT NOT NULL
);
"""

# Chat com o Claude: só contagens por dia (as conversas não são guardadas).
ESQUEMA_V3 = """
CREATE TABLE IF NOT EXISTS chat_uso (
    dia       TEXT PRIMARY KEY,
    mensagens INTEGER NOT NULL DEFAULT 0 CHECK (mensagens >= 0),
    entrada   INTEGER NOT NULL DEFAULT 0 CHECK (entrada >= 0),
    saida     INTEGER NOT NULL DEFAULT 0 CHECK (saida >= 0)
);
"""


def conectar(caminho: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(caminho),
        timeout=10,  # espera até 10 s se outro processo estiver escrevendo
        check_same_thread=False,  # cada requisição usa a sua própria conexão
        isolation_level=None,  # controlamos as transações explicitamente
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(caminho) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def abrir(caminho: str | Path) -> Iterator[sqlite3.Connection]:
    conn = conectar(caminho)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transacao(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Tudo ou nada: se algo falhar no meio, nada é gravado."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def inicializar(caminho: str | Path) -> None:
    """Cria as tabelas (se não existirem) e aplica migrações pendentes."""
    if str(caminho) != ":memory:":
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    with abrir(caminho) as conn:
        versao = conn.execute("PRAGMA user_version").fetchone()[0]
        if versao > VERSAO_ESQUEMA:
            raise RuntimeError(
                f"O banco está na versão {versao}, mais nova que este programa "
                f"({VERSAO_ESQUEMA}). Atualize o servidor antes de usar este banco."
            )
        if versao < 1:
            conn.executescript(ESQUEMA_V1)
            conn.execute("PRAGMA user_version = 1")
        if versao < 2:
            conn.executescript(ESQUEMA_V2)
            conn.execute("PRAGMA user_version = 2")
        if versao < 3:
            conn.executescript(ESQUEMA_V3)
            conn.execute("PRAGMA user_version = 3")
        # Próximas migrações: `if versao < 4: ...` — nunca altere as anteriores.


def verificar(conn: sqlite3.Connection) -> bool:
    """Teste rápido de que o banco responde e está íntegro."""
    return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
