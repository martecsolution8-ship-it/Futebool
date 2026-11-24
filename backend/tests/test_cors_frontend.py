from fastapi.testclient import TestClient


def test_render_frontend_origin_is_allowed(client):
    response = client.options(
        "/api/v1/data/competitions",
        headers={
            "Origin": "https://marbet-web.onrender.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://marbet-web.onrender.com"


def test_local_frontend_origin_is_allowed(client):
    response = client.options(
        "/api/v1/data/competitions",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200


def test_loopback_frontend_origin_is_allowed(client):
    response = client.options(
        "/api/v1/data/competitions",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
