"""Banco, configuração, segurança, referências e comandos de terminal."""

from __future__ import annotations

import sqlite3

import pytest

from aurora import cli, db, seguranca, servico
from aurora.config import Configuracao, ErroDeConfiguracao
from aurora.limites import REFERENCIAS, Nivel, avaliar_leitura
from tests.conftest import AGORA

# ------------------------------------------------------------------ banco


def test_banco_recusa_valor_impossivel_mesmo_sem_python(tmp_path):
    caminho = tmp_path / "b.db"
    db.inicializar(caminho)
    with db.abrir(caminho) as conn:
        servico.registrar_dispositivo(conn, "s1", "S1", AGORA)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO leituras (dispositivo_id, id_leitura, medido_em, recebido_em, co2_ppm)"
                " VALUES ('s1', 'x', 0, 0, 50000)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # leitura vazia
            conn.execute(
                "INSERT INTO leituras (dispositivo_id, id_leitura, medido_em, recebido_em)"
                " VALUES ('s1', 'y', 0, 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # dispositivo inexistente
            conn.execute(
                "INSERT INTO leituras (dispositivo_id, id_leitura, medido_em, recebido_em, co2_ppm)"
                " VALUES ('fantasma', 'z', 0, 0, 500)"
            )


def test_transacao_desfaz_tudo_em_erro(tmp_path):
    caminho = tmp_path / "b.db"
    db.inicializar(caminho)
    with db.abrir(caminho) as conn:
        with pytest.raises(RuntimeError), db.transacao(conn):
            conn.execute(
                "INSERT INTO dispositivos (id, sala, hash_chave, criado_em) VALUES (?,?,?,?)",
                ("s1", "S1", "a" * 64, 0),
            )
            raise RuntimeError("queda no meio")
        assert conn.execute("SELECT COUNT(*) FROM dispositivos").fetchone()[0] == 0


def test_inicializar_e_idempotente_e_wal(tmp_path):
    caminho = tmp_path / "sub" / "b.db"
    db.inicializar(caminho)
    db.inicializar(caminho)
    with db.abrir(caminho) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.VERSAO_ESQUEMA
        assert db.verificar(conn)


def test_banco_de_versao_futura_e_recusado(tmp_path):
    caminho = tmp_path / "b.db"
    with db.abrir(caminho) as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="mais nova"):
        db.inicializar(caminho)


def test_banco_em_memoria():
    db.inicializar(":memory:")  # não deve tentar criar pasta
    with db.abrir(":memory:") as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_remover_dispositivo_apaga_leituras(amb):
    amb.enviar([{"id_leitura": "a", "idade_s": 0, "co2_ppm": 500}])
    with db.abrir(amb.cfg.caminho_banco) as conn:
        servico.remover_dispositivo(conn, "sala-101")
        assert conn.execute("SELECT COUNT(*) FROM leituras").fetchone()[0] == 0
        with pytest.raises(servico.NaoEncontrado):
            servico.remover_dispositivo(conn, "sala-101")


def test_apagar_antigas(amb):
    amb.enviar(
        [
            {"id_leitura": "velha", "idade_s": 5 * 86400, "co2_ppm": 500},
            {"id_leitura": "nova", "idade_s": 0, "co2_ppm": 500},
        ]
    )
    with db.abrir(amb.cfg.caminho_banco) as conn:
        assert servico.apagar_antigas(conn, int(AGORA) - 86400) == 1


# ------------------------------------------------------------------ dispositivos e chaves


@pytest.mark.parametrize("ruim", ["", "Sala 101", "SALA", "-x", "a" * 65, "sala/1", "ç"])
def test_id_de_dispositivo_invalido(tmp_path, ruim):
    db.inicializar(tmp_path / "b.db")
    with db.abrir(tmp_path / "b.db") as conn, pytest.raises(servico.ErroDeDados):
        servico.registrar_dispositivo(conn, ruim, "Sala", AGORA)


@pytest.mark.parametrize("sala", ["", "   ", "x" * 101])
def test_nome_de_sala_invalido(tmp_path, sala):
    db.inicializar(tmp_path / "b.db")
    with db.abrir(tmp_path / "b.db") as conn, pytest.raises(servico.ErroDeDados):
        servico.registrar_dispositivo(conn, "s1", sala, AGORA)


def test_dispositivo_repetido(amb):
    with db.abrir(amb.cfg.caminho_banco) as conn, pytest.raises(servico.ErroDeDados):
        servico.registrar_dispositivo(conn, "sala-101", "De novo", AGORA)


def test_trocar_chave_invalida_a_antiga(amb):
    antiga = amb.chave
    with db.abrir(amb.cfg.caminho_banco) as conn:
        nova = servico.trocar_chave(conn, "sala-101")
        with pytest.raises(servico.NaoEncontrado):
            servico.trocar_chave(conn, "nao-existe")
    assert (
        amb.enviar([{"id_leitura": "a", "idade_s": 0, "co2_ppm": 500}], chave=antiga).status_code
        == 401
    )
    assert (
        amb.enviar([{"id_leitura": "a", "idade_s": 0, "co2_ppm": 500}], chave=nova).status_code
        == 200
    )


