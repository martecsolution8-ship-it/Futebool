def test_liveness(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_register_login_and_me(client, db):
    registration = client.post("/api/v1/auth/register", json={
        "email": "analista@marbet.ao",
        "display_name": "Analista MarBet",
        "password": "UmaSenhaSegura123!",
    })
    assert registration.status_code == 201
    user = registration.json()
    assert user["email"] == "analista@marbet.ao"
    assert user["role"] == "USER"
    assert user["plan"] == "FREE"
    from app.models.user import User
    assert db.query(User).filter(User.email == user["email"]).one().password_hash.startswith("$argon2id$")

    login = client.post("/api/v1/auth/login", json={
        "email": "analista@marbet.ao",
        "password": "UmaSenhaSegura123!",
    })
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["id"] == user["id"]


def test_refresh_renews_access_and_rejects_access_token(client):
    registration = client.post("/api/v1/auth/register", json={
        "email": "sessao@marbet.ao",
        "display_name": "Sessão MarBet",
        "password": "UmaSenhaSegura123!",
    })
    assert registration.status_code == 201
    login = client.post("/api/v1/auth/login", json={
        "email": "sessao@marbet.ao",
        "password": "UmaSenhaSegura123!",
    })
    tokens = login.json()

    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["token_type"] == "bearer"
    assert client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
    ).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}).status_code == 401


def test_password_hash_uses_argon2id_and_invalid_hash_fails_login(client, db):
    from app.models.user import User

    client.post("/api/v1/auth/register", json={
        "email": "hash@marbet.ao",
        "display_name": "Conta segura",
        "password": "UmaSenhaSegura123!",
    })
    user = db.query(User).filter(User.email == "hash@marbet.ao").one()
    assert user.password_hash.startswith("$argon2id$")

    user.password_hash = "invalid-hash"
    db.commit()
    response = client.post("/api/v1/auth/login", json={
        "email": "hash@marbet.ao",
        "password": "UmaSenhaSegura123!",
    })
    assert response.status_code == 401


def test_duplicate_registration_is_rejected(client):
    payload = {
        "email": "duplicado@marbet.ao",
        "display_name": "Utilizador",
        "password": "UmaSenhaSegura123!",
    }
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409


def test_registration_rejects_blank_display_name(client):
    response = client.post("/api/v1/auth/register", json={
        "email": "vazio@marbet.ao",
        "display_name": "   ",
        "password": "UmaSenhaSegura123!",
    })
    assert response.status_code == 422


def test_invalid_login_is_rejected(client):
    response = client.post("/api/v1/auth/login", json={
        "email": "desconhecido@marbet.ao",
        "password": "SenhaQualquer123!",
    })
    assert response.status_code == 401


def test_admin_role_permission_is_enforced():
    import pytest
    from fastapi import HTTPException

    from app.api.dependencies import require_roles
    from app.models.user import User, UserRole

    dependency = require_roles(UserRole.ADMIN)
    user = User(role=UserRole.USER)

    with pytest.raises(HTTPException) as exc:
        dependency(current_user=user)

    assert exc.value.status_code == 403


def test_admin_role_permission_allows_admin():
    from app.api.dependencies import require_roles
    from app.models.user import User, UserRole

    dependency = require_roles(UserRole.ADMIN)
    user = User(role=UserRole.ADMIN)

    assert dependency(current_user=user) is user
