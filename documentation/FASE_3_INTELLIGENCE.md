# Fase 3 - MarBet Intelligence Engine

## Arquitetura

A Fase 3 separa cálculo estatístico, preparação de características, validação, geração e persistência:

- `intelligence/models`: Poisson, Elo, regressão logística e Random Forest.
- `intelligence/features`: características históricas anteriores ao início de cada jogo.
- `intelligence/validation`: divisões cronológicas, métricas e calibração.
- `intelligence/top8.py`: elegibilidade e distribuição preferencial TOP 8.
- `backend/app/intelligence/engine.py`: integração com SQLAlchemy, liquidação e execução idempotente.
- `backend/app/api/routes/intelligence.py`: consulta e tarefas administrativas.

## Dados utilizados

O motor utiliza apenas jogos persistidos pela Fase 2 com hora anterior ao jogo atual:

- Últimos 5 e 10 jogos.
- Vitórias, empates e derrotas.
- Golos marcados e sofridos.
- Médias de golos.
- Forma ponderada recente.
- Elo relativo das equipas.
- Cobertura da história para qualidade dos dados.

Dados futuros não são utilizados nas características. xG/xGA só são incluídos quando existentes na estrutura `statistics` do fornecedor.

## Modelos

### Poisson

A matriz é construída com $P(X=h,Y=a)=Poisson(h|\lambda_h)Poisson(a|\lambda_a)$.

Ela fornece vitória, empate, derrota, Over/Under 1.5 e 2.5 e ambas marcam.

### Elo

O Elo começa em 1500, usa fator $K=20$, ajustamento por diferença de golos e vantagem doméstica nominal de 65 pontos.

### Regressão logística

A regressão logística é treinada apenas com pelo menos 50 observações, usando normalização e labels 0/1/2 para casa/empate/fora.

### Random Forest

Foi implementado como modelo complementar para pelo menos 50 observações. Nesta entrega não é combinado por defeito com Poisson/Elo; combinações só devem entrar em produção após backtesting comparável.

## Calibração

O sistema produz binning de calibração e calcula Brier Score e Log Loss quando existem amostras. Probabilidades são normalizadas nos mercados 1X2. Nenhum valor é apresentado como garantia.

## TOP 8

São elegíveis apenas prognósticos com probabilidade mínima de 0.58 e qualidade mínima de 0.40. O ranking combina probabilidade e confiança. A preferência é:

- 2 conservadores.
- 4 equilibrados.
- 2 ousados.

A distribuição não é forçada; menos de oito previsões é apresentado quando a qualidade é insuficiente.

## Backtesting

As divisões são cronológicas. O conjunto de treino precede sempre o conjunto de teste. O guard de leakage rejeita o primeiro índice de teste anterior ao corte.

Sem um histórico real suficiente, o modelo não é ativado como preciso. A entrega apresenta o estado real em `INSUFFICIENT_DATA` e não cria métricas artificiais.

## Imutabilidade e liquidação

Uma previsão tem chave única por jogo, mercado, seleção e versão do modelo. Regerações não substituem previsões existentes. A liquidação atualiza apenas estado, resultado, acerto e data de liquidação.

## API

- `GET /api/v1/intelligence/predictions`
- `GET /api/v1/intelligence/top8`
- `GET /api/v1/intelligence/fixtures/{fixture_id}/probabilities`
- `GET /api/v1/intelligence/metrics`
- `GET /api/v1/intelligence/models`
- `GET /api/v1/intelligence/backtests`
- `GET /api/v1/intelligence/history`
- `GET /api/v1/intelligence/runs`
- `POST /api/v1/intelligence/admin/train`
- `POST /api/v1/intelligence/admin/generate`
- `POST /api/v1/intelligence/admin/settle`

Os três POST exigem role `ADMIN` ou `SUPERADMIN`.

## Worker

O worker Render executa sincronização existente e depois treino/backtesting, geração e liquidação. Previsões têm chave única e liquidação ignora previsões já liquidadas, garantindo idempotência.

## Limitações reais

- A qualidade depende do histórico efetivamente sincronizado.
- API-Football e Football-Data.org podem não disponibilizar xG/xGA ou jogadores no plano contratado.
- Sem 30 jogos terminados não existe conclusão de backtesting suficiente.
- Sem 50 observações não existe treino da regressão logística ou Random Forest.
- Não existe resultado de backtesting real sem dados históricos reais.

## Testes

```powershell
cd C:\Marbet\backend
C:\Marbet\.venv\Scripts\python.exe -m pytest -q
```

Os testes da Fase 3 usam dados controlados e não realizam chamadas externas.
