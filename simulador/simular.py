#!/usr/bin/env python3
"""Simulador de sensor da Aurora — testa tudo sem precisar de hardware.

Usa só a biblioteca padrão do Python. Imita o comportamento do firmware real:
guarda leituras num buffer, envia em lotes e reenvia o que falhou.

Exemplos:
  # 3 dias de histórico de uma vez (para ver gráficos e padrões)
  python simular.py --dispositivo sala-101 --chave SUA_CHAVE --historico 72

  # Envio ao vivo, uma leitura a cada 5 s
  python simular.py --dispositivo sala-101 --chave SUA_CHAVE --ao-vivo --intervalo 5

  # Testar a robustez: mistura leituras inválidas e reenvios
  python simular.py --dispositivo sala-101 --chave SUA_CHAVE --historico 6 --com-falhas
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone

BRASILIA = timezone(timedelta(hours=-3))
TAMANHO_LOTE = 100


def em_aula(momento: datetime) -> bool:
    """Aulas de segunda a sexta, 7h–12h20, com intervalo das 9h40 às 10h."""
    local = momento.astimezone(BRASILIA)
    if local.weekday() >= 5:
        return False
    minutos = local.hour * 60 + local.minute
    return 7 * 60 <= minutos < 12 * 60 + 20 and not 9 * 60 + 40 <= minutos < 10 * 60


class Sala:
    """Modelo simples de uma sala: o CO₂ sobe com gente dentro e cai com ventilação."""

    def __init__(self, semente: int) -> None:
        self.rng = random.Random(semente)
        self.co2 = 450.0
        self.temp = 22.0

    def passo(self, momento: datetime, dt_s: float) -> dict[str, float]:
        local = momento.astimezone(BRASILIA)
        ocupada = em_aula(momento)
        alvo_co2 = 1350.0 if ocupada else 430.0
        constante = 1800.0 if ocupada else 900.0
        self.co2 += (alvo_co2 - self.co2) * (1 - math.exp(-dt_s / constante))
        hora = local.hour + local.minute / 60
        externo = 21 + 6 * math.sin((hora - 9) / 24 * 2 * math.pi)
        alvo_temp = externo + (2.5 if ocupada else 0)
        self.temp += (alvo_temp - self.temp) * (1 - math.exp(-dt_s / 3600))
        umidade = max(25.0, min(90.0, 85 - 1.6 * self.temp + self.rng.gauss(0, 1.2)))
        return {
            "temperatura_c": round(self.temp + self.rng.gauss(0, 0.1), 2),
            "umidade_pct": round(umidade, 1),
            "co2_ppm": round(max(400.0, self.co2 + self.rng.gauss(0, 15))),
        }


def enviar(url: str, dispositivo: str, chave: str, leituras: list[dict]) -> dict:
    corpo = json.dumps({"leituras": leituras}).encode()
    pedido = urllib.request.Request(  # noqa: S310 — URL vem do próprio usuário
        f"{url.rstrip('/')}/api/v1/leituras",
        data=corpo,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Dispositivo": dispositivo,
            "X-Chave": chave,
        },
    )
    with urllib.request.urlopen(pedido, timeout=15) as resposta:  # noqa: S310
        return json.loads(resposta.read())


def enviar_com_retentativa(args: argparse.Namespace, lote: list[dict]) -> dict | None:
    espera = 1.0
    for tentativa in range(1, 6):
        try:
            return enviar(args.url, args.dispositivo, args.chave, lote)
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode(errors="replace")[:200]
            if erro.code in (401, 403):
                sys.exit(f"Recusado ({erro.code}): confira --dispositivo e --chave. {detalhe}")
            if 400 <= erro.code < 500:
                print(f"Lote descartado ({erro.code}): {detalhe}")
                return None
            print(f"Servidor com erro {erro.code} (tentativa {tentativa}); tentando de novo…")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as erro:
            print(
                f"Sem conexão (tentativa {tentativa}): {erro}; tentando de novo em {espera:.0f} s"
            )
        time.sleep(espera)
        espera = min(espera * 2, 30)
    print("Desisti deste lote depois de 5 tentativas (o firmware real guardaria para depois).")
    return None


def estragar(leitura: dict, rng: random.Random) -> dict:
    ruim = dict(leitura)
    escolha = rng.choice(["co2", "umidade", "vazia", "futuro"])
    if escolha == "co2":
        ruim["co2_ppm"] = 65535  # valor típico de sensor com defeito
    elif escolha == "umidade":
        ruim["umidade_pct"] = 140
    elif escolha == "vazia":
        for k in ("temperatura_c", "umidade_pct", "co2_ppm"):
            ruim.pop(k, None)
    else:
        ruim.pop("medido_em", None)
        ruim.pop("idade_s", None)
        ruim["medido_em"] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    return ruim


def historico(args: argparse.Namespace) -> None:
    sala = Sala(semente=zlib.crc32(args.dispositivo.encode()))
    rng = random.Random(42)
    agora = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    inicio = agora - timedelta(hours=args.historico)
    passo = timedelta(seconds=args.intervalo)
    leituras = []
    momento = inicio
    while momento <= agora:
        valores = sala.passo(momento, passo.total_seconds())
        leitura = {
            "id_leitura": f"sim-{int(momento.timestamp())}",
            "medido_em": momento.isoformat(),
            **valores,
        }
        if args.com_falhas and rng.random() < 0.03:
            leitura = estragar(leitura, rng)
        leituras.append(leitura)
        momento += passo

    total = {"aceitas": 0, "duplicadas": 0, "rejeitadas": 0}
    for i in range(0, len(leituras), TAMANHO_LOTE):
        lote = leituras[i : i + TAMANHO_LOTE]
        if args.com_falhas and rng.random() < 0.2:
            lote = lote + lote[:10]  # reenvio parcial, como após uma queda de rede
        r = enviar_com_retentativa(args, lote)
        if r:
            total["aceitas"] += r["aceitas"]
            total["duplicadas"] += r["duplicadas"]
            total["rejeitadas"] += len(r["rejeitadas"])
    print(
        f"Histórico de {args.historico} h enviado: {total['aceitas']} aceitas, "
        f"{total['duplicadas']} duplicadas ignoradas, {total['rejeitadas']} recusadas."
    )


def ao_vivo(args: argparse.Namespace) -> None:
    sala = Sala(semente=zlib.crc32(args.dispositivo.encode()))
    boot = f"{random.getrandbits(32):08x}"
    contador = 0
    print(f"Enviando a cada {args.intervalo} s. Ctrl+C para parar.")
    try:
        while True:
            contador += 1
            valores = sala.passo(datetime.now(timezone.utc), args.intervalo)
            leitura = {"id_leitura": f"{boot}-{contador}", "idade_s": 0, **valores}
            r = enviar_com_retentativa(args, [leitura])
            if r:
                print(
                    f"#{contador}: CO₂ {valores['co2_ppm']:.0f} ppm, "
                    f"{valores['temperatura_c']:.1f} °C, {valores['umidade_pct']:.0f}% → {r}"
                )
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        print("\nParado.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--dispositivo", required=True)
    p.add_argument("--chave", required=True)
    p.add_argument("--intervalo", type=int, default=60, help="segundos entre leituras")
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument("--historico", type=int, metavar="HORAS", help="gera e envia HORAS de dados")
    modo.add_argument("--ao-vivo", action="store_true", help="envia continuamente")
    p.add_argument("--com-falhas", action="store_true", help="mistura leituras ruins e reenvios")
    args = p.parse_args()
    if args.historico is not None and not 1 <= args.historico <= 160:
        p.error(
            "--historico deve ficar entre 1 e 160 h (o servidor recusa dados com mais de 7 dias)"
        )
    historico(args) if args.historico else ao_vivo(args)


if __name__ == "__main__":
    main()
