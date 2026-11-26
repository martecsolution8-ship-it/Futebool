# Fase 2 - Arquitectura de dados desportivos

## Fornecedores

### API-Football

- Base: API-Sports v3, `https://v3.football.api-sports.io`
- Variável: `API_FOOTBALL_KEY`
- Limite configurável: `API_FOOTBALL_DAILY_LIMIT`
- Cobertura depende do plano do fornecedor e das competições disponíveis.
- Utilizado para ligas, épocas e fixtures normalizados.
- Não se assume cobertura de Girabola sem confirmação na conta real.

### Football-Data.org

- Base: API v4, `https://api.football-data.org/v4`
- Variável principal: `FOOTBALL_DATA_TOKEN`
- Compatibilidade temporária: `FOOTBALL_DATA_KEY`
- Limite configurável: `FOOTBALL_DATA_DAILY_LIMIT`
- Fonte complementar para competições e resultados.
- A cobertura gratuita pode excluir algumas competições e histórico.

## Limites e segurança

- Chaves apenas no backend e variáveis do Render.
- Cache persistente em `api_cache` com TTL configurável.
- Utilização registada em `api_usage`.
- Testes usam MockTransport ou respostas directas de fixtures, sem chamadas externas.
- Sincronizações não publicam dados até terem sido validadas e persistidas.

## Dados

Entidades: competições, temporadas, equipas, jogadores, jogos, executações de sincronização, utilização das APIs e cache.

## Render

A API pode executar sincronizações manualmente. Um Render Background Worker deve executar `python -m app.worker` com `WORKER_INTERVAL_SECONDS` configurado.

## Estado validado nesta entrega

- API-Football normaliza competições, épocas, fixtures e jogadores.
- Football-Data.org normaliza competições e fixtures.
- Cache persistente valida TTL e expiração.
- Limites por dia são aplicados por fornecedor antes da chamada externa.
- 15 testes passaram nas Fases 1 e 2, incluindo respostas mockadas sem chamadas externas reais.
- Migração `0002_sports_data` validada por upgrade.

## Endpoints

- `GET /api/v1/data/competitions`
- `GET /api/v1/data/competitions/{id}/seasons`
- `GET /api/v1/data/teams`
- `GET /api/v1/data/fixtures`
- `POST /api/v1/data/sync/competitions`
- `POST /api/v1/data/sync/fixtures`
- `GET /api/v1/data/players`
- `POST /api/v1/data/sync/players`
- `GET /api/v1/data/sync/runs`
- `GET /api/v1/data/sync/usage`
