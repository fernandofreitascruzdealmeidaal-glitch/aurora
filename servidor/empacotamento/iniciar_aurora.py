"""Ponto de entrada do Aurora.exe (empacotado com PyInstaller)."""

import multiprocessing

from aurora.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
