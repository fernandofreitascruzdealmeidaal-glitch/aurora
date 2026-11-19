"""Cria os atalhos "Aurora" no menu Iniciar e na Área de Trabalho (Windows) e abre o app.

Uso (uma vez, depois de instalar com o pip):
    py -m aurora.atalho

Por que este caminho existe: o "Controle Inteligente de Aplicativos" do Windows 11 bloqueia
programas novos sem assinatura digital paga (como o instalador da Aurora). O Python oficial
é assinado, então a Aurora roda por meio dele: o atalho aponta para o `pythonw.exe` do seu
computador com `-m aurora.desktop`.

O arquivo .lnk é escrito direto no formato da Microsoft (MS-SHLLINK), sem PowerShell nem
bibliotecas extras — o PowerShell costuma ficar restrito quando essa proteção está ligada.
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
from pathlib import Path

ARGUMENTOS = "-m aurora.desktop"
DESCRICAO = "Aurora — sustentabilidade em Minas Gerais"
ICONE = Path(__file__).parent / "static" / "icones" / "aurora.ico"

# CLSID fixo dos atalhos do Windows: 00021401-0000-0000-C000-000000000046
_CLSID_ATALHO = bytes.fromhex("0114020000000000c000000000000046")
_TEM_LINK_INFO = 0x02
_TEM_NOME = 0x04
_TEM_PASTA_TRABALHO = 0x10
_TEM_ARGUMENTOS = 0x20
_TEM_ICONE = 0x40
_UNICODE = 0x80
_ARQUIVO_NORMAL = 0x80
_MOSTRAR_NORMAL = 1


def _texto_unicode(s: str) -> bytes:
    """StringData: quantidade de caracteres (2 bytes) + UTF-16LE, sem terminador."""
    codificado = s.encode("utf-16-le")
    return struct.pack("<H", len(codificado) // 2) + codificado


def montar_lnk(alvo: str, argumentos: str, pasta: str, icone: str, descricao: str) -> bytes:
    """Monta um atalho .lnk mínimo e válido (cabeçalho + LinkInfo + textos)."""
    flags = (
        _TEM_LINK_INFO | _TEM_NOME | _TEM_PASTA_TRABALHO | _TEM_ARGUMENTOS | _TEM_ICONE | _UNICODE
    )
    cabecalho = struct.pack(
        "<I16sII8s8s8sIiIHHII",
        0x4C,  # tamanho do cabeçalho
        _CLSID_ATALHO,
        flags,
        _ARQUIVO_NORMAL,
        b"\0" * 8,  # datas: zero = desconhecida
        b"\0" * 8,
        b"\0" * 8,
        0,  # tamanho do arquivo de destino (desconhecido)
        0,  # índice do ícone
        _MOSTRAR_NORMAL,
        0,  # tecla de atalho
        0,
        0,
        0,
    )

    # LinkInfo com o caminho local em ANSI e também em Unicode (cabeçalho de 0x24 bytes),
    # para funcionar mesmo com acentos no nome de usuário.
    caminho_ansi = alvo.encode("mbcs" if sys.platform == "win32" else "latin-1", "replace")
    volume = struct.pack("<IIII", 0x11, 3, 0, 0x10) + b"\0"  # unidade fixa, sem rótulo
    cab_info = 0x24
    pos_volume = cab_info
    pos_base = pos_volume + len(volume)
    pos_sufixo = pos_base + len(caminho_ansi) + 1
    pos_base_u = pos_sufixo + 1
    pos_sufixo_u = pos_base_u + len(alvo.encode("utf-16-le")) + 2
    corpo = (
        volume
        + caminho_ansi
        + b"\0"
        + b"\0"  # sufixo comum vazio (ANSI)
        + alvo.encode("utf-16-le")
        + b"\0\0"
        + b"\0\0"  # sufixo comum vazio (Unicode)
    )
    tamanho_info = cab_info + len(corpo)
    link_info = (
        struct.pack(
            "<IIIIIIIII",
            tamanho_info,
            cab_info,
            0x1,  # VolumeIDAndLocalBasePath
            pos_volume,
            pos_base,
            0,  # sem caminho de rede
            pos_sufixo,
            pos_base_u,
            pos_sufixo_u,
        )
        + corpo
    )

    textos = (
        _texto_unicode(descricao)
        + _texto_unicode(pasta)
        + _texto_unicode(argumentos)
        + _texto_unicode(icone)
    )
    return cabecalho + link_info + textos + b"\0\0\0\0"


def ler_lnk(dados: bytes) -> dict[str, str]:
    """Lê de volta os textos de um .lnk gerado por montar_lnk (usado nos testes)."""
    if dados[:4] != struct.pack("<I", 0x4C) or dados[4:20] != _CLSID_ATALHO:
        raise ValueError("não é um atalho do Windows")
    pos = 0x4C
    (tamanho_info,) = struct.unpack_from("<I", dados, pos)
    pos_base_u = struct.unpack_from("<I", dados, pos + 28)[0]
    inicio_u = pos + pos_base_u
    fim_u = inicio_u
    while dados[fim_u : fim_u + 2] != b"\0\0":
        fim_u += 2
    alvo = dados[inicio_u:fim_u].decode("utf-16-le")
    pos += tamanho_info
    campos = {}
    for nome in ("descricao", "pasta", "argumentos", "icone"):
        (n,) = struct.unpack_from("<H", dados, pos)
        campos[nome] = dados[pos + 2 : pos + 2 + 2 * n].decode("utf-16-le")
        pos += 2 + 2 * n
    return {"alvo": alvo, **campos}


def pythonw() -> Path:
    """O pythonw.exe ao lado do Python atual (abre sem janela preta de terminal)."""
    exe = Path(sys.executable)
    candidato = exe.with_name("pythonw.exe")
    return candidato if candidato.exists() else exe


def _pasta_conhecida(guid: str, reserva: Path) -> Path:
    """Pergunta ao Windows onde fica a pasta (a Área de Trabalho pode estar no OneDrive)."""
    if sys.platform != "win32":
        return reserva
    import ctypes
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


def criar_atalhos(lista: list[Path] | None = None) -> list[Path]:
    alvo = str(pythonw())
    dados = montar_lnk(
        alvo=alvo,
        argumentos=ARGUMENTOS,
        pasta=str(Path.home()),
        icone=str(ICONE),
        descricao=DESCRICAO,
    )
    criados = []
    for destino in lista or destinos():
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(dados)
            criados.append(destino)
        except OSError as exc:
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
    p = argparse.ArgumentParser(prog="aurora.atalho", description=__doc__.splitlines()[0])
    p.add_argument("--sem-abrir", action="store_true", help="só cria os atalhos")
    args = p.parse_args(argv)
    if sys.platform != "win32":
        print("Os atalhos são para Windows. Para abrir a Aurora: python -m aurora.desktop")
        return 0
    criados = criar_atalhos()
    for c in criados:
        print(f"Atalho criado: {c}")
    if not criados:
        return 1
    if not args.sem_abrir:  # pragma: no cover
        print("Abrindo a Aurora…")
        abrir_aurora()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
