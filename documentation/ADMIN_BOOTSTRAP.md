# Primeiro administrador MarBet

Não use o endpoint público de registo para criar um administrador.

## Render sem Shell pago

Adicione temporariamente como Environment Variables:

- `MARBET_ADMIN_EMAIL`
- `MARBET_ADMIN_NAME`
- `MARBET_ADMIN_PASSWORD`

A password deve ter pelo menos 12 caracteres. Faça **Manual Deploy** do Web Service. O startup da API executa o bootstrap automaticamente após as migrações.

O script cria ou atualiza o utilizador como `SUPERADMIN`, plano `ELITE`, activo e email verificado.

Depois do bootstrap:

1. Remova `MARBET_ADMIN_PASSWORD` das variáveis Render.
2. Opcionalmente mantenha `MARBET_ADMIN_EMAIL` e `MARBET_ADMIN_NAME` ou remova-as também.
3. Faça login pelo endpoint `/api/v1/auth/login`.
4. Utilize o access token nas rotas `/api/v1/data/sync/*` e `/api/v1/intelligence/admin/*`.

Não coloque a password do administrador no Git, no frontend ou no código.
