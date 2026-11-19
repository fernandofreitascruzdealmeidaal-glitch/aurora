"""Atalhos do Windows (criados pelo próprio Windows; aqui testamos a lógica em volta)."""

from __future__ import annotations

from aurora import atalho


def test_criar_atalhos_chama_o_windows_para_cada_destino(tmp_path):
    chamadas = []

    def gravador_falso(destino, alvo, argumentos, pasta, icone, descricao):
        chamadas.append((destino, argumentos, icone))
        destino.write_text("ok")

    destinos = [tmp_path / "menu" / "Aurora.lnk", tmp_path / "area" / "Aurora.lnk"]
    criados = atalho.criar_atalhos(destinos, gravador=gravador_falso)
    assert criados == destinos
    assert all(d.exists() for d in destinos)
    assert [c[1] for c in chamadas] == ["-m aurora.desktop"] * 2
    assert all(c[2].endswith("aurora.ico") for c in chamadas)


def test_falha_num_destino_nao_impede_o_outro(tmp_path, capsys):
    def gravador(destino, *resto):
        if "menu" in str(destino):
            raise OSError("sem permissão")

    destinos = [tmp_path / "menu" / "Aurora.lnk", tmp_path / "area" / "Aurora.lnk"]
    assert atalho.criar_atalhos(destinos, gravador=gravador) == [destinos[1]]
    assert "sem permissão" in capsys.readouterr().err


def test_icone_existe_e_e_ico():
    assert atalho.ICONE.read_bytes()[:4] == b"\0\0\1\0"


def test_pythonw_aponta_para_um_python():
    assert "python" in atalho.pythonw().name.lower()


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
