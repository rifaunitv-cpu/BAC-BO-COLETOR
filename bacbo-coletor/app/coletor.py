#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import concurrent.futures
import logging
import os
import re
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from playwright.async_api import async_playwright
from sqlalchemy import create_engine, text, String, DateTime, Integer, func
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column

# ============================================================
# CONFIGURAÇÃO
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
INTERVALO_SEGUNDOS = int(os.environ.get("COLLECT_INTERVAL_SECONDS", "30"))

URL_TIPMINER = "https://www.tipminer.com/br/historico/blaze/bac-bo-ao-vivo"

if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL não configurada!")

# ============================================================
# BANCO DE DADOS (COM SSL PARA RENDER/RAILWAY)
# ============================================================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=5,
    connect_args={"sslmode": "require"},  # 🔥 ESSENCIAL
)

SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Resultado(Base):
    __tablename__ = "resultados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resultado: Mapped[str] = mapped_column(String(50), index=True)
    fonte: Mapped[str] = mapped_column(String(100), default="scraping")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def init_db():
    Base.metadata.create_all(bind=engine)
    logger.info("✅ Banco conectado e pronto")


# ============================================================
# SCRAPER
# ============================================================

async def _scrape():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = await browser.new_context()
        page = await context.new_page()

        try:
            logger.info("🌐 Acessando site...")
            await page.goto(URL_TIPMINER, timeout=30000)

            await page.wait_for_selector("div[title]", timeout=20000)

            elementos = await page.query_selector_all("div[title]")

            resultados = []
            for el in elementos:
                title = await el.get_attribute("title") or ""
                if "PLAYER" in title:
                    resultados.append("azul")
                elif "BANKER" in title:
                    resultados.append("vermelho")
                elif "TIE" in title:
                    resultados.append("branco")

            if not resultados:
                logger.warning("❌ Nenhum resultado encontrado")
                return None

            return resultados[-1]

        except Exception as e:
            logger.error(f"❌ Erro scraping: {e}")
            return None

        finally:
            await browser.close()


def run_scraper():
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_scrape())

    with concurrent.futures.ThreadPoolExecutor() as executor:
        return executor.submit(run).result()


# ============================================================
# SALVAR DADOS
# ============================================================

def coletar_e_salvar():
    logger.info("🔄 Coletando...")

    resultado = run_scraper()

    if not resultado:
        return

    db = SessionLocal()

    try:
        db.execute(text("SELECT 1"))

        ultimo = db.query(Resultado).order_by(Resultado.id.desc()).first()

        if ultimo and ultimo.resultado == resultado:
            logger.info("🔁 Repetido, ignorando")
            return

        novo = Resultado(
            resultado=resultado,
            fonte="render",
            timestamp=datetime.now(timezone.utc),
        )

        db.add(novo)
        db.commit()

        logger.info(f"💾 Salvo: {resultado}")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Erro banco: {e}")

    finally:
        db.close()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    logger.info("🚀 Iniciando coletor...")

    init_db()

    scheduler = BlockingScheduler()

    scheduler.add_job(
        coletar_e_salvar,
        "interval",
        seconds=INTERVALO_SEGUNDOS,
        max_instances=1,
    )

    logger.info("⏱️ Rodando...")

    scheduler.start()
