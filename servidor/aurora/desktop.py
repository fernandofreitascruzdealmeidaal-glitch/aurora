"""Aurora para computador: dois cliques e o app abre, sem terminal.

O que faz ao abrir:
1. Guarda os dados em uma pasta do usuário (no Windows: %LOCALAPPDATA%\\Aurora).
2. Cria (uma única vez) a senha da área da equipe e guarda junto com os dados.
3. Liga o servidor da Aurora em segundo plano.
4. Abre o site numa janela própria (Edge ou Chrome em "modo app"; senão, o navegador padrão).
5. Mostra uma janelinha de controle: abrir de novo, área da equipe, endereço para o celular,
   senha da equipe, a chave do chat com o Claude e o botão Encerrar.

Se a Aurora já estiver aberta, um novo clique só abre outra janela do app (não liga um
segundo servidor em cima do mesmo banco de dados).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

NOME_TOKEN = "senha-da-equipe.txt"  # noqa: S105 — nome do arquivo, não a senha
NOME_CHAVE_CLAUDE = "chave-claude.txt"
URL_CRIAR_CHAVE = "https://console.anthropic.com/settings/keys"
PORTA_PADRAO = 8000
log = logging.getLogger("aurora.desktop")


# ---------------------------------------------------------------- utilidades


def pasta_dados() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        pasta = base / "Aurora"
    else:
        pasta = Path.home() / ".aurora"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def senha_da_equipe(pasta: Path) -> str:
    """Lê a senha guardada ou cria uma nova (forte e aleatória) na primeira vez."""
    arquivo = pasta / NOME_TOKEN
    if arquivo.exists():
        senha = arquivo.read_text(encoding="utf-8").strip()
        if len(senha) >= 16:
            return senha
    senha = secrets.token_urlsafe(18)
    arquivo.write_text(senha + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        arquivo.chmod(0o600)
    return senha


def porta_livre(preferida: int = PORTA_PADRAO, tentativas: int = 20) -> int:
    for porta in range(preferida, preferida + tentativas):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", porta))  # noqa: S104 — o celular precisa alcançar o servidor
            except OSError:
                continue
            return porta
    raise RuntimeError("Não encontrei uma porta livre para ligar a Aurora")


def ip_na_rede() -> str:
    """Descobre o endereço deste computador no Wi-Fi (para o app do celular). Não envia nada."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return str(s.getsockname()[0])
        except OSError:
            return "127.0.0.1"


