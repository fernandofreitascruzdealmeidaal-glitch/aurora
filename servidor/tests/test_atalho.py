"""Atalhos do Windows (.lnk) criados sem PowerShell."""

from __future__ import annotations

import struct

import pytest

from aurora import atalho


def test_lnk_ida_e_volta():
    dados = atalho.montar_lnk(
        alvo=r"C:\Users\Fulano\AppData\Local\Programs\Python\Python312\pythonw.exe",
        argumentos="-m aurora.desktop",
        pasta=r"C:\Users\Fulano",
        icone=r"C:\x\aurora.ico",
        descricao="Aurora — sustentabilidade",
    )
    assert struct.unpack_from("<I", dados)[0] == 0x4C
    lido = atalho.ler_lnk(dados)
    assert lido == {
        "alvo": r"C:\Users\Fulano\AppData\Local\Programs\Python\Python312\pythonw.exe",
        "descricao": "Aurora — sustentabilidade",
        "pasta": r"C:\Users\Fulano",
        "argumentos": "-m aurora.desktop",
        "icone": r"C:\x\aurora.ico",
    }
    assert dados.endswith(b"\0\0\0\0")


def test_lnk_com_acentos_no_caminho():
    alvo = r"C:\Users\João Ção\python\pythonw.exe"
    lido = atalho.ler_lnk(atalho.montar_lnk(alvo, "-m x", "C:\\", "i.ico", "d"))
    assert lido["alvo"] == alvo


def test_ler_lnk_recusa_lixo():
    with pytest.raises(ValueError):
        atalho.ler_lnk(b"nada disso")


def test_criar_atalhos_em_pastas_dadas(tmp_path):
    destinos = [tmp_path / "menu" / "Aurora.lnk", tmp_path / "area" / "Aurora.lnk"]
    criados = atalho.criar_atalhos(destinos)
    assert criados == destinos
    lido = atalho.ler_lnk(destinos[0].read_bytes())
    assert lido["argumentos"] == "-m aurora.desktop"
    assert lido["icone"].endswith("aurora.ico")


def test_icone_existe_e_e_ico():
    assert atalho.ICONE.read_bytes()[:4] == b"\0\0\1\0"


def test_fora_do_windows_so_explica(capsys, monkeypatch):
    monkeypatch.setattr(atalho.sys, "platform", "linux")
    assert atalho.main([]) == 0
    assert "python -m aurora.desktop" in capsys.readouterr().out


def test_destinos_padrao(monkeypatch, tmp_path):
    monkeypatch.setattr(atalho.sys, "platform", "linux")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    menu, area = atalho.destinos()
    assert menu == tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Aurora.lnk"
    assert area.name == "Aurora.lnk"
