# Fase 4 — Motor de inteligência MarBet (modelo 2.1.0)

## O que mudou (e porquê)
| Problema encontrado na auditoria | Correção |
|---|---|
| `engine.py` chamava `RankedPrediction` com 7 argumentos e o `top8.py` só aceitava 6 → a geração de palpites falhava (`EngineRun` FAILED) | campo `edge` (por omissão 0) em `top8.py` |
| Palpites publicados em mercados onde o modelo não bate a média histórica | só se publica em mercados **validados** no backtest (ganho de Log Loss ≥ 0,5% e ≥ 30 jogos fora da amostra) |
| Calibração (Platt) ajustada e avaliada nos mesmos dados | confirmada num bloco final que o ajuste não viu; só se aplica se melhorar o Log Loss |
| Nova versão do modelo anulava palpites publicados e permitia 2 palpites no mesmo jogo | publicados nunca são anulados; 1 palpite por jogo entre versões |
| Imutabilidade só por convenção | `published_snapshot` + guarda no SQLAlchemy (`immutability.py`) recusa alterar probabilidade/mercado/seleção/modelo |
| Faltavam mercados | + Dupla possibilidade, Empate anula, Mais/Menos 3,5, golos por equipa |
| Confiança numérica sem critérios | BAIXA/MODERADA/ELEVADA por critérios objetivos (não depende da probabilidade) |
| Sem fundamentação/riscos/data da análise | `summary`, `factors_for`, `risks`, `analysed_at`, `data_sources` por palpite |
| Liquidação só 3 mercados; empate anula inexistente | `settle_selection` para todos; empate em DNB = anulado |
| Ajuste lento (gradiente numérico) e rho fixo | gradiente analítico (verificado) e rho estimado por máxima verosimilhança |
| Backtest/ajuste repetidos de hora a hora sem dados novos | impressão digital dos jogos: salta se nada mudou |

## Novos ficheiros
`intelligence/markets.py, value.py, confidence.py, explain.py, evaluation.py, published_rules.py`, `backend/app/intelligence/immutability.py`, testes `test_marbet_engine.py` e `test_marbet_engine_db.py`.

## Endpoints novos
- `GET /api/v1/intelligence/fixtures/{id}/analysis` — probabilidades separadas do palpite oficial; recusa jogos terminados/adiados.
- `GET /api/v1/intelligence/admin/status` — painel (ADMIN/SUPERADMIN/ANALYST).
- `/performance` ganha `evaluation` (por mercado, por competição, calibração, degradação); `/top8` só lista palpites oficiais.

## Deploy no Render
Sem alterações de infraestrutura nem migrações (mercados e campos novos vivem em colunas existentes). Faz deploy normal; o worker cria o modelo `strength-dc 2.1.0`, executa backtest e só depois publica palpites. Palpites de 2.0.0 já publicados continuam e são liquidados normalmente.
Testes: `cd backend && pip install -r requirements.txt && PYTHONPATH=.. pytest -q`.

## Limitações conhecidas
- **Sem odds** nos fornecedores integrados: `value.py` está pronto, mas não há avaliação de valor (o motor diz isso em cada palpite).
- Sem lesões/ausências nem xG; desempenho por competição só ao nível de palpites liquidados.
- Peso do Elo escolhido no mesmo backtest que mede o desempenho (ligeiro otimismo).
