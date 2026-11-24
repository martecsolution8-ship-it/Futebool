"""Aplicação segura do primeiro administrador durante o arranque.

Executa apenas quando MARBET_ADMIN_EMAIL e MARBET_ADMIN_PASSWORD estão definidos.
Depois de criar ou atualizar o administrador, remova MARBET_ADMIN_PASSWORD do Render.
"""

import os

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import User, UserPlan, UserRole, UserStatus


def bootstrap_admin() -> bool:
    email = os.getenv("MARBET_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("MARBET_ADMIN_PASSWORD", "")

    if not email or not password:
        return False
    if len(password) < 12:
        raise RuntimeError("MARBET_ADMIN_PASSWORD must contain at least 12 characters")

    display_name = os.getenv("MARBET_ADMIN_NAME", "MarBet Superadmin").strip()
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
            db.add(User(
                email=email,
                display_name=display_name,
                password_hash=hash_password(password),
                role=UserRole.SUPERADMIN,
                plan=UserPlan.ELITE,
                status=UserStatus.ACTIVE,
                email_verified=True,
            ))
        db.commit()
        return True
    finally:
        db.close()
