"""Fundamentação em linguagem simples (português), construída só com dados realmente disponíveis.

Cada frase tem origem num número calculado pelo motor; nada é preenchido por inferência livre.
"""
from __future__ import annotations

from intelligence.markets import market_label, selection_label


def _pct(value: float) -> str:
    return f"{round(float(value) * 100)}%"


def _num(value: float, digits: int = 2) -> str:
    return f"{float(value):.{digits}f}".replace(".", ",")


def build_explanation(
    *,
    market: str,
    selection: str,
    probability: float,
    base_rate: float | None,
    expected_goals: tuple[float, float] | None,
    home_matches: int,
    away_matches: int,
    form: dict | None,
    validation: dict | None,
    calibration_state: str,
    model_agreement: float,
    odds_assessed: bool = False,
) -> dict:
    """Devolve {'summary', 'factors_for', 'risks'}."""
    factors: list[str] = []
    risks: list[str] = []
    form = form or {}

    if base_rate is not None:
        gap = (probability - base_rate) * 100
        if gap >= 1:
            factors.append(f"A probabilidade estimada ({_pct(probability)}) está {round(gap)} pontos percentuais acima da média histórica deste resultado ({_pct(base_rate)}).")
    if expected_goals is not None:
        eh, ea = expected_goals
        factors.append(f"O modelo espera cerca de {_num(eh)} golos da equipa da casa e {_num(ea)} do visitante ({_num(eh + ea)} no total).")

    for side, label, matches in (("home", "A equipa da casa", home_matches), ("away", "A equipa visitante", away_matches)):
        n = form.get(f"{side}_matches")
        if n and n >= 3:
            gf, ga = form.get(f"{side}_goals_for_avg"), form.get(f"{side}_goals_against_avg")
            if gf is not None and ga is not None:
                factors.append(f"{label} tem {_num(gf, 1)} golos marcados e {_num(ga, 1)} sofridos por jogo nos últimos {n} jogos.")

    if validation and validation.get("validated"):
        factors.append(
            f"Este mercado foi testado em {validation['samples']} jogos que o modelo não tinha visto "
            f"e ficou {_num(validation['gain'] * 100, 1)}% melhor do que a média histórica.")

    fewest = min(home_matches, away_matches)
    if fewest < 12:
        risks.append(f"Uma das equipas tem apenas {fewest} jogos no histórico, por isso a estimativa é menos estável.")
    if validation is None or not validation.get("validated"):
        risks.append("O modelo ainda não demonstrou vantagem sobre a média histórica neste mercado.")
    elif validation["samples"] < 100:
        risks.append(f"A validação deste mercado usa apenas {validation['samples']} jogos de teste.")
    if calibration_state == "NONE":
        risks.append("As probabilidades ainda não foram calibradas com resultados suficientes.")
    elif calibration_state == "FITTED":
        risks.append("A calibração das probabilidades ainda não foi confirmada em dados novos.")
    if model_agreement < 0.5:
        risks.append("Os métodos Profissionais discordam entre si, o que reduz a segurança da estimativa.")
    if base_rate is not None and probability - base_rate < 0.06:
        risks.append("A vantagem sobre a média histórica é pequena.")
    if not odds_assessed:
        risks.append("Sem odds fiáveis: não foi avaliado o valor de aposta, apenas a probabilidade.")
    risks.append("O futebol tem muita variância: mesmo uma probabilidade alta falha com frequência e nada é garantido.")

    summary = (f"{market_label(market)}: {selection_label(market, selection)} com probabilidade estimada de {_pct(probability)}."
               + (f" Média histórica: {_pct(base_rate)}." if base_rate is not None else ""))
    return {"summary": summary, "factors_for": factors, "risks": risks}
