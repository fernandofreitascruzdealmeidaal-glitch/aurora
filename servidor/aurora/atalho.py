"""Cria os atalhos "Aurora" no menu Iniciar e na Área de Trabalho (Windows) e abre o app.

Uso (uma vez, depois de instalar com o pip):
    py -m aurora.atalho

Por que este caminho existe: o "Controle Inteligente de Aplicativos" do Windows 11 bloqueia
programas novos sem assinatura digital paga (como o instalador da Aurora). O Python oficial
é assinado, então a Aurora roda por meio dele: o atalho aponta para o `pythonw.exe` do seu
computador com `-m aurora.desktop`.

Quem cria o atalho é o próprio Windows (componente WScript.Shell), chamado direto do Python
pela biblioteca `comtypes` — sem PowerShell, que costuma ficar restrito com essa proteção.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ARGUMENTOS = "-m aurora.desktop"
DESCRICAO = "Aurora — sustentabilidade em Minas Gerais"
ICONE = Path(__file__).parent / "static" / "icones" / "aurora.ico"

# Função que grava um atalho: (destino, programa, argumentos, pasta, ícone, descrição) -> None
Gravador = Callable[[Path, str, str, str, str, str], None]


def gravar_com_windows(
    destino: Path, alvo: str, argumentos: str, pasta: str, icone: str, descricao: str
) -> None:  # pragma: no cover — só existe no Windows (testado no GitHub Actions)
    import comtypes.client

    shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    atalho = shell.CreateShortcut(str(destino))
    atalho.TargetPath = alvo
    atalho.Arguments = argumentos
    atalho.WorkingDirectory = pasta
    atalho.IconLocation = f"{icone},0"
    atalho.Description = descricao
    atalho.Save()


def pythonw() -> Path:
    """O pythonw.exe ao lado do Python atual (abre sem janela preta de terminal)."""
    exe = Path(sys.executable)
    candidato = exe.with_name("pythonw.exe")
    return candidato if candidato.exists() else exe


def _pasta_conhecida(guid: str, reserva: Path) -> Path:
    """Pergunta ao Windows onde fica a pasta (a Área de Trabalho pode estar no OneDrive)."""
    if sys.platform != "win32":
        return reserva
    import ctypes  # pragma: no cover — só no Windows
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    u = uuid.UUID(guid)
    g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    caminho = ctypes.c_wchar_p()
    try:
        if (
            ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
                ctypes.byref(g), 0, None, ctypes.byref(caminho)
            )
            == 0
        ):
            resultado = Path(caminho.value or "")
            ctypes.windll.ole32.CoTaskMemFree(caminho)  # type: ignore[attr-defined]
            return resultado
    except OSError:
        pass
    return reserva


def destinos() -> list[Path]:
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    menu = _pasta_conhecida(
        "A77F5D77-2E2B-44C3-A6A2-ABA601054A51",  # Programas do menu Iniciar
        appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    )
    area = _pasta_conhecida("B4BFCC3A-DB2C-424C-B029-7FE99A87C641", Path.home() / "Desktop")
    return [menu / "Aurora.lnk", area / "Aurora.lnk"]


def criar_atalhos(
    lista: list[Path] | None = None,
    argumentos: str = ARGUMENTOS,
    gravador: Gravador | None = None,
) -> list[Path]:
    gravar = gravador or gravar_com_windows
    criados = []
    for destino in lista or destinos():
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            gravar(destino, str(pythonw()), argumentos, str(Path.home()), str(ICONE), DESCRICAO)
            criados.append(destino)
        except Exception as exc:
            print(f"Não consegui criar {destino}: {exc}", file=sys.stderr)
    return criados


def abrir_aurora() -> None:  # pragma: no cover — abre a janela de verdade
    opcoes: dict[str, object] = {}
    if sys.platform == "win32":
        opcoes["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    subprocess.Popen(  # noqa: S603 — o próprio Python, argumentos fixos
        [str(pythonw()), "-m", "aurora.desktop"], close_fds=True, **opcoes
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aurora.atalho", description="Cria os atalhos da Aurora.")
    p.add_argument("--sem-abrir", action="store_true", help="só cria os atalhos")
    args = p.parse_args(argv)
    if sys.platform != "win32":
        print("Os atalhos são para Windows. Para abrir a Aurora: python -m aurora.desktop")
        return 0
    criados = criar_atalhos()  # pragma: no cover — Windows
    for c in criados:  # pragma: no cover
        print(f"Atalho criado: {c}")
    if not criados:  # pragma: no cover
        print("Não deu para criar os atalhos. Abra a Aurora com: py -m aurora.desktop")
        return 1
    if not args.sem_abrir:  # pragma: no cover
        print("Abrindo a Aurora…")
        abrir_aurora()
    return 0  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
