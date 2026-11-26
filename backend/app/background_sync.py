"""Sincronização em segundo plano dentro da própria API (substitui o worker pago).

Reaproveita app.worker.run_once. Corre numa thread separada para não bloquear
os pedidos da API. Desativar com API_BACKGROUND_SYNC=false.
"""
import asyncio
import logging
import os

from app.worker import run_once

logger = logging.getLogger("marbet.background_sync")


def _run_cycle() -> None:
    asyncio.run(run_once())


async def _loop() -> None:
    interval = int(os.getenv("WORKER_INTERVAL_SECONDS", "3600"))
    # Pequeno atraso para o arranque/health check da API terminar primeiro
    await asyncio.sleep(int(os.getenv("BACKGROUND_SYNC_START_DELAY", "20")))
    while True:
        try:
            logger.warning("Background sync: a iniciar ciclo")
            await asyncio.to_thread(_run_cycle)
            logger.warning("Background sync: ciclo concluído")
        except Exception:
            logger.exception("Background sync: ciclo falhou")
        await asyncio.sleep(interval)


def start_background_sync() -> None:
    if os.getenv("API_BACKGROUND_SYNC", "true").lower() in ("0", "false", "no"):
        return
    asyncio.get_running_loop().create_task(_loop())
