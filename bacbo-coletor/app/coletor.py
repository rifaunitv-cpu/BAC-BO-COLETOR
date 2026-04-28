#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================================================
# Coletor Bac Bo — API da Blaze (sem Playwright, sem login)
# ============================================================

import logging
import os
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.blocking import BlockingScheduler
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

# API pública da Blaze — funciona de São Paulo sem bloqueio
URL_API = "https://blaze.bet.br/api/singleplayer-originals/originals/bac_bo/recent/1/simple"
TIMEOUT = 15

if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL não configurada!")

# ============================================================
# MAPEAMENTO
#   player / jogador = AZUL      🔵
#   banker / banca   = VERMELHO  🔴
#   tie    / empate  = BRANCO    ⚪
# ============================================================
MAPA_RESULTADO = {
    "player":  "azul",
    "banker":  "vermelho",
    "tie":     "branco",
    "jogador": "azul",
    "banca":   "vermelho",
    "empate":  "branco",
    "1":       "azul",
    "2":       "vermelho",
    "0":       "branco",
}

# ============================================================
# BANCO DE DADOS
# ============================================================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=5,
    connect_args={"sslmode": "require"},
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
# SCRAPER — API da Blaze
# ============================================================

def coletar_resultado() -> str | None:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://blaze.bet.br/",
        }

        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.get(URL_API, headers=headers)

        if response.status_code != 200:
            logger.warning(f"API Blaze retornou status {response.status_code}")
            return None

        data = response.json()

        # Pega a rodada mais recente
        if isinstance(data, list) and len(data) > 0:
            rodada = data[0]
        elif isinstance(data, dict):
            rodada = data
        else:
            logger.warning("Formato inesperado da API Blaze")
            return None

        # Tenta os campos mais comuns
        resultado_raw = (
            rodada.get("winner") or
            rodada.get("result") or
            rodada.get("color") or
            rodada.get("side") or
            rodada.get("outcome") or
            ""
        )

        resultado_raw = str(resultado_raw).lower().strip()
        valor = MAPA_RESULTADO.get(resultado_raw)

        if valor:
            logger.info(f"✅ Coletado: {valor} (raw='{resultado_raw}')")
            return valor

        logger.warning(f"Campo desconhecido: '{resultado_raw}' — rodada: {rodada}")
        return None

    except httpx.TimeoutException:
        logger.error(f"Timeout na API Blaze ({TIMEOUT}s)")
        return None
    except Exception as e:
        logger.error(f"❌ Erro ao coletar: {e}")
        return None


# ============================================================
# SALVAR DADOS
# ============================================================

def coletar_e_salvar():
    logger.info("🔄 Coletando...")

    resultado = coletar_resultado()

    if not resultado:
        logger.warning("Sem resultado — pulando ciclo")
        return

    db = SessionLocal()

    try:
        db.execute(text("SELECT 1"))

        ultimo = db.query(Resultado).order_by(Resultado.id.desc()).first()

        if ultimo and ultimo.resultado == resultado:
            logger.info("🔁 Repetido — ignorando")
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
