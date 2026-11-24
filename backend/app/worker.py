import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.sports import ApiUsage, Competition, Fixture, FixtureStatus, Season, SyncRun, SyncStatus
from app.services.sync_service import SportsSyncService
from app.intelligence.engine import IntelligenceEngine


logger = logging.getLogger(__name__)

# Competições do plano gratuito do football-data.org (códigos). Sobrepõe com FOOTBALL_DATA_COMPETITIONS.
DEFAULT_FD_CODES = "PL,ELC,BL1,SA,PD,FL1,DED,PPL,CL,BSA,EC,WC"

API_FOOTBALL = "api-football"
FOOTBALL_DATA = "football-data"


def _csv_env(name: str, default: str = "") -> set[str]:
    return {item.strip().upper() for item in os.getenv(name, default).split(",") if item.strip()}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _fd_competition(competition: Competition) -> bool:
    codes = _csv_env("FOOTBALL_DATA_COMPETITIONS", DEFAULT_FD_CODES)
    return bool(competition.code) and competition.code.upper() in codes


def _is_wanted(provider: str, competition: Competition) -> bool:
    """Evita gastar o limite diário a sincronizar centenas de ligas irrelevantes."""
    if provider == FOOTBALL_DATA:
        return _fd_competition(competition)
    if provider == API_FOOTBALL:
        ids = _csv_env("API_FOOTBALL_LEAGUE_IDS")
        if str(competition.external_ids.get(provider, "")).upper() not in ids:
            return False
        # Evita jogos duplicados: se o football-data já cobre esta liga, a API-Football não a repete.
        if os.getenv("API_FOOTBALL_SKIP_OVERLAP", "true").lower() not in ("0", "false", "no"):
            if competition.external_ids.get(FOOTBALL_DATA) and _fd_competition(competition):
                return False
        return True
    return False


# ---------------------------------------------------------------- orçamento API-Football

def _used_today(db, provider: str) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count(ApiUsage.id)).where(ApiUsage.provider == provider, ApiUsage.request_at >= start)
    ) or 0


def _budget_left(db, service: SportsSyncService) -> int:
    """Pedidos que o worker ainda pode gastar hoje, deixando uma reserva para sincronizações manuais."""
    reserve = int(_float_env("API_FOOTBALL_RESERVE", 10))
    limit = service.settings.api_football_daily_limit
    return limit - reserve - _used_today(db, API_FOOTBALL)


def _last_fixture_runs(db) -> dict[tuple[str, str], dict[str, datetime | None]]:
    """Último sucesso e última falha por (competição, época) na API-Football, nos últimos 3 dias."""
    since = datetime.now(timezone.utc) - timedelta(days=3)
    runs = db.scalars(
        select(SyncRun).where(
            SyncRun.provider == API_FOOTBALL,
            SyncRun.operation == "fixtures",
            SyncRun.started_at >= since,
        )
    ).all()
    result: dict[tuple[str, str], dict[str, datetime | None]] = {}
    for run in runs:
        meta = run.metadata_json or {}
        key = (str(meta.get("competition_id")), str(meta.get("season")))
        entry = result.setdefault(key, {"success": None, "failure": None})
        when = _aware(run.completed_at) or _aware(run.started_at)
        slot = "success" if run.status in (SyncStatus.SUCCESS, SyncStatus.PARTIAL) else "failure"
        if run.status == SyncStatus.RUNNING:
            slot = "failure"  # um ciclo interrompido conta como tentativa
        if entry[slot] is None or (when and when > entry[slot]):
            entry[slot] = when
    return result


def _competitions_need_refresh(db) -> bool:
    """A lista de ligas (1 pedido) só é pedida de novo passados N dias, ou se ainda não existem as ligas escolhidas."""
    days = _float_env("API_FOOTBALL_COMPETITIONS_REFRESH_DAYS", 7)
    last = db.scalar(
        select(func.max(SyncRun.completed_at)).where(
            SyncRun.provider == API_FOOTBALL,
            SyncRun.operation == "competitions",
            SyncRun.status == SyncStatus.SUCCESS,
        )
    )
    last = _aware(last)
    if last is None or datetime.now(timezone.utc) - last > timedelta(days=days):
        return True
    wanted = _csv_env("API_FOOTBALL_LEAGUE_IDS")
    known = {
        str(c.external_ids.get(API_FOOTBALL, "")).upper()
        for c in db.scalars(select(Competition)).all()
        if c.external_ids.get(API_FOOTBALL)
    }
    return not wanted.issubset(known)


