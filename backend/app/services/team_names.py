"""Normalização e correspondência de nomes de equipas entre fornecedores.

Módulo puro (sem base de dados) para poder ser testado isoladamente.
Ordem de correspondência: exacta -> subconjunto de palavras único -> semelhança alta única.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Mapping, TypeVar

T = TypeVar("T")

_TRANSLIT = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "ß": "ss", "đ": "d", "Đ": "d", "ł": "l", "Ł": "l",
})

# Palavras que variam entre fornecedores e não distinguem equipas.
_NOISE = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "us", "ud", "cd", "sd", "rcd",
    "fk", "sk", "sv", "vfl", "vfb", "tsg", "fsv", "club", "de", "the", "calcio",
}

# Nomes curtos do football-data.co.uk (já normalizados) -> nome normalizado "oficial".
# Só entram aqui os casos que a correspondência automática não resolve.
MANUAL_ALIASES: dict[str, str] = {
    "man united": "manchester united",
    "man city": "manchester city",
    "nottm forest": "nottingham forest",
    "wolves": "wolverhampton wanderers",
    "sheffield weds": "sheffield wednesday",
    "west brom": "west bromwich albion",
    "qpr": "queens park rangers",
    "spurs": "tottenham hotspur",
    "ein frankfurt": "eintracht frankfurt",
    "mgladbach": "borussia monchengladbach",
    "bayern munich": "bayern munchen",
    "paris sg": "paris saint germain",
    "st etienne": "saint etienne",
    "inter": "internazionale",
    "ath madrid": "atletico madrid",
    "ath bilbao": "athletic club",
    "espanol": "espanyol",
    "sp lisbon": "sporting cp",
    "sp braga": "braga",
}


def normalize_team_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name.translate(_TRANSLIT))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("&", " and ").replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = text.split()
    kept = [t for t in tokens if t not in _NOISE and not t.isdigit()]
    return " ".join(kept or tokens)


def canonical_key(name: str) -> str:
    normalized = normalize_team_name(name)
    return MANUAL_ALIASES.get(normalized, normalized)


def best_match(key: str, index: Mapping[str, T], fuzzy_threshold: float = 0.90) -> T | None:
    """Devolve o candidato do índice (chave normalizada -> objecto) ou None se ambíguo/inexistente."""
    if key in index:
        return index[key]

    tokens = set(key.split())
    if tokens and not (len(tokens) == 1 and len(key) < 4):
        found: dict[int, T] = {}
        for candidate_key, value in index.items():
            candidate_tokens = set(candidate_key.split())
            if candidate_tokens and (tokens <= candidate_tokens or candidate_tokens <= tokens):
                found[id(value)] = value
        if len(found) == 1:
            return next(iter(found.values()))

    scored = sorted(
        ((SequenceMatcher(None, key, candidate_key).ratio(), candidate_key) for candidate_key in index),
        reverse=True,
    )
    if scored and scored[0][0] >= fuzzy_threshold:
        if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.03:
            return index[scored[0][1]]
    return None
