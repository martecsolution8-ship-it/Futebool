import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.core.config import get_settings
from app.db.session import get_db
from app.models.sports import ApiUsage, Competition, Fixture, FixtureStatus, Player, Season, SyncRun, Team
from app.models.user import UserRole
from app.services.sync_service import SportsSyncService

router = APIRouter(prefix="/data", tags=["sports-data"])


@router.get("/competitions")
def list_competitions(db: Session = Depends(get_db)):
    return db.scalars(select(Competition).where(Competition.active == True).order_by(Competition.name)).all()


@router.get("/competitions/{competition_id}/seasons")
def list_seasons(competition_id: uuid.UUID, db: Session = Depends(get_db)):
    if db.get(Competition, competition_id) is None:
        raise HTTPException(status_code=404, detail="Competition not found")
    return db.scalars(select(Season).where(Season.competition_id == competition_id).order_by(Season.label.desc())).all()


@router.get("/providers/status")
def provider_status(db: Session = Depends(get_db)):
    settings = get_settings()
    latest = {}
    for provider in ("api-football", "football-data"):
        run = db.scalar(select(SyncRun).where(SyncRun.provider == provider).order_by(SyncRun.started_at.desc()))
        latest[provider] = None if run is None else {
            "status": run.status.value,
            "operation": run.operation,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "records_received": run.records_received,
            "records_written": run.records_written,
        }
    return {
        "api-football": {
            "configured": bool(settings.api_football_key),
            "latest_sync": latest["api-football"],
        },
        "football-data": {
            "configured": bool(settings.football_data_token or settings.football_data_key),
            "latest_sync": latest["football-data"],
        },
    }


@router.get("/teams")
def list_teams(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    return db.scalars(select(Team).order_by(Team.name).limit(limit)).all()


@router.get("/players")
def list_players(team_id: uuid.UUID | None = None, limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    query = select(Player).order_by(Player.name).limit(limit)
    if team_id:
        query = query.where(Player.team_id == team_id)
    return db.scalars(query).all()


@router.get("/fixtures")
def list_fixtures(
    competition_id: uuid.UUID | None = None,
    season_id: uuid.UUID | None = None,
    fixture_status: list[FixtureStatus] | None = Query(default=None, alias="status"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    order: str = Query(default="default", pattern="^(default|asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Lista jogos.

    Sem filtros mantém a ordem original (próximos primeiro, depois resultados recentes).
    O frontend usa `status`, `date_from`/`date_to` e `order` para pedir explicitamente
    os jogos de hoje e os resultados recentes, que de outra forma ficavam fora do
    `limit` quando há muitos jogos agendados.
    """
    live_statuses = (FixtureStatus.SCHEDULED, FixtureStatus.LIVE, FixtureStatus.INTERVAL)
    query = select(Fixture)
    if competition_id:
        query = query.where(Fixture.competition_id == competition_id)
    if season_id:
        query = query.where(Fixture.season_id == season_id)
    if fixture_status:
        query = query.where(Fixture.status.in_(fixture_status))
    if date_from:
        query = query.where(Fixture.kickoff_at >= date_from)
    if date_to:
        query = query.where(Fixture.kickoff_at < date_to)
    if order == "asc":
        query = query.order_by(Fixture.kickoff_at.asc())
    elif order == "desc":
        query = query.order_by(Fixture.kickoff_at.desc())
    else:
        query = query.order_by(
            case((Fixture.status.in_(live_statuses), 0), else_=1),
            case((Fixture.status.in_(live_statuses), Fixture.kickoff_at), else_=None).asc(),
            case((Fixture.status == FixtureStatus.FINISHED, Fixture.kickoff_at), else_=None).desc(),
        )
    return db.scalars(query.limit(limit)).all()


@router.post("/sync/competitions", status_code=status.HTTP_202_ACCEPTED)
async def sync_competitions(
    provider: str = Query(pattern="^(api-football|football-data)$"),
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.SUPERADMIN)),
):
    run = await SportsSyncService(db).sync_competitions(provider)
    if run.status.value == "FAILED":
        raise HTTPException(status_code=502, detail=run.error_message)
    return run


@router.post("/sync/players", status_code=status.HTTP_202_ACCEPTED)
async def sync_players(
    provider: str,
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.SUPERADMIN)),
):
    if provider not in {"api-football", "football-data"}:
        raise HTTPException(status_code=400, detail="Unsupported provider")
    run = await SportsSyncService(db).sync_players(provider, team_id)
    if run.status.value == "FAILED":
        raise HTTPException(status_code=502, detail=run.error_message)
    return run


@router.post("/sync/fixtures", status_code=status.HTTP_202_ACCEPTED)
async def sync_fixtures(
    provider: str,
    competition_id: uuid.UUID,
    season_label: str,
    db: Session = Depends(get_db),
    _: object = Depends(require_roles(UserRole.ADMIN, UserRole.SUPERADMIN)),
):
    if provider not in {"api-football", "football-data"}:
        raise HTTPException(status_code=400, detail="Unsupported provider")
    run = await SportsSyncService(db).sync_fixtures(provider, competition_id, season_label)
    if run.status.value == "FAILED":
        raise HTTPException(status_code=502, detail=run.error_message)
    return run


@router.get("/sync/runs")
def sync_runs(limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db),
             _: object = Depends(require_roles(UserRole.ADMIN, UserRole.SUPERADMIN, UserRole.ANALYST))):
    return db.scalars(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)).all()


@router.get("/sync/usage")
def sync_usage(limit: int = Query(default=100, ge=1, le=1000), db: Session = Depends(get_db),
              _: object = Depends(require_roles(UserRole.ADMIN, UserRole.SUPERADMIN))):
    return db.scalars(select(ApiUsage).order_by(ApiUsage.request_at.desc()).limit(limit)).all()