async def _sync_api_football(db, service: SportsSyncService) -> None:
    if not service.settings.api_football_key:
        logger.warning("Skipping api-football: API_FOOTBALL_KEY não configurada")
        return
    if not _csv_env("API_FOOTBALL_LEAGUE_IDS"):
        logger.warning("Skipping api-football: defina API_FOOTBALL_LEAGUE_IDS (ex.: 39,140,135)")
        return

    if _budget_left(db, service) <= 0:
        logger.warning("api-football: orçamento diário esgotado, a aguardar")
        return

    if _competitions_need_refresh(db):
        run = await service.sync_competitions(API_FOOTBALL)
        if run.status.value != "SUCCESS":
            logger.error("api-football competition synchronization failed: %s", run.error_message)
            return

    refresh = timedelta(hours=_float_env("API_FOOTBALL_REFRESH_HOURS", 12))
    live_refresh = timedelta(minutes=_float_env("API_FOOTBALL_LIVE_REFRESH_MINUTES", 5))
    backoff = timedelta(hours=_float_env("API_FOOTBALL_FAIL_BACKOFF_HOURS", 6))
    now = datetime.now(timezone.utc)
    history = _last_fixture_runs(db)

    due: list[tuple[datetime, Competition, Season]] = []
    for competition in db.scalars(select(Competition).where(Competition.active == True)).all():
        if not competition.external_ids.get(API_FOOTBALL) or not _is_wanted(API_FOOTBALL, competition):
            continue
        for season in db.scalars(
            select(Season).where(Season.competition_id == competition.id, Season.active == True)
        ).all():
            entry = history.get((str(competition.id), season.label), {"success": None, "failure": None})
            has_live_game = db.scalar(select(func.count()).select_from(Fixture).where(
                Fixture.competition_id == competition.id,
                Fixture.season_id == season.id,
                Fixture.status.in_((FixtureStatus.LIVE, FixtureStatus.INTERVAL)),
            )) or 0
            effective_refresh = live_refresh if has_live_game else refresh
            if entry["success"] and now - entry["success"] < effective_refresh:
                continue
            if entry["failure"] and now - entry["failure"] < backoff:
                continue
            due.append((entry["success"] or datetime.min.replace(tzinfo=timezone.utc), competition, season))

    due.sort(key=lambda item: item[0])  # nunca sincronizadas / mais antigas primeiro
    for _, competition, season in due:
        if _budget_left(db, service) <= 0:
            logger.warning("api-football: orçamento diário atingido, restantes ligas ficam para o próximo ciclo")
            break
        run = await service.sync_fixtures(API_FOOTBALL, competition.id, season.label)
        if run.status.value == "FAILED":
            logger.error(
                "api-football fixture synchronization failed for %s (%s): %s",
                competition.name, season.label, run.error_message,
            )
            if run.error_message and "limit reached" in run.error_message.lower():
                break


# ---------------------------------------------------------------- football-data

async def _sync_football_data(db, service: SportsSyncService) -> None:
    if not (service.settings.football_data_token or service.settings.football_data_key):
        logger.warning("Skipping football-data synchronization: credentials are not configured")
        return

    competition_run = await service.sync_competitions(FOOTBALL_DATA)
    if competition_run.status.value != "SUCCESS":
        logger.error("football-data competition synchronization failed: %s", competition_run.error_message)
        return

    for competition in db.scalars(select(Competition).where(Competition.active == True)).all():
        if not competition.external_ids.get(FOOTBALL_DATA) or not _is_wanted(FOOTBALL_DATA, competition):
            continue
        for season in db.scalars(
            select(Season).where(Season.competition_id == competition.id, Season.active == True)
        ).all():
            await asyncio.sleep(7)  # respeita o limite de 10 pedidos/minuto
            fixture_run = await service.sync_fixtures(FOOTBALL_DATA, competition.id, season.label)
            if fixture_run.status.value == "FAILED":
                logger.error(
                    "football-data fixture synchronization failed for %s (%s): %s",
                    competition.name, season.label, fixture_run.error_message,
                )
                if fixture_run.error_message and "limit reached" in fixture_run.error_message.lower():
                    return


async def run_once() -> None:
    db = SessionLocal()
    try:
        service = SportsSyncService(db)
        await _sync_football_data(db, service)
        await _sync_api_football(db, service)
        engine = IntelligenceEngine(db)
        engine.train_and_backtest()
        engine.generate_predictions()
        engine.settle_predictions()
    finally:
        db.close()


async def loop() -> None:
    interval_seconds = int(os.getenv("WORKER_INTERVAL_SECONDS", "3600"))
    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("MarBet background synchronization cycle failed")
        await asyncio.sleep(interval_seconds)


def main() -> None:
    asyncio.run(loop())


if __name__ == "__main__":
    main()
