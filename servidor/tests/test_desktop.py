"""Lançador de computador (Aurora.exe)."""

from __future__ import annotations

import logging
import socket

import pytest

from aurora import desktop


@pytest.fixture(autouse=True)
def _restaurar_log():
    raiz = logging.getLogger()
    antes = list(raiz.handlers)
    yield
    for h in raiz.handlers:
        if h not in antes:
            h.close()
    raiz.handlers = antes


def test_senha_da_equipe_e_criada_uma_vez(tmp_path):
    primeira = desktop.senha_da_equipe(tmp_path)
    assert len(primeira) >= 16
    assert desktop.senha_da_equipe(tmp_path) == primeira  # não muda ao abrir de novo


def test_senha_curta_ou_vazia_e_trocada(tmp_path):
    (tmp_path / desktop.NOME_TOKEN).write_text("curta")
    assert len(desktop.senha_da_equipe(tmp_path)) >= 16


def test_porta_livre_pula_porta_ocupada():
    with socket.socket() as ocupada:
        ocupada.bind(("0.0.0.0", 0))
        ocupada.listen()
        numero = ocupada.getsockname()[1]
        assert desktop.porta_livre(numero) != numero


def test_ip_na_rede():
    ip = desktop.ip_na_rede()
    socket.inet_aton(ip)  # é um IPv4 válido


def test_pasta_dados_no_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    assert desktop.pasta_dados() == tmp_path / ".aurora"


def test_pasta_dados_no_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert desktop.pasta_dados() == tmp_path / "Aurora"


def test_nada_respondendo():
    assert desktop.aurora_respondendo(1, tempo=0.2) is False


def test_liga_confere_e_desliga(tmp_path, capsys, monkeypatch):
    """O mesmo teste que roda no .exe do Windows: liga de verdade, consulta e desliga."""
    monkeypatch.delenv("AURORA_TOKEN_PAINEL", raising=False)
    porta = desktop.porta_livre(18765)
    codigo = desktop.main(
        ["--verificar", "--porta", str(porta), "--pasta", str(tmp_path), "--so-neste-computador"]
    )
    assert codigo == 0
    assert "Aurora OK" in capsys.readouterr().out
    assert (tmp_path / "aurora.db").exists()
    assert (tmp_path / desktop.NOME_TOKEN).exists()
    assert (tmp_path / "aurora.log").exists()
    assert not desktop.aurora_respondendo(porta, 0.3)  # desligou mesmo


def test_abrir_janela_usa_navegador_padrao_sem_edge_ou_chrome(monkeypatch):
    abertos = []
    monkeypatch.setattr(desktop, "_navegador_modo_app", lambda: None)
    monkeypatch.setattr(desktop.webbrowser, "open", abertos.append)
    desktop.abrir_janela("http://127.0.0.1:8000/")
    assert abertos == ["http://127.0.0.1:8000/"]


def test_abrir_janela_em_modo_app(monkeypatch):
    chamadas = []
    monkeypatch.setattr(desktop, "_navegador_modo_app", lambda: "/usr/bin/navegador")
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, **k: chamadas.append(args))
    desktop.abrir_janela("http://127.0.0.1:8000/")
    assert chamadas == [
        ["/usr/bin/navegador", "--app=http://127.0.0.1:8000/", "--window-size=1200,860"]
    ]