def aurora_respondendo(porta: int, tempo: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/saude", timeout=tempo) as r:
            return json.loads(r.read()).get("status") == "ok"
    except (OSError, ValueError):
        return False


def esperar_ficar_pronta(porta: int, limite_s: float = 30) -> bool:
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        if aurora_respondendo(porta, 1):
            return True
        time.sleep(0.25)
    return False


def _navegador_modo_app() -> str | None:
    candidatos = ["msedge", "chrome", "google-chrome", "chromium", "chromium-browser"]
    if sys.platform == "win32":
        for base in (
            os.environ.get("PROGRAMFILES(X86)", ""),
            os.environ.get("PROGRAMFILES", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            if not base:
                continue
            for rel in (
                r"Microsoft\Edge\Application\msedge.exe",
                r"Google\Chrome\Application\chrome.exe",
            ):
                caminho = Path(base) / rel
                if caminho.exists():
                    return str(caminho)
    for nome in candidatos:
        achado = shutil.which(nome)
        if achado:
            return achado
    return None


def abrir_janela(url: str) -> None:
    """Abre a Aurora numa janela só dela (sem barra de endereço), como um app."""
    navegador = _navegador_modo_app()
    if navegador:
        try:
            subprocess.Popen(  # noqa: S603 — executável do próprio sistema, argumentos fixos
                [navegador, f"--app={url}", "--window-size=1200,860"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except OSError:
            log.warning("Não consegui abrir %s; usando o navegador padrão", navegador)
    webbrowser.open(url)


def _preparar_saida_padrao(pasta: Path) -> None:
    """No .exe sem console, stdout/stderr não existem; manda tudo para um arquivo de registro."""
    registro = pasta / "aurora.log"
    if registro.exists() and registro.stat().st_size > 2_000_000:
        registro.replace(pasta / "aurora.log.antigo")
    if sys.stdout is None or sys.stderr is None:
        arquivo = open(registro, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or arquivo
        sys.stderr = sys.stderr or arquivo
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(registro, encoding="utf-8")],
        force=True,
    )


# ---------------------------------------------------------------- servidor


class ServidorAurora:
    def __init__(self, porta: int, host: str) -> None:
        import uvicorn

        from aurora.api import criar_app
        from aurora.config import Configuracao

        self.porta = porta
        app = criar_app(Configuracao.do_ambiente())
        self._servidor = uvicorn.Server(
            uvicorn.Config(app, host=host, port=porta, log_config=None, access_log=False)
        )
        self._linha = threading.Thread(target=self._servidor.run, daemon=True, name="servidor")

    def ligar(self) -> None:
        self._linha.start()

    def desligar(self) -> None:
        self._servidor.should_exit = True
        self._linha.join(timeout=10)


# ---------------------------------------------------------------- janela de controle


def painel(servidor: ServidorAurora, senha: str, pasta: Path) -> None:  # pragma: no cover
    import tkinter as tk
    from tkinter import ttk

    local = f"http://127.0.0.1:{servidor.porta}"
    celular = f"http://{ip_na_rede()}:{servidor.porta}"

    raiz = tk.Tk()
    raiz.title("Aurora")
    raiz.resizable(False, False)
    with contextlib.suppress(Exception):
        icone = Path(__file__).parent / "static" / "icones" / "icone-192.png"
        raiz.iconphoto(True, tk.PhotoImage(file=str(icone)))

    quadro = ttk.Frame(raiz, padding=18)
    quadro.grid()
    ttk.Label(quadro, text="☀  A Aurora está ligada", font=("Segoe UI", 14, "bold")).grid(
        column=0, row=0, columnspan=2, sticky="w"
    )
    ttk.Label(
        quadro,
        text="Feche esta janela (ou clique em Encerrar) para desligar.",
        foreground="#5c6b61",
    ).grid(column=0, row=1, columnspan=2, sticky="w", pady=(0, 12))

    def copiar(texto: str, aviso: ttk.Label) -> None:
        raiz.clipboard_clear()
        raiz.clipboard_append(texto)
        aviso.configure(text="Copiado!")
        raiz.after(2000, lambda: aviso.configure(text=""))

    ttk.Button(quadro, text="Abrir a Aurora", command=lambda: abrir_janela(local + "/")).grid(
        column=0, row=2, sticky="ew", padx=(0, 6), pady=3
    )
    ttk.Button(quadro, text="Área da equipe", command=lambda: abrir_janela(local + "/equipe")).grid(
        column=1, row=2, sticky="ew", pady=3
    )

    linha = 3
    for rotulo, valor in (
        ("Endereço para o app do celular (mesmo Wi-Fi):", celular),
        ("Senha da área da equipe:", senha),
    ):
        ttk.Label(quadro, text=rotulo).grid(
            column=0, row=linha, columnspan=2, sticky="w", pady=(12, 2)
        )
        campo = ttk.Entry(quadro, width=40)
        campo.insert(0, valor)
        campo.configure(state="readonly")
        campo.grid(column=0, row=linha + 1, sticky="ew", padx=(0, 6))
        aviso = ttk.Label(quadro, text="", foreground="#1f6b45")
        ttk.Button(quadro, text="Copiar", command=lambda v=valor, a=aviso: copiar(v, a)).grid(
            column=1, row=linha + 1, sticky="w"
        )
        aviso.grid(column=1, row=linha + 2, sticky="w")
        linha += 3

    linha = _secao_chat(raiz, quadro, pasta, linha)

    ttk.Label(quadro, text=f"Dados guardados em: {pasta}", foreground="#5c6b61").grid(
        column=0, row=linha, columnspan=2, sticky="w", pady=(10, 8)
    )

    def encerrar() -> None:
        raiz.destroy()

    ttk.Button(quadro, text="Encerrar a Aurora", command=encerrar).grid(
        column=0, row=linha + 1, columnspan=2, sticky="ew"
    )
    raiz.protocol("WM_DELETE_WINDOW", encerrar)
    raiz.mainloop()


def _secao_chat(raiz: Any, quadro: Any, pasta: Path, linha: int) -> int:  # pragma: no cover
    """Campo para colar (ou apagar) a chave da API da Anthropic que liga o chat."""
    from tkinter import ttk

    from aurora import chat

    arquivo = pasta / NOME_CHAVE_CLAUDE
    ttk.Separator(quadro).grid(column=0, row=linha, columnspan=2, sticky="ew", pady=(14, 6))
    ttk.Label(quadro, text="Chat com o Claude", font=("Segoe UI", 10, "bold")).grid(
        column=0, row=linha + 1, columnspan=2, sticky="w"
    )
    situacao = ttk.Label(quadro, text="", foreground="#5c6b61", wraplength=380)
    situacao.grid(column=0, row=linha + 2, columnspan=2, sticky="w")
    campo = ttk.Entry(quadro, width=40, show="•")
    campo.grid(column=0, row=linha + 3, sticky="ew", padx=(0, 6), pady=3)

    def atualizar(texto: str | None = None) -> None:
        ligado = chat.ler_chave(None, str(arquivo)) is not None
        situacao.configure(
            text=texto
            or (
                "Ligado. A chave fica só neste computador."
                if ligado
                else "Desligado. Cole aqui a chave da API da Anthropic (começa com sk-ant-)."
            )
        )
        apagar.configure(state="normal" if ligado else "disabled")

    def salvar() -> None:
        try:
            chat.salvar_chave(arquivo, campo.get())
        except ValueError as erro:
            atualizar(str(erro))
            return
        campo.delete(0, "end")
        atualizar("Chave salva. O chat já está ligado no app.")

    def remover() -> None:
        chat.apagar_chave(arquivo)
        atualizar("Chave apagada. O chat foi desligado.")

    ttk.Button(quadro, text="Salvar chave", command=salvar).grid(
        column=1, row=linha + 3, sticky="w"
    )
    botoes = ttk.Frame(quadro)
    botoes.grid(column=0, row=linha + 4, columnspan=2, sticky="w")
    ttk.Button(
        botoes, text="Criar uma chave", command=lambda: webbrowser.open(URL_CRIAR_CHAVE)
    ).grid(column=0, row=0, padx=(0, 6))
    apagar = ttk.Button(botoes, text="Apagar chave", command=remover)
    apagar.grid(column=1, row=0)
    raiz.bind("<Return>", lambda _e: salvar() if campo.get() else None)
    atualizar()
    return linha + 5


def avisar_erro(mensagem: str) -> None:  # pragma: no cover — janela de erro
    try:
        import tkinter as tk
        from tkinter import messagebox

        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showerror("Aurora", mensagem)
        raiz.destroy()
    except Exception:
        print(mensagem, file=sys.stderr)


# ---------------------------------------------------------------- principal


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="Aurora", description="Abre a Aurora no computador.")
    p.add_argument("--porta", type=int, default=PORTA_PADRAO)
    p.add_argument(
        "--so-neste-computador",
        action="store_true",
        help="não aceita conexões do celular (só deste computador)",
    )
    p.add_argument(
        "--verificar",
        action="store_true",
        help="liga, confere se funciona e desliga (usado nos testes automáticos)",
    )
    p.add_argument("--pasta", type=Path, default=None, help="pasta dos dados (padrão: do usuário)")
    args = p.parse_args(argv)

    pasta = args.pasta or pasta_dados()
    pasta.mkdir(parents=True, exist_ok=True)
    _preparar_saida_padrao(pasta)

    try:
        # Já está aberta? Só mostra a janela de novo.
        if not args.verificar and aurora_respondendo(args.porta):
            abrir_janela(f"http://127.0.0.1:{args.porta}/")
            return 0

        senha = senha_da_equipe(pasta)
        os.environ["AURORA_BANCO"] = str(pasta / "aurora.db")
        os.environ["AURORA_TOKEN_ADMIN"] = senha
        os.environ.setdefault("AURORA_CLAUDE_ARQUIVO", str(pasta / NOME_CHAVE_CLAUDE))
        porta = porta_livre(args.porta)
        host = "127.0.0.1" if args.so_neste_computador else "0.0.0.0"  # noqa: S104

        servidor = ServidorAurora(porta, host)
        servidor.ligar()
        if not esperar_ficar_pronta(porta):
            raise RuntimeError("O servidor da Aurora não respondeu a tempo")
        log.info("Aurora ligada na porta %s (dados em %s)", porta, pasta)

        if args.verificar:
            ok = aurora_respondendo(porta)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{porta}/api/v1/conteudo/inicio", timeout=5
            ) as r:
                ok = ok and bool(json.loads(r.read())["proximas_datas"])
            with urllib.request.urlopen(
                f"http://127.0.0.1:{porta}/api/v1/chat/estado", timeout=5
            ) as r:
                ok = ok and "disponivel" in json.loads(r.read())
            servidor.desligar()
            print("Aurora OK" if ok else "Aurora com problema")
            return 0 if ok else 1

        abrir_janela(f"http://127.0.0.1:{porta}/")  # pragma: no cover
        painel(servidor, senha, pasta)  # pragma: no cover
        servidor.desligar()  # pragma: no cover
        return 0  # pragma: no cover
    except Exception as exc:  # pragma: no cover — mostra o erro numa janela
        log.exception("Falha ao abrir a Aurora")
        avisar_erro(
            f"Não foi possível abrir a Aurora.\n\n{exc}\n\nDetalhes em: {pasta / 'aurora.log'}"
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
