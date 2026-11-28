# Fase 4 - Histórico football-data.co.uk + odds em tempo real

## Tabelas novas (migração `0005_odds_aliases`)
- `fixture_odds`: odds em formato longo (jogo, fonte, bookmaker, mercado, selecção, linha, tipo, preço).
  - Mercados: `1X2` (H/D/A), `OU25` (OVER/UNDER, linha 2.5), `AH` (HOME/AWAY, linha = handicap).
  - Tipos (`kind`): `pre_match` (recolhida antes do jogo), `opening`, `closing`.
- `team_aliases`: nomes alternativos de equipas por fornecedor.

## Fontes
| Fornecedor | O que traz | Como corre |
|---|---|---|
| football-data.co.uk | Resultados + odds históricas (Bet365, Pinnacle, médias/máximos, fecho) | `run_import_footballdata` |
| The Odds API (plano grátis, 500 créditos/mês) | Odds actuais de jogos futuros (1X2, e Mais/Menos 2,5 se activar `totals`) | `run_ingest_odds` |
| 5DollarFootballAPI (plano grátis, 60 pedidos/hora e máximo 20/min) | Odds Bet365 de abertura/fecho das 5 grandes ligas; só 3 meses de histórico | `run_ingest_odds` |

OddsPapi **não** foi integrado (ver notas no fim).

## Comandos
    python -m app.run_import_footballdata                          # época actual, ligas principais
    python -m app.run_import_footballdata --seasons last:10 --leagues all
    python -m app.run_import_footballdata --seasons 2425 --leagues E0,SP1 --force
    python -m app.run_import_footballdata --local-dir ./csv        # <dir>/<época>/<Div>.csv
    python -m app.run_ingest_odds                                  # as duas APIs de odds
    python -m app.run_ingest_odds --providers 5dollar --days-back 2 --days-ahead 3
    python -m app.run_ingest_odds --list-5dollar-leagues           # confirmar ids das ligas

## GitHub Actions (secrets do repositório)
- `DATABASE_URL` (já existe), `THE_ODDS_API_KEY`, `FIVE_DOLLAR_API_KEY`.
- `import_footballdata.yml`: diário 05:30 UTC (+ manual com época/ligas/force).
- `ingest_odds.yml`: 05:10 e 17:10 UTC (+ manual).
- `intelligence.yml`: inalterado (00/06/12/18 UTC).

Variáveis opcionais: `THE_ODDS_API_MARKETS` (`h2h` por defeito; `h2h,totals` gasta o dobro de créditos),
`THE_ODDS_API_SPORTS`, `THE_ODDS_API_REGIONS`, `FIVE_DOLLAR_LEAGUE_IDS`, `ODDS_PREFERRED_BOOKMAKERS`
(`bet365,pinnacle,market_avg`), `ODDS_MAX_AGE_HOURS` (48).

Orçamento The Odds API: 5 ligas x 1 mercado x 1 região = 5 créditos por execução; 2 execuções/dia = ~300/mês.
O conector pára quando restarem menos de 25 créditos (`--odds-api-reserve`).

No 5DollarFootballAPI Free, as listas `/fixtures` não incluem odds. O importador consulta `/fixtures/{id}/odds` por jogo, conforme a documentação oficial. Para respeitar o limite de 20 pedidos/minuto, o cliente aplica um intervalo mínimo de 3,1 s e limita a 30 consultas individuais de odds por execução (além das consultas de calendário). Os jogos que excederem esse orçamento ficam para a execução seguinte.

## Regras de funcionamento
- Os conectores de odds **nunca criam jogos nem equipas**: só juntam odds a jogos que a API-Football/football-data.org já sincronizaram.
  Eventos sem jogo correspondente aparecem no log (`sem_jogo`) e em `sync_runs.metadata_json`.
- O importador do football-data.co.uk cria jogos históricos (`provider = football-data-uk`).
  Jogos que já existam de outro fornecedor (mesmas equipas, ±36 h) recebem só as odds.
- O `sync_service` deixou de criar um jogo se o football-data.co.uk já o importou (evita duplicados no treino).
- Equipas: alias guardado -> nome normalizado -> subconjunto de palavras único -> semelhança. Ligações aproximadas e
  equipas sem correspondência aparecem no log; corrigir acrescentando a `MANUAL_ALIASES` em `services/team_names.py`.

## Motor (valor esperado)
`IntelligenceEngine` usa agora odds reais de `fixture_odds` em `rationale["odds"]` (EV, probabilidade implícita, `quote`
com bookmaker/fonte/tipo/hora) para `1X2` e `TOTAL_GOALS` Mais/Menos 2,5. Sem odds recentes (<= 48 h) continua a não afirmar valor.
As regras de selecção/publicação de palpites **não** mudaram.

## Notas
- OddsPapi: o endpoint de jogos devolve só ids de participantes (sem nomes) e o histórico só cobre desde janeiro de 2026 em jogos
  terminados; o football-data.co.uk já dá odds históricas mais antigas. Fica como passo futuro, se fizer falta.
- O ficheiro `fixtures.csv` do football-data.co.uk (jogos futuros) não é usado.
