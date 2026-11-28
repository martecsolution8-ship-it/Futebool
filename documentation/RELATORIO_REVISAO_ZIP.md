# Relatório de revisão e alterações — Futebool / MarBet

Data: 28-11-2026

## Alterações nesta revisão

- Corrigido o conector 5DollarFootballAPI para o plano Free: as listas `/v1/fixtures` não fornecem odds no Free, por isso o cliente consulta `/v1/fixtures/{id}/odds` por jogo quando a lista não contém odds.
- Normalizado o envelope actual `data.bookmakers[].odds` da resposta da API, mantendo compatibilidade com o formato simplificado usado pelos testes existentes.
- Aplicado intervalo mínimo de 3,1 segundos entre pedidos para respeitar o limite documentado de 20 pedidos/minuto.
- Limitadas a 30 as consultas individuais de odds por execução, além dos pedidos de calendário, para reduzir o risco de esgotar o limite horário do plano Free. O relatório de sincronização assinala quando esse orçamento é atingido.
- Corrigidos testes antigos que não respeitavam a regra actual de liquidar apenas palpites publicados (`rationale.published_pick`) e que assumiam a existência de um ModelVersion antes de o motor o criar.
- Acrescentados testes para o envelope actual de odds e para a consulta individual por fixture.
- Actualizada a documentação da Fase 4.

## Validação executada

- `python -m compileall -q backend/app backend/tests`: passou.
- `pytest -q`: 112 passaram.
- Alembic SQLite: `upgrade head` → `downgrade 0004_fixture_states` → `upgrade head`: passou.
- Não foi executado um backfill real nem ingestão live: o ZIP não contém CSVs de dados nem credenciais de API/base de dados disponíveis neste ambiente.

## Observações do ambiente de teste

O ambiente de execução não tinha `python-jose` instalado e não tinha acesso à Internet para instalar dependências. Para executar a suite completa, foi usado temporariamente um módulo de compatibilidade local, fora do projecto, que encaminhou as funções JWT para PyJWT já instalado. Esse shim não foi incluído no ZIP. No ambiente de desenvolvimento/deploy, instalar as dependências oficiais com `pip install -r requirements.txt`.

A suite emitiu quatro avisos de descontinuação relativos a `@app.on_event` do FastAPI; não impediram os testes.

## Antes de substituir no ambiente publicado

1. Fazer backup da base de dados.
2. Aplicar a migração `alembic upgrade head` no ambiente correcto.
3. Confirmar as chaves `THE_ODDS_API_KEY` e `FIVE_DOLLAR_API_KEY` nos segredos do GitHub Actions, se estes fornecedores forem utilizados.
4. Executar primeiro o workflow manual de importação/ingestão e verificar os logs e `sync_runs`.
5. Só depois validar o workflow de inteligência e o site publicado.

Nenhuma chave, palavra-passe ou segredo foi adicionado ao código ou ao ZIP.
