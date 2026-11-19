"""Vocabulário fixo da base de sustentabilidade: tipos de conteúdo e regiões de Minas."""

from __future__ import annotations

import re
import unicodedata

TIPOS: dict[str, str] = {
    "noticia": "Notícia",
    "evento": "Evento ambiental",
    "feira_ciencias": "Feira de ciências e mostra",
    "feira_sustentavel": "Feira sustentável",
    "parque": "Atividade em parque",
    "voluntariado": "Voluntariado",
    "vaga": "Vaga e estágio",
    "curso": "Curso e oficina",
}

# Tipos que costumam ter data (aparecem na Agenda). Os demais são oportunidades.
TIPOS_AGENDA = ("evento", "feira_ciencias", "feira_sustentavel", "parque")
TIPOS_OPORTUNIDADE = ("voluntariado", "vaga", "curso", "feira_ciencias")

# Regiões de planejamento de Minas Gerais + opções para conteúdo sem lugar fixo.
REGIOES: tuple[str, ...] = (
    "Metropolitana de BH",
    "Central",
    "Centro-Oeste",
    "Zona da Mata",
    "Sul de Minas",
    "Campo das Vertentes",
    "Triângulo e Alto Paranaíba",
    "Noroeste",
    "Norte de Minas",
    "Jequitinhonha e Mucuri",
    "Vale do Rio Doce",
    "Vale do Aço",
    "Todo o estado",
    "On-line",
    "Nacional",
)

STATUS = ("pendente", "aprovado", "rejeitado")

# Palavras que fazem uma notícia coletada ser considerada sobre sustentabilidade.
# Comparadas sem acento e em minúsculas, como início de palavra.
PALAVRAS_CHAVE: tuple[str, ...] = (
    "sustentab",
    "meio ambiente",
    "ambiental",
    "ambientais",
    "reciclag",
    "reciclav",
    "residuo",
    "compostag",
    "clima",
    "climatic",
    "aquecimento global",
    "desmat",
    "reflorest",
    "plantio",
    "mudas",
    "arboriz",
    "biodivers",
    "cerrado",
    "mata atlantica",
    "nascente",
    "bacia hidrografica",
    "rio das velhas",
    "rio doce",
    "sao francisco",
    "agua",
    "saneamento",
    "energia solar",
    "energia renovavel",
    "agroecolog",
    "organico",
    "unidade de conservacao",
    "parque estadual",
    "parque nacional",
    "parque municipal",
    "incendio florestal",
    "queimada",
    "fauna",
    "flora",
    "poluic",
    "qualidade do ar",
    "feira de ciencia",
    "iniciacao cientifica",
    "educacao ambiental",
    "voluntari",
)


def normalizar(texto: str | None) -> str:
    """'Feira de Ciências' → 'feira de ciencias' (sem acento, minúsculo, espaços simples)."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento.casefold()).strip()


_PADROES_CHAVE = [re.compile(r"\b" + re.escape(p)) for p in PALAVRAS_CHAVE]


def sobre_sustentabilidade(*textos: str | None) -> bool:
    alvo = " ".join(normalizar(t) for t in textos)
    return any(p.search(alvo) for p in _PADROES_CHAVE)
