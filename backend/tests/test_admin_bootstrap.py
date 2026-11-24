from app.admin_bootstrap import bootstrap_admin
from app.db.session import SessionLocal
from app.models.user import User, UserRole


def test_bootstrap_creates_superadmin_without_exposing_password(monkeypatch):
    monkeypatch.setenv("MARBET_ADMIN_EMAIL", "bootstrap@marbet.ao")
    monkeypatch.setenv("MARBET_ADMIN_NAME", "Bootstrap Admin")
    monkeypatch.setenv("MARBET_ADMIN_PASSWORD", "SuperSecureBootstrap2026!")

    assert bootstrap_admin() is True

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "bootstrap@marbet.ao").first()
        assert user is not None
        assert user.role == UserRole.SUPERADMIN
        assert user.password_hash != "SuperSecureBootstrap2026!"
    finally:
        db.close()


def test_bootstrap_requires_email_and_password(monkeypatch):
    monkeypatch.delenv("MARBET_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("MARBET_ADMIN_PASSWORD", raising=False)

    assert bootstrap_admin() is False