def test_chave_nao_fica_no_banco(amb):
    with db.abrir(amb.cfg.caminho_banco) as conn:
        conteudo = "".join(str(tuple(r)) for r in conn.execute("SELECT * FROM dispositivos"))
    assert amb.chave not in conteudo


def test_seguranca():
    chave = seguranca.gerar_chave()
    assert len(chave) >= 40
    assert seguranca.chave_confere(chave, seguranca.hash_chave(chave))
    assert not seguranca.chave_confere("outra", seguranca.hash_chave(chave))
    assert not seguranca.chave_confere(None, seguranca.hash_chave(""))
    assert not seguranca.chave_confere("", None)
    assert seguranca.token_confere("abc", "abc")
    assert not seguranca.token_confere(None, "abc")


# ------------------------------------------------------------------ referências


@pytest.mark.parametrize(
    ("metrica", "valor", "nivel"),
    [
        ("co2_ppm", 800, Nivel.OK),
        ("co2_ppm", 1120, Nivel.OK),  # 420 (ar externo) + 700: limite da NBR 17037
        ("co2_ppm", 1121, Nivel.ATENCAO),
        ("co2_ppm", 1600, Nivel.CRITICO),
        ("temperatura_c", 21, Nivel.OK),
        ("temperatura_c", 26, Nivel.OK),
        ("temperatura_c", 20.9, Nivel.ATENCAO),
        ("temperatura_c", 26.1, Nivel.ATENCAO),
        ("temperatura_c", 33, Nivel.CRITICO),
        ("umidade_pct", 35, Nivel.OK),
        ("umidade_pct", 65, Nivel.OK),
        ("umidade_pct", 34, Nivel.ATENCAO),
        ("umidade_pct", 85, Nivel.CRITICO),
    ],
)
def test_niveis(metrica, valor, nivel):
    assert REFERENCIAS[metrica].avaliar(valor) is nivel


def test_avisos_com_dica_correta():
    avisos = avaliar_leitura({"temperatura_c": 18, "umidade_pct": 50, "co2_ppm": None})
    [a] = avisos
    assert a["metrica"] == "temperatura_c" and "fria" in a["mensagem"]
    assert a["mensagem"].startswith("Temperatura em 18,0 °C.")
    [b] = avaliar_leitura({"umidade_pct": 75.4})
    assert "úmido" in b["mensagem"] and "75 %" in b["mensagem"]


# ------------------------------------------------------------------ configuração


def test_configuracao_do_ambiente(monkeypatch):
    monkeypatch.setenv("AURORA_BANCO", "/tmp/x.db")
    monkeypatch.setenv("AURORA_INTERVALO_S", "30")
    monkeypatch.setenv("AURORA_TOKEN_PAINEL", "")
    monkeypatch.setenv("AURORA_MAX_BYTES", " ")
    cfg = Configuracao.do_ambiente()
    assert cfg.caminho_banco == "/tmp/x.db" and cfg.intervalo_esperado_s == 30
    assert cfg.token_painel is None and cfg.max_bytes_requisicao == 1_000_000


@pytest.mark.parametrize(
    ("var", "valor"),
    [
        ("AURORA_INTERVALO_S", "abc"),
        ("AURORA_INTERVALO_S", "1"),
        ("AURORA_FUSO", "Marte/Olympus"),
        ("AURORA_TOKEN_PAINEL", "curto"),
    ],
)
def test_configuracao_invalida_falha_cedo(monkeypatch, var, valor):
    monkeypatch.setenv(var, valor)
    with pytest.raises(ErroDeConfiguracao):
        Configuracao.do_ambiente()


# ------------------------------------------------------------------ terminal


def test_cli_fluxo_completo(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AURORA_BANCO", str(tmp_path / "cli.db"))
    assert cli.main(["dispositivos"]) == 0
    assert "Nenhum dispositivo" in capsys.readouterr().out

    assert cli.main(["novo-dispositivo", "sala-7", "Sala 7"]) == 0
    assert "CHAVE" in capsys.readouterr().out

    assert cli.main(["dispositivos"]) == 0
    assert "sala-7" in capsys.readouterr().out

    assert cli.main(["trocar-chave", "sala-7"]) == 0
    assert "Nova CHAVE" in capsys.readouterr().out

    assert cli.main(["limpar", "--dias", "30"]) == 0
    assert "0 leitura" in capsys.readouterr().out

    assert cli.main(["remover-dispositivo", "sala-7"]) == 1  # sem --sim não apaga
    assert cli.main(["remover-dispositivo", "sala-7", "--sim"]) == 0


def test_cli_erros(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AURORA_BANCO", str(tmp_path / "cli.db"))
    assert cli.main(["novo-dispositivo", "Sala Ruim", "x"]) == 2
    assert cli.main(["trocar-chave", "nao-existe"]) == 2
    monkeypatch.setenv("AURORA_INTERVALO_S", "abc")
    assert cli.main(["dispositivos"]) == 2
    assert "Erro" in capsys.readouterr().err
