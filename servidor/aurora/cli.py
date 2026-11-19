"""Comandos de terminal da Aurora.

Exemplos:
  aurora novo-dispositivo sala-101 "Sala 101"
  aurora dispositivos
  aurora trocar-chave sala-101
  aurora remover-dispositivo sala-101 --sim
  aurora limpar --dias 365
  aurora importar-semente
  aurora coletar
  aurora servir --porta 8000
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from aurora import __version__, coleta, conteudo, db, servico
from aurora.config import Configuracao, ErroDeConfiguracao


def _cfg() -> Configuracao:
    return Configuracao.do_ambiente()


def cmd_novo(args: argparse.Namespace) -> int:
    cfg = _cfg()
    db.inicializar(cfg.caminho_banco)
    with db.abrir(cfg.caminho_banco) as conn:
        novo = servico.registrar_dispositivo(conn, args.id, args.sala, time.time())
    print(f"Dispositivo criado: {novo.id} ({novo.sala})")
    print(f"CHAVE (guarde agora; ela não será mostrada de novo): {novo.chave}")
    return 0


def cmd_listar(_: argparse.Namespace) -> int:
    cfg = _cfg()
    db.inicializar(cfg.caminho_banco)
    with db.abrir(cfg.caminho_banco) as conn:
        itens = servico.status_dispositivos(conn, time.time(), cfg)
    if not itens:
        print("Nenhum dispositivo cadastrado. Use: aurora novo-dispositivo <id> <sala>")
    for d in itens:
        estado = "online " if d["online"] else "offline"
        print(f"{estado}  {d['id']:<20} {d['sala']:<25} último contato: {d['ultimo_contato']}")
    return 0


def cmd_trocar(args: argparse.Namespace) -> int:
    cfg = _cfg()
    with db.abrir(cfg.caminho_banco) as conn:
        chave = servico.trocar_chave(conn, args.id)
    print(f"Nova CHAVE para {args.id} (a antiga deixou de valer): {chave}")
    return 0


def cmd_remover(args: argparse.Namespace) -> int:
    if not args.sim:
        print("Isso apaga o dispositivo E todas as suas leituras. Repita com --sim para confirmar.")
        return 1
    cfg = _cfg()
    with db.abrir(cfg.caminho_banco) as conn:
        servico.remover_dispositivo(conn, args.id)
    print(f"Dispositivo {args.id} removido.")
    return 0


def cmd_limpar(args: argparse.Namespace) -> int:
    cfg = _cfg()
    corte = int(time.time()) - args.dias * 86400
    with db.abrir(cfg.caminho_banco) as conn:
        n = servico.apagar_antigas(conn, corte)
    print(f"{n} leitura(s) com mais de {args.dias} dias apagada(s).")
    return 0


def cmd_semente(_: argparse.Namespace) -> int:
    cfg = _cfg()
    db.inicializar(cfg.caminho_banco)
    with db.abrir(cfg.caminho_banco) as conn:
        n = conteudo.importar_semente(conn, time.time())
    print(
        f"Conteúdo inicial: {n['itens']} itens, {n['datas']} datas, {n['dicas']} dicas e "
        f"{n['fontes']} fontes novas (o que já existia foi mantido)."
    )
    return 0


def cmd_coletar(_: argparse.Namespace) -> int:  # pragma: no cover — acessa a internet
    cfg = _cfg()
    db.inicializar(cfg.caminho_banco)
    with db.abrir(cfg.caminho_banco) as conn:
        resultados = coleta.coletar_todas(conn, time.time())
    if not resultados:
        print("Nenhuma fonte ativa.")
    for r in resultados:
        situacao = f"ERRO: {r['erro']}" if r["erro"] else f"{r['novos']} novas para aprovar"
        print(f"{r['fonte']}: {situacao} ({r['ignorados']} ignoradas)")
    return 0


def cmd_servir(args: argparse.Namespace) -> int:  # pragma: no cover — sobe um servidor real
    import uvicorn

    from aurora.api import criar_app

    app = criar_app(_cfg())
    uvicorn.run(app, host=args.host, port=args.porta, proxy_headers=False)
    return 0


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aurora", description="Servidor da Aurora")
    p.add_argument("--versao", action="version", version=f"aurora {__version__}")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("novo-dispositivo", help="cadastra um sensor e gera a chave dele")
    s.add_argument("id", help="identificador curto, ex.: sala-101")
    s.add_argument("sala", help='nome legível, ex.: "Sala 101 — 9º ano"')
    s.set_defaults(func=cmd_novo)

    s = sub.add_parser("dispositivos", help="lista os sensores e se estão online")
    s.set_defaults(func=cmd_listar)

    s = sub.add_parser("trocar-chave", help="gera nova chave (use se a antiga vazar)")
    s.add_argument("id")
    s.set_defaults(func=cmd_trocar)

    s = sub.add_parser("remover-dispositivo", help="apaga o sensor e todas as leituras dele")
    s.add_argument("id")
    s.add_argument("--sim", action="store_true", help="confirma a remoção")
    s.set_defaults(func=cmd_remover)

    s = sub.add_parser("limpar", help="apaga leituras antigas")
    s.add_argument("--dias", type=int, default=365)
    s.set_defaults(func=cmd_limpar)

    s = sub.add_parser("importar-semente", help="carrega o conteúdo inicial pesquisado")
    s.set_defaults(func=cmd_semente)

    s = sub.add_parser("coletar", help="busca notícias novas nas fontes RSS (ficam pendentes)")
    s.set_defaults(func=cmd_coletar)

    s = sub.add_parser("servir", help="inicia o servidor e o painel")
    s.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 para aceitar a rede local")
    s.add_argument("--porta", type=int, default=8000)
    s.set_defaults(func=cmd_servir)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = construir_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ErroDeConfiguracao, servico.ErroDeDados) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    except servico.NaoEncontrado as exc:
        print(f"Erro: dispositivo não encontrado: {exc.args[0]}", file=sys.stderr)
        return 2
