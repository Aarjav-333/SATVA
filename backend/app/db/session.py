"""Engine and session management.

Two engines exist on purpose:

* `engine`          -- full application access.
* `analytics_engine` -- used by the clustering and heatmap workloads. In a full
  deployment its URL points at a database role with no grant on the `identity`
  schema. `analytics_session()` additionally sets a restrictive `search_path`
  so that an accidental unqualified reference to an identity table fails at the
  database rather than silently succeeding.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import ANALYTICS_SCHEMA

engine: Engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=settings.sql_echo,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

analytics_engine: Engine = create_engine(
    settings.analytics_url,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    echo=settings.sql_echo,
    future=True,
)

AnalyticsSessionLocal = sessionmaker(
    bind=analytics_engine, autoflush=False, expire_on_commit=False, future=True
)


@event.listens_for(analytics_engine, "connect")
def _restrict_analytics_search_path(dbapi_connection, _record) -> None:
    """Keep the identity schema out of reach of analytics connections."""
    with dbapi_connection.cursor() as cur:
        cur.execute(f"SET search_path TO {ANALYTICS_SCHEMA}, public")


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency. One transaction per request, committed by the route."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Transactional scope for workers and scripts."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def analytics_session() -> Generator[Session, None, None]:
    """Session for identity-blind workloads (clustering, public heatmap)."""
    db = AnalyticsSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ping() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
