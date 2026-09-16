import asyncpg


CREATE_QUOTES_TABLE = """
CREATE TABLE IF NOT EXISTS quotes (
    id SERIAL PRIMARY KEY,
    quote TEXT NOT NULL UNIQUE,
    author TEXT NOT NULL,
    tags TEXT[] NOT NULL
)
"""

CREATE_SCRAPE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS scrape_jobs (
    id SERIAL PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    target_url TEXT NOT NULL,
    pages_requested INTEGER NOT NULL,
    pages_completed INTEGER NOT NULL DEFAULT 0,
    items_inserted INTEGER NOT NULL DEFAULT 0,
    card_selector TEXT NOT NULL DEFAULT '.quote',
    quote_selector TEXT NOT NULL DEFAULT '.text',
    author_selector TEXT NOT NULL DEFAULT '.author',
    tags_selector TEXT NOT NULL DEFAULT '.tag',
    error_message TEXT,
    stopped_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
)
"""


async def create_db_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(database_url)

    async with pool.acquire() as connection:
        await connection.execute(CREATE_QUOTES_TABLE)
        await connection.execute(CREATE_SCRAPE_JOBS_TABLE)
        await connection.execute(
            "ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS card_selector TEXT NOT NULL DEFAULT '.quote'"
        )
        await connection.execute(
            "ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS quote_selector TEXT NOT NULL DEFAULT '.text'"
        )
        await connection.execute(
            "ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS author_selector TEXT NOT NULL DEFAULT '.author'"
        )
        await connection.execute(
            "ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS tags_selector TEXT NOT NULL DEFAULT '.tag'"
        )
        await connection.execute(
            "ALTER TABLE scrape_jobs ADD COLUMN IF NOT EXISTS stopped_reason TEXT"
        )

    return pool


async def create_scrape_job(
    pool: asyncpg.Pool,
    target_url: str,
    pages_requested: int,
    card_selector: str,
    quote_selector: str,
    author_selector: str,
    tags_selector: str,
) -> tuple[int | None, int | None]:
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(874321)")
            active_job_id = await connection.fetchval(
                """
                SELECT id
                FROM scrape_jobs
                WHERE status IN ('pending', 'running')
                ORDER BY id DESC
                LIMIT 1
                """
            )

            if active_job_id is not None:
                return None, active_job_id

            job_id = await connection.fetchval(
                """
                INSERT INTO scrape_jobs (
                    status, target_url, pages_requested, card_selector,
                    quote_selector, author_selector, tags_selector
                )
                VALUES ('pending', $1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                target_url,
                pages_requested,
                card_selector,
                quote_selector,
                author_selector,
                tags_selector,
            )

    return job_id, None


async def update_scrape_job(
    pool: asyncpg.Pool,
    job_id: int,
    status: str,
    pages_completed: int | None = None,
    items_inserted: int | None = None,
    error_message: str | None = None,
    stopped_reason: str | None = None,
) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE scrape_jobs
            SET status = $2,
                pages_completed = COALESCE($3, pages_completed),
                items_inserted = COALESCE($4, items_inserted),
                error_message = $5,
                stopped_reason = $6,
                started_at = CASE
                    WHEN $2 = 'running' AND started_at IS NULL THEN NOW()
                    ELSE started_at
                END,
                finished_at = CASE
                    WHEN $2 IN ('completed', 'failed') THEN NOW()
                    ELSE finished_at
                END
            WHERE id = $1
            """,
            job_id,
            status,
            pages_completed,
            items_inserted,
            error_message,
            stopped_reason,
        )


async def fetch_scrape_job(pool: asyncpg.Pool, job_id: int) -> dict | None:
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT id, status, target_url, pages_requested, pages_completed,
                     items_inserted, card_selector, quote_selector, author_selector,
                     tags_selector, error_message, stopped_reason,
                     created_at, started_at, finished_at
            FROM scrape_jobs
            WHERE id = $1
            """,
            job_id,
        )

    return dict(row) if row else None


async def claim_scrape_job(pool: asyncpg.Pool, job_id: int) -> dict | None:
    """Atomically claim a pending job so only one worker can run it."""
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            UPDATE scrape_jobs
            SET status = 'running',
                started_at = COALESCE(started_at, NOW()),
                error_message = NULL
            WHERE id = $1 AND status = 'pending'
            RETURNING id, status, target_url, pages_requested, pages_completed,
                      items_inserted, card_selector, quote_selector,
                      author_selector, tags_selector
            """,
            job_id,
        )

    return dict(row) if row else None


async def recover_incomplete_scrape_job(pool: asyncpg.Pool) -> dict | None:
    """Return one unfinished job for the startup worker to resume."""
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(874321)")
            await connection.execute(
                """
                UPDATE scrape_jobs
                SET status = 'pending', error_message = NULL
                WHERE status = 'running'
                """
            )
            row = await connection.fetchrow(
                """
                SELECT id, target_url, pages_requested, pages_completed,
                       items_inserted, card_selector, quote_selector,
                       author_selector, tags_selector
                FROM scrape_jobs
                WHERE status = 'pending'
                ORDER BY id DESC
                LIMIT 1
                """
            )

    return dict(row) if row else None


async def database_is_ready(pool: asyncpg.Pool) -> bool:
    async with pool.acquire() as connection:
        await connection.execute("SELECT 1")
    return True


async def fetch_quotes(
    pool: asyncpg.Pool,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    async with pool.acquire() as connection:
        total = await connection.fetchval("SELECT COUNT(*) FROM quotes")
        rows = await connection.fetch(
            """
            SELECT id, quote, author, tags
            FROM quotes
            ORDER BY id
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )

    quotes = [
        {
            "id": row["id"],
            "quote": row["quote"],
            "by": row["author"],
            "tags": row["tags"],
        }
        for row in rows
    ]

    return quotes, total
