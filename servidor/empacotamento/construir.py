"""Empacota a Aurora como programa de computador (pasta dist/Aurora).

Uso:  python empacotamento/construir.py
Precisa: pip install ./servidor pyinstaller
"""

from __future__ import annotations

import sys
from pathlib import Path

import PyInstaller.__main__

AQUI = Path(__file__).parent


def main() -> None:
    argumentos = [
        str(AQUI / "iniciar_aurora.py"),
        "--name=Aurora",
        "--noconfirm",
        "--clean",
        f"--distpath={AQUI.parent / 'dist'}",
        f"--workpath={AQUI.parent / 'build' / 'pyinstaller'}",
        f"--specpath={AQUI.parent / 'build'}",
        f"--paths={AQUI.parent}",  # usa o código desta pasta, não uma instalação antiga
        "--collect-submodules=aurora",
        "--collect-data=aurora",  # site, ícones e conteúdo inicial
        "--collect-submodules=uvicorn",
        "--hidden-import=aurora.api",
        "--hidden-import=tkinter",
        "--exclude-module=pytest",
        "--exclude-module=numpy",
        "--exclude-module=pandas",
        "--exclude-module=scipy",
        "--exclude-module=PIL",
        "--exclude-module=IPython",
        "--exclude-module=jedi",
    ]
    if sys.platform == "win32":
        argumentos += ["--windowed", f"--icon={AQUI / 'aurora.ico'}"]
    PyInstaller.__main__.run(argumentos)


if __name__ == "__main__":
    main()
