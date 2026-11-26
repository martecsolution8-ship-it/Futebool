"""Executa só a parte pesada (treino, previsões e liquidação) uma vez e termina.

Pensado para correr fora da API (ex.: GitHub Actions), onde há memória de sobra.
A sincronização de jogos continua a ser feita pela API.
"""
import logging
import sys

from app.db.session import SessionLocal
from app.intelligence.engine import IntelligenceEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("marbet.run_intelligence")


def main() -> int:
    db = SessionLocal()
    try:
        engine = IntelligenceEngine(db)
        logger.info("A treinar e a fazer backtest")
        engine.train_and_backtest()
        logger.info("A gerar previsões")
        engine.generate_predictions()
        logger.info("A liquidar previsões")
        engine.settle_predictions()
        logger.info("Concluído")
        return 0
    except Exception:
        logger.exception("Falhou")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
