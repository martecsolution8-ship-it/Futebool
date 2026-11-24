# Relatório de integração real - 2026-11-21

## Estado externo

- URL consultada: `https://futebool.onrender.com`
- Resultado inicial: Render mostrou `503` com página de carregamento do serviço.
- Não foi possível confirmar ainda liveness, readiness, banco Render ou endpoints da aplicação.
- Nenhum valor de credencial foi exibido ou armazenado no relatório.

## Correções efetuadas

- Football-Data.org aceita agora `FOOTBALL_DATA_TOKEN` como variável principal.
- Mantido `FOOTBALL_DATA_KEY` como fallback legado.
- Web Service e Background Worker usam `FOOTBALL_DATA_TOKEN` no Render Blueprint.
- Equipas e competições com nome/código equivalente reutilizam registros normalizados entre fornecedores.
- O endpoint `/api/v1/data/providers/status` não exige chave e não devolve credenciais.

## Limites operacionais

- Nenhuma chamada real foi feita com credenciais durante os testes locais.
- Os testes usam MockTransport e respostas simuladas claramente identificadas.
- Sincronizações externas só devem começar após liveness, readiness e acesso administrativo.
- Usar pelo menos uma lista limitada de competições; não executar sincronizações massivas.

## Próxima etapa após o serviço ficar disponível

1. Confirmar `/health/live` e `/health/ready` com HTTP 200.
2. Confirmar `GET /api/v1/data/providers/status` sem exposição de valores secretos.
3. Autenticar um usuário ADMIN no backend.
4. Executar `/api/v1/data/sync/competitions` uma vez por fornecedor.
5. Consultar `/api/v1/data/sync/runs` e `/api/v1/data/sync/usage`.
6. Validar PostgreSQL por `/api/v1/data/competitions` e `/api/v1/data/teams`.
7. Comparar IDs externos e nomes normalizados antes de fixtures e jogadores.
8. Gerar ou backtestar apenas após cobertura real suficiente.
