"""Mercados suportados pelo motor MarBet: probabilidades, rótulos e liquidação.

Funções puras (sem base de dados). Tudo o que está aqui é derivado de:
  - a matriz de resultados (Dixon-Coles) e
  - as probabilidades 1X2 já combinadas com o Elo.

Nada é inventado: se uma probabilidade não puder ser calculada, o mercado não é devolvido.

Mercados (campo `market` da tabela predictions -> campo `selection`):
  1X2               HOME | DRAW | AWAY
  DOUBLE_CHANCE     1X | X2 | 12
  DRAW_NO_BET       HOME | AWAY            (empate = anulado)
  BTTS              YES | NO
  TOTAL_GOALS       OVER_1.5 | OVER_2.5 | OVER_3.5 | UNDER_2.5 | UNDER_3.5
  TEAM_GOALS_HOME   OVER_0.5 | OVER_1.5
  TEAM_GOALS_AWAY   OVER_0.5 | OVER_1.5
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

# Resultado da liquidação de uma seleção.
WIN, LOSS, PUSH = "WIN", "LOSS", "PUSH"


@dataclass(frozen=True)
class MarketEstimate:
    market: str
    selection: str
    probability: float          # probabilidade final (calibrada quando a calibração foi validada)
    raw_probability: float      # probabilidade do modelo antes da calibração
    validation_key: str         # chave do backtest que valida este mercado (ex.: "over_25", "1X2")
    label: str                  # rótulo em português


# chave de backtest -> função que decide o evento (para o backtest e para a base histórica)
BINARY_EVENTS = {
    "over_15": lambda h, a: h + a >= 2,
    "over_25": lambda h, a: h + a >= 3,
    "over_35": lambda h, a: h + a >= 4,
    "under_25": lambda h, a: h + a <= 2,
    "under_35": lambda h, a: h + a <= 3,
    "btts": lambda h, a: h >= 1 and a >= 1,
    "home_over_05": lambda h, a: h >= 1,
    "home_over_15": lambda h, a: h >= 2,
    "away_over_05": lambda h, a: a >= 1,
    "away_over_15": lambda h, a: a >= 2,
}

# chaves da base histórica (inclui o 1X2 e os mercados derivados dele)
BASE_EVENTS = {
    "home_win": lambda h, a: h > a,
    "draw": lambda h, a: h == a,
    "away_win": lambda h, a: h < a,
    **BINARY_EVENTS,
}


def _clip01(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Probabilidade inválida (não finita)")
    return min(max(value, 0.0), 1.0)


def validate_probabilities(values: dict[str, float]) -> dict[str, float]:
    """Rejeita probabilidades inválidas (NaN, infinitas, fora de [0, 1]). Nunca as 'corrige' em silêncio."""
    clean: dict[str, float] = {}
    for key, value in values.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Probabilidade inválida para {key!r}: {value!r}")
        if not math.isfinite(number) or number < -1e-9 or number > 1 + 1e-9:
            raise ValueError(f"Probabilidade fora de [0, 1] para {key!r}: {value!r}")
        clean[key] = min(max(number, 0.0), 1.0)
    return clean


def build_market_estimates(
    model_probs: dict[str, float],
    one_x_two: tuple[float, float, float],
    calibrated: dict[str, float] | None = None,
) -> list[MarketEstimate]:
    """Constrói todas as estimativas de mercado de um jogo.

    model_probs: saída de `market_probabilities` (matriz Dixon-Coles).
    one_x_two:   (casa, empate, fora) já combinado com o Elo e normalizado.
    calibrated:  probabilidades já calibradas por chave binária (over_15, btts, ...). Onde faltar,
                 usa-se a probabilidade do modelo sem alteração.
    """
    home, draw, away = (_clip01(v) for v in one_x_two)
    total = home + draw + away
    if total <= 0:
        raise ValueError("Probabilidades 1X2 inválidas (soma nula)")
    home, draw, away = home / total, draw / total, away / total
    model_probs = validate_probabilities(model_probs)
    calibrated = validate_probabilities(calibrated or {})

    def binary(key: str) -> tuple[float, float]:
        raw = model_probs[key]
        return calibrated.get(key, raw), raw

    out: list[MarketEstimate] = []

    def add(market, selection, probability, raw, validation_key, label):
        out.append(MarketEstimate(market, selection, _clip01(probability), _clip01(raw), validation_key, label))

    # 1X2 (a probabilidade bruta é a do Dixon-Coles; a final inclui o Elo)
    add("1X2", "HOME", home, model_probs["home_win"], "1X2", "Vitória da casa")
    add("1X2", "DRAW", draw, model_probs["draw"], "1X2", "Empate")
    add("1X2", "AWAY", away, model_probs["away_win"], "1X2", "Vitória do visitante")

    # Dupla possibilidade — soma de seleções do mesmo 1X2 (coerente por construção)
    add("DOUBLE_CHANCE", "1X", home + draw, model_probs["home_win"] + model_probs["draw"], "1X2", "Casa ou empate")
    add("DOUBLE_CHANCE", "X2", draw + away, model_probs["draw"] + model_probs["away_win"], "1X2", "Empate ou visitante")
    add("DOUBLE_CHANCE", "12", home + away, model_probs["home_win"] + model_probs["away_win"], "1X2", "Casa ou visitante")

    # Empate anula aposta — probabilidade condicionada a não haver empate
    non_draw = home + away
    raw_non_draw = model_probs["home_win"] + model_probs["away_win"]
    if non_draw > 1e-9 and raw_non_draw > 1e-9:
        add("DRAW_NO_BET", "HOME", home / non_draw, model_probs["home_win"] / raw_non_draw, "1X2", "Empate anula: casa")
        add("DRAW_NO_BET", "AWAY", away / non_draw, model_probs["away_win"] / raw_non_draw, "1X2", "Empate anula: visitante")

    btts, btts_raw = binary("btts")
    add("BTTS", "YES", btts, btts_raw, "btts", "Ambas marcam")
    add("BTTS", "NO", 1.0 - btts, 1.0 - btts_raw, "btts", "Ambas não marcam")

    for key, selection, label in (
        ("over_15", "OVER_1.5", "Mais de 1,5 golos"),
        ("over_25", "OVER_2.5", "Mais de 2,5 golos"),
        ("over_35", "OVER_3.5", "Mais de 3,5 golos"),
    ):
        p, raw = binary(key)
        add("TOTAL_GOALS", selection, p, raw, key, label)

    # Menos de X.5 = complemento de Mais de X.5 já calibrado (nunca há dois lados incoerentes)
    for over_key, under_key, selection, label in (
        ("over_25", "under_25", "UNDER_2.5", "Menos de 2,5 golos"),
        ("over_35", "under_35", "UNDER_3.5", "Menos de 3,5 golos"),
    ):
        p, raw = binary(over_key)
        add("TOTAL_GOALS", selection, 1.0 - p, 1.0 - raw, under_key, label)

    for side, market, name in (("home", "TEAM_GOALS_HOME", "casa"), ("away", "TEAM_GOALS_AWAY", "visitante")):
        for line, selection in (("05", "OVER_0.5"), ("15", "OVER_1.5")):
            key = f"{side}_over_{line}"
            p, raw = binary(key)
            add(market, selection, p, raw, key, f"Equipa {name}: mais de {line[0]},{line[1]} golos")
    return out


def base_rate_for(market: str, selection: str, base: dict[str, float]) -> float | None:
    """Frequência histórica do resultado correspondente (média da competição). None se não existir base."""
    if not base:
        return None
    hw, dr, aw = base.get("home_win"), base.get("draw"), base.get("away_win")
    try:
        if market == "1X2":
            return {"HOME": hw, "DRAW": dr, "AWAY": aw}.get(selection)
        if market == "DOUBLE_CHANCE":
            return {"1X": hw + dr, "X2": dr + aw, "12": hw + aw}.get(selection)
        if market == "DRAW_NO_BET":
            return {"HOME": hw / (hw + aw), "AWAY": aw / (hw + aw)}.get(selection)
        if market == "BTTS":
            return base["btts"] if selection == "YES" else 1.0 - base["btts"] if selection == "NO" else None
        if market == "TOTAL_GOALS":
            key = {"OVER_1.5": "over_15", "OVER_2.5": "over_25", "OVER_3.5": "over_35",
                   "UNDER_2.5": "under_25", "UNDER_3.5": "under_35"}.get(selection)
            return base.get(key) if key else None
        if market in ("TEAM_GOALS_HOME", "TEAM_GOALS_AWAY"):
            side = "home" if market == "TEAM_GOALS_HOME" else "away"
            line = {"OVER_0.5": "05", "OVER_1.5": "15"}.get(selection)
            return base.get(f"{side}_over_{line}") if line else None
    except (TypeError, ZeroDivisionError):
        return None
    return None


# --------------------------------------------------------------------------- rótulos

_MARKET_LABELS = {
    "1X2": "Resultado final",
    "DOUBLE_CHANCE": "Dupla possibilidade",
    "DRAW_NO_BET": "Empate anula aposta",
    "BTTS": "Ambas marcam",
    "TOTAL_GOALS": "Total de golos",
    "TEAM_GOALS_HOME": "Golos da equipa da casa",
    "TEAM_GOALS_AWAY": "Golos da equipa visitante",
}


def market_label(market: str) -> str:
    return _MARKET_LABELS.get(market, market)


def selection_label(market: str, selection: str) -> str:
    if market == "1X2" or market == "DRAW_NO_BET":
        return {"HOME": "Vitória da casa", "DRAW": "Empate", "AWAY": "Vitória do visitante"}.get(selection, selection)
    if market == "DOUBLE_CHANCE":
        return {"1X": "Casa ou empate", "X2": "Empate ou visitante", "12": "Casa ou visitante"}.get(selection, selection)
    if market == "BTTS":
        return {"YES": "Sim", "NO": "Não"}.get(selection, selection)
    side, line = _parse_total_selection(selection)
    if side:
        text = f"{'Mais' if side == 'OVER' else 'Menos'} de {str(line).replace('.', ',')} golos"
        return text
    return selection


# --------------------------------------------------------------------------- liquidação

def _parse_total_selection(selection: str) -> tuple[str | None, float | None]:
    if not isinstance(selection, str) or "_" not in selection:
        return None, None
    side, _, raw_line = selection.partition("_")
    if side not in ("OVER", "UNDER"):
        return None, None
    try:
        line = float(raw_line)
    except ValueError:
        return None, None
    return (side, line) if math.isfinite(line) else (None, None)


def _outcome_1x2(home_goals: int, away_goals: int) -> str:
    return "HOME" if home_goals > away_goals else "AWAY" if away_goals > home_goals else "DRAW"


def _compare_line(side: str, line: float, value: int) -> str:
    if value == line:
        return PUSH
    return WIN if (value > line) == (side == "OVER") else LOSS


def settle_selection(market: str, selection: str, home_goals: int, away_goals: int) -> str | None:
    """WIN / LOSS / PUSH para uma seleção, ou None se o mercado/seleção não for reconhecido.

    Os totais de golos são reconhecidos pela própria seleção (OVER_x / UNDER_x), por isso um registo
    antigo com o rótulo de mercado diferente continua a ser liquidado corretamente.
    """
    if home_goals is None or away_goals is None or home_goals < 0 or away_goals < 0:
        return None
    result = _outcome_1x2(home_goals, away_goals)
    if market == "1X2":
        return WIN if selection == result else LOSS if selection in ("HOME", "DRAW", "AWAY") else None
    if market == "DOUBLE_CHANCE":
        if selection not in ("1X", "X2", "12"):
            return None
        return WIN if ((selection == "1X" and result != "AWAY") or (selection == "X2" and result != "HOME")
                       or (selection == "12" and result != "DRAW")) else LOSS
    if market == "DRAW_NO_BET":
        if selection not in ("HOME", "AWAY"):
            return None
        return PUSH if result == "DRAW" else WIN if selection == result else LOSS
    if market == "BTTS":
        if selection not in ("YES", "NO"):
            return None
        both = home_goals > 0 and away_goals > 0
        return WIN if both == (selection == "YES") else LOSS
    side, line = _parse_total_selection(selection)
    if side is None:
        return None
    if market == "TEAM_GOALS_HOME":
        return _compare_line(side, line, home_goals)
    if market == "TEAM_GOALS_AWAY":
        return _compare_line(side, line, away_goals)
    return _compare_line(side, line, home_goals + away_goals)


def actual_outcome(market: str, selection: str, home_goals: int, away_goals: int) -> str | None:
    """Texto do resultado real, no formato já usado pela base de dados e pelo frontend."""
    verdict = settle_selection(market, selection, home_goals, away_goals)
    if verdict is None:
        return None
    if market in ("1X2", "DOUBLE_CHANCE", "DRAW_NO_BET"):
        return _outcome_1x2(home_goals, away_goals)
    if market == "BTTS":
        return "YES" if home_goals > 0 and away_goals > 0 else "NO"
    if verdict == PUSH:
        return "PUSH"
    return selection if verdict == WIN else f"NOT_{selection}"


# Estados de jogo (strings, iguais aos da enumeração FixtureStatus).
_VOID_STATES = {"CANCELLED", "SUSPENDED", "POSTPONED"}


def settlement_state(fixture_status: str, home_goals: int | None, away_goals: int | None) -> str:
    """READY (pode liquidar) | VOID (adiado/cancelado/suspenso) | PENDING (ainda sem resultado final)."""
    status = str(getattr(fixture_status, "value", fixture_status)).upper()
    if status in _VOID_STATES:
        return "VOID"
    if status == "FINISHED" and home_goals is not None and away_goals is not None:
        return "READY"
    return "PENDING"


def analysis_eligibility(fixture_status: str, kickoff_at: datetime, now: datetime | None = None) -> tuple[bool, str | None]:
    """Só se analisam jogos futuros ainda por começar. Devolve (pode_analisar, motivo_da_recusa)."""
    now = now or datetime.now(timezone.utc)
    kickoff = kickoff_at if kickoff_at.tzinfo else kickoff_at.replace(tzinfo=timezone.utc)
    status = str(getattr(fixture_status, "value", fixture_status)).upper()
    if status == "FINISHED":
        return False, "JOGO_TERMINADO"
    if status in _VOID_STATES:
        return False, {"POSTPONED": "JOGO_ADIADO", "CANCELLED": "JOGO_CANCELADO", "SUSPENDED": "JOGO_SUSPENSO"}[status]
    if status in ("LIVE", "INTERVAL"):
        return False, "JOGO_EM_CURSO"
    if status != "SCHEDULED":
        return False, "ESTADO_DESCONHECIDO"
    if kickoff <= now:
        return False, "JOGO_JA_INICIADO"
    return True, None


REFUSAL_MESSAGES = {
    "JOGO_TERMINADO": "Este jogo já terminou: não é apresentado como previsão futura.",
    "JOGO_ADIADO": "Jogo adiado: não há previsão enquanto não existir nova data.",
    "JOGO_CANCELADO": "Jogo cancelado: não há previsão.",
    "JOGO_SUSPENSO": "Jogo suspenso: não há previsão.",
    "JOGO_EM_CURSO": "Jogo em curso: o MarBet só analisa jogos antes do início.",
    "JOGO_JA_INICIADO": "O jogo já devia ter começado: o MarBet só analisa jogos antes do início.",
    "ESTADO_DESCONHECIDO": "Estado do jogo por confirmar: não há previsão.",
    "DADOS_INSUFICIENTES": "Dados insuficientes de uma das equipas para uma análise fiável.",
}

NO_PICK_MESSAGE = ("Sem palpite recomendado — os dados disponíveis não demonstram "
                   "uma vantagem estatística suficientemente clara.")
