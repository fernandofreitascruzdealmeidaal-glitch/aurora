"""Faixas de valores usadas pela Aurora.

Há dois tipos de faixa, e é importante não confundir:

1. FAIXAS FÍSICAS — o que o sensor consegue medir. Um valor fora delas é erro de
   leitura (fio solto, sensor com defeito) e é RECUSADO, nunca guardado.

2. REFERÊNCIAS DE CONFORTO — valores a partir dos quais vale a pena prestar atenção
   (abrir a janela, ligar o ventilador, avisar a manutenção). São orientações gerais
   de conforto e ventilação, NÃO diagnóstico de saúde nem garantia de segurança.

   Os limites de ATENÇÃO seguem a ABNT NBR 17037 (qualidade do ar interior em ambientes
   não residenciais climatizados), que desde 2024 substitui a Resolução ANVISA RE nº 9/2003:
     - CO₂: até 700 ppm acima do ar externo (a norma pede medir o ar de fora; aqui usamos
       um valor típico, CO2_EXTERNO_PPM — se tiverem um sensor do lado de fora, ajustem);
     - temperatura: 21 a 26 °C;
     - umidade relativa: 35 % a 65 %.
   A norma vale para ambientes com ar-condicionado; em salas sem climatização ela serve
   como referência, não como obrigação. Os limites CRÍTICOS são escolha do projeto.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

METRICAS = ("temperatura_c", "umidade_pct", "co2_ppm")

NOMES = {
    "temperatura_c": "Temperatura",
    "umidade_pct": "Umidade",
    "co2_ppm": "CO₂",
}

UNIDADES = {
    "temperatura_c": "°C",
    "umidade_pct": "%",
    "co2_ppm": "ppm",
}

# Faixa de medição do sensor SCD40/SCD41 (com folga). Fora disso = leitura impossível.
FAIXAS_FISICAS: dict[str, tuple[float, float]] = {
    "temperatura_c": (-10.0, 60.0),
    "umidade_pct": (0.0, 100.0),
    "co2_ppm": (250.0, 10_000.0),
}


# CO₂ típico do ar ao ar livre. A NBR 17037 compara o ar interno com o externo medido.
CO2_EXTERNO_PPM = 420


class Nivel(str, Enum):
    OK = "ok"
    ATENCAO = "atencao"
    CRITICO = "critico"


@dataclass(frozen=True)
class Referencia:
    metrica: str
    atencao_abaixo: float | None
    atencao_acima: float | None
    critico_abaixo: float | None
    critico_acima: float | None
    dica_alto: str
    dica_baixo: str

    def avaliar(self, valor: float) -> Nivel:
        if (self.critico_acima is not None and valor > self.critico_acima) or (
            self.critico_abaixo is not None and valor < self.critico_abaixo
        ):
            return Nivel.CRITICO
        if (self.atencao_acima is not None and valor > self.atencao_acima) or (
            self.atencao_abaixo is not None and valor < self.atencao_abaixo
        ):
            return Nivel.ATENCAO
        return Nivel.OK

    def dica(self, valor: float) -> str:
        acima = self.atencao_acima is not None and valor > self.atencao_acima
        return self.dica_alto if acima else self.dica_baixo


REFERENCIAS: dict[str, Referencia] = {
    "co2_ppm": Referencia(
        metrica="co2_ppm",
        atencao_abaixo=None,
        atencao_acima=CO2_EXTERNO_PPM + 700,
        critico_abaixo=None,
        critico_acima=1500,
        dica_alto="CO₂ alto indica pouca renovação de ar: abrir janelas e portas ajuda.",
        dica_baixo="",
    ),
    "temperatura_c": Referencia(
        metrica="temperatura_c",
        atencao_abaixo=21,
        atencao_acima=26,
        critico_abaixo=16,
        critico_acima=31,
        dica_alto="Sala quente: ventilar, fechar cortinas do lado do sol ou ligar o ventilador.",
        dica_baixo="Sala fria: fechar janelas que não sejam necessárias para ventilação.",
    ),
    "umidade_pct": Referencia(
        metrica="umidade_pct",
        atencao_abaixo=35,
        atencao_acima=65,
        critico_abaixo=20,
        critico_acima=80,
        dica_alto="Ar muito úmido: melhorar a ventilação e verificar infiltrações.",
        dica_baixo="Ar muito seco: beber água e, se possível, aumentar a umidade do ambiente.",
    ),
}

AVISO_GERAL = (
    "Os níveis são referências de conforto e ventilação. Não são diagnóstico médico "
    "nem garantia de segurança ambiental."
)


CASAS_DECIMAIS = {"temperatura_c": 1, "umidade_pct": 0, "co2_ppm": 0}


def formatar_br(valor: float, metrica: str) -> str:
    """26.72 → "26,7" (vírgula decimal, como no Brasil)."""
    return f"{valor:.{CASAS_DECIMAIS[metrica]}f}".replace(".", ",")


def avaliar_leitura(valores: dict[str, float | None]) -> list[dict[str, object]]:
    """Devolve os avisos (atenção/crítico) para os valores de uma leitura."""
    avisos: list[dict[str, object]] = []
    for metrica in METRICAS:
        valor = valores.get(metrica)
        if valor is None:
            continue
        ref = REFERENCIAS[metrica]
        nivel = ref.avaliar(valor)
        if nivel is not Nivel.OK:
            avisos.append(
                {
                    "metrica": metrica,
                    "nivel": nivel.value,
                    "valor": valor,
                    "mensagem": f"{NOMES[metrica]} em {formatar_br(valor, metrica)} "
                    f"{UNIDADES[metrica]}. " + ref.dica(valor),
                }
            )
    return avisos


def referencias_publicas() -> dict[str, object]:
    """Formato enviado ao painel para desenhar as linhas de referência."""
    return {
        "aviso": AVISO_GERAL,
        "metricas": {
            m: {
                "nome": NOMES[m],
                "unidade": UNIDADES[m],
                "atencao_abaixo": r.atencao_abaixo,
                "atencao_acima": r.atencao_acima,
                "critico_abaixo": r.critico_abaixo,
                "critico_acima": r.critico_acima,
            }
            for m, r in REFERENCIAS.items()
        },
    }
