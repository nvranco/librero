"""Pool de conexiones asyncpg + arranque del schema."""

from pathlib import Path

import asyncpg

from app.config import DATABASE_URL

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_pool: asyncpg.Pool | None = None


async def conectar() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    async with _pool.acquire() as conn:
        await conn.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return _pool


async def cerrar() -> None:
    if _pool is not None:
        await _pool.close()


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("El pool de DB todavia no fue inicializado.")
    return _pool
