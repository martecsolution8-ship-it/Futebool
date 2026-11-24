import os

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import User, UserPlan, UserRole, UserStatus


def main() -> None:
    email = os.getenv("MARBET_ADMIN_EMAIL", "").strip().lower()
    display_name = os.getenv("MARBET_ADMIN_NAME", "MarBet Superadmin").strip()
    password = os.getenv("MARBET_ADMIN_PASSWORD", "")

    if not email or not password:
        raise RuntimeError("MARBET_ADMIN_EMAIL and MARBET_ADMIN_PASSWORD are required")
    if len(password) < 12:
        raise RuntimeError("MARBET_ADMIN_PASSWORD must contain at least 12 characters")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.display_name = display_name
            user.password_hash = hash_password(password)
            user.role = UserRole.SUPERADMIN
            user.plan = UserPlan.ELITE
            user.status = UserStatus.ACTIVE
            user.email_verified = True
        else:
            user = User(
                email=email,
                display_name=display_name,
                password_hash=hash_password(password),
                role=UserRole.SUPERADMIN,
                plan=UserPlan.ELITE,
                status=UserStatus.ACTIVE,
                email_verified=True,
            )
            db.add(user)
        db.commit()
        print("Bootstrap administrator is configured.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
