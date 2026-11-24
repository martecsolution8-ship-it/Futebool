# Fase 1 - Arquitetura técnica

## Estrutura de runtime

- API: FastAPI
- Base de dados: PostgreSQL em produção; SQLite apenas para testes locais
- ORM: SQLAlchemy 2
- Migrações: Alembic
- Segurança: Argon2id e JWT HS256
- Alojamento: Render Web Service para API, Render PostgreSQL e configuração reservada para Worker

## Esquema inicial

### users

`id`, `email`, `display_name`, `password_hash`, `role`, `plan`, `status`, `email_verified`, `created_at`, `updated_at`.

Roles: `USER`, `ANALYST`, `ADMIN`, `SUPERADMIN`. Planos: `FREE`, `VIP`, `ELITE`. Estados: `ACTIVE`, `SUSPENDED`.

### audit_logs

`id`, `actor_id`, `action`, `resource_type`, `resource_id`, `metadata_json`, `ip_address`, `created_at`.

## Endpoints iniciais

- `GET /health/live`
- `GET /health/ready`
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`

## Testes de aceitação

- Liveness e readiness
- Registo de utilizador
- Login e emissão de access token
- Utilização do token em `/me`
- Rejeição de email duplicado
- Rejeição de credenciais inválidas

## Limitações conhecidas da Fase 1

- Refresh token ainda não possui endpoint de renovação.
- Auditoria ainda não está ligada automaticamente às ações.
- Rate limiting ainda não está aplicado.
- Subscrições e painel administrativo pertencem às fases seguintes.
- O Worker do Render está reservado na arquitetura, mas não possui tarefas até à Fase 3.
