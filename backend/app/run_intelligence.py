"""Executa só a parte pesada (treino, previsões e liquidação) uma vez e termina.

Pensado para correr fora da API (ex.: GitHub Actions), onde há memória de sobra.
A sincronização de jogos continua a ser feita pela API.
"""
import logging
import sys
import time

from app.db.session import SessionLocal
from app.intelligence.engine import IntelligenceEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("marbet.run_intelligence")


def main() -> int:
    started = time.perf_counter()
    db = SessionLocal()
    try:
        logger.info("A iniciar motor MarBet...")
        engine = IntelligenceEngine(db)

        stage_started = time.perf_counter()
        training = engine.train_and_backtest()
        logger.info("Treino/backtest concluído em %.2f segundos; estado=%s", time.perf_counter() - stage_started, training.status.value)

        logger.info("A carregar histórico...")
        stage_started = time.perf_counter()
        logger.info("A selecionar jogos futuros...")
        generation = engine.generate_predictions()
        logger.info("Seleção concluída em %.2f segundos; estado=%s", time.perf_counter() - stage_started, generation.status.value)
        generation_metadata = generation.metadata_json or {}
        logger.info("Histórico carregado: %s jogos.", generation_metadata.get("finished_matches", 0))
        logger.info("Jogos elegíveis: %s.", generation_metadata.get("eligible_fixtures", generation.records_processed))
        logger.info("Jogos recusados: %s.", generation_metadata.get("insufficient_or_refused", 0))
        logger.info("Palpites publicados: %s.", generation_metadata.get("published_new", 0))

        stage_started = time.perf_counter()
        settlement = engine.settle_predictions()
        logger.info("Liquidação concluída em %.2f segundos; estado=%s", time.perf_counter() - stage_started, settlement.status.value)

        logger.info("Execução concluída.")
        logger.info("Tempo total: %.2f segundos", time.perf_counter() - started)
        return 0
    except Exception:
        logger.exception("Falhou")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
