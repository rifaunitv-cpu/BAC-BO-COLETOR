#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BAC BO — Serviço de Coleta
==========================
Responsabilidade ÚNICA: coletar o resultado mais recente do Bac Bo
via scraping e salvar no banco PostgreSQL compartilhado.

Roda a cada 30 segundos via APScheduler.
O Railway (análise + Telegram) consome os dados deste banco.
"""

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
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
INTERVALO_SEGUNDOS = int(os.environ.get("COLLECT_INTERVAL_SECONDS", "30"))
URL_TIPMINER = "https://www.tipminer.com/br/historico/blaze/bac-bo-ao-vivo"

if not DATABASE_URL:
    raise RuntimeError("❌ Variável DATABASE_URL não configurada!")

# ============================================================
# BANCO DE DADOS
# ============================================================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=5,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Resultado(Base):
    __tablename__ = "resultados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resultado: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    fonte: Mapped[str] = mapped_column(String(100), nullable=False, default="scraping")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def init_db():
    """Cria tabelas se não existirem (seguro para rodar junto com o Railway)."""
    Base.metadata.create_all(bind=engine)
    logger.info("✅ Banco de dados pronto.")


# ============================================================
# SCRAPER
# ============================================================

async def _scrape() -> str | None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="pt-BR",
        )
        page = await context.new_page()

        try:
            logger.info("[SCRAPER] Acessando TipMiner...")
            await page.goto(URL_TIPMINER, wait_until="domcontentloaded", timeout=30000)

            await page.wait_for_selector(
                'div[title*="PLAYER"], div[title*="BANKER"], div[title*="TIE"]',
                timeout=20000,
            )
            await asyncio.sleep(1)

            cells = await page.query_selector_all("div[title]")
            logger.info(f"[SCRAPER] {len(cells)} elementos encontrados")

            resultados = []
            for cell in cells:
                title = await cell.get_attribute("title") or ""
                match = re.search(r"(PLAYER|BANKER|TIE)", title)
                if match:
                    lado = match.group(1)
                    if lado == "PLAYER":
                        resultados.append("azul")
                    elif lado == "BANKER":
                        resultados.append("vermelho")
                    elif lado == "TIE":
                        resultados.append("branco")

            if not resultados:
                logger.warning("[SCRAPER] ❌ Nenhum resultado encontrado")
                return None

            mais_recente = resultados[-1]
            logger.info(f"[SCRAPER] ✅ {len(resultados)} resultados — mais recente: {mais_recente}")
            return mais_recente

        except Exception as e:
            logger.error(f"[SCRAPER] ❌ Erro: {e}")
            return None

        finally:
            await browser.close()


def _run_em_thread() -> str | None:
    """Roda o scraper async em thread isolada para evitar conflito de event loop."""
    def _nova_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_scrape())
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_nova_loop)
        return future.result(timeout=60)


# ============================================================
# COLETA + SAVE
# ============================================================

def coletar_e_salvar():
    """Coleta resultado e salva no banco se for novo."""
    logger.info("🔄 Iniciando ciclo de coleta...")

    try:
        valor = _run_em_thread()
    except Exception as e:
        logger.error(f"❌ Scraper falhou: {e}")
        return

    if valor is None:
        logger.warning("⚠️  Scraper retornou None — pulando ciclo.")
        return

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))  # testa conexão

        ultimo = (
            db.query(Resultado)
            .order_by(Resultado.timestamp.desc())
            .first()
        )

        if ultimo and ultimo.resultado == valor:
            logger.info(f"🔁 Resultado repetido ({valor}) — ignorando.")
            return

        novo = Resultado(
            resultado=valor,
            fonte="scraping-render",
            timestamp=datetime.now(timezone.utc),
        )
        db.add(novo)
        db.commit()
        logger.info(f"💾 Salvo: {valor}")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Erro ao salvar no banco: {e}")
    finally:
        db.close()


# ============================================================
# PING — evita hibernação no Render free
# ============================================================

def ping():
    """Mantém o serviço acordado no Render (plano gratuito hiberna após 15min)."""
    logger.debug("💓 Ping — serviço ativo")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    logger.info("🚀 BAC BO Coletor iniciando...")
    init_db()

    scheduler = BlockingScheduler(timezone="UTC")

    # Coleta a cada 30 segundos
    scheduler.add_job(
        coletar_e_salvar,
        "interval",
        seconds=INTERVALO_SEGUNDOS,
        id="coleta",
        max_instances=1,
        coalesce=True,
    )

    # Ping a cada 10 minutos (evita hibernação no Render free)
    scheduler.add_job(
        ping,
        "interval",
        minutes=10,
        id="ping",
    )

    logger.info(f"⏱️  Coleta configurada a cada {INTERVALO_SEGUNDOS}s")
    logger.info("✅ Scheduler iniciado — aguardando ciclos...")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Coletor encerrado.")
