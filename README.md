# MARBET

Plataforma de inteligência desportiva preparada para FastAPI, PostgreSQL e Render.

## Estado atual

A Fase 1 fornece o backend, a autenticação inicial, roles, migrações, health checks, configuração Render e testes de aceitação.

## Executar localmente

```powershell
cd C:\Marbet
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
cd backend
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API disponível em `http://127.0.0.1:8000/docs`.

## Testes

```powershell
cd C:\Marbet\backend
python -m pytest -q
```

## Variáveis principais

Consulte `.env.example`. `JWT_SECRET_KEY` deve ser gerado aleatoriamente em produção. `DATABASE_URL` deve apontar para PostgreSQL Render em produção.

## Implantação

O ficheiro `render.yaml` define o serviço de API, o PostgreSQL e a reserva do Worker. Os serviços pagos de Render devem ser confirmados antes da implantação. O Worker será ativado somente após existir uma implementação de tarefas definida; não contém uma rotina fictícia nesta fase.
