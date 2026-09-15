from contextlib import asynccontextmanager
import asyncio
import hmac

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query
import uvicorn

from config import DATABASE_URL, HOST, PORT, SCRAPE_API_KEY, SCRAPE_PAGES
from database import (
    create_db_pool,
    create_scrape_job,
    database_is_ready,
    fetch_quotes,
    fetch_scrape_job,
    recover_incomplete_scrape_job,
)
from models import ScrapeRequest
from scraper import run_scraper_task

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_db_pool(DATABASE_URL)
    recovered_job = await recover_incomplete_scrape_job(app.state.db_pool)
    app.state.resume_task = None
    if recovered_job is not None:
        app.state.resume_task = asyncio.create_task(
            run_scraper_task(app.state.db_pool, recovered_job["id"])
        )
    yield
    if app.state.resume_task is not None:
        app.state.resume_task.cancel()
        try:
            await app.state.resume_task
        except asyncio.CancelledError:
            pass
    await app.state.db_pool.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {
        "message": "Welcome to the Quote Scraper API",
        "status": "active",
        "documentation": "/docs",
        "endpoints": {
            "health_check": "/health",
            "fetch_quotes": "/api/quotes"
        }
    }



@app.get("/health")
async def health_check():
    await database_is_ready(app.state.db_pool)
    return {"status": "ok"}


@app.get("/api/quotes")
async def get_quotes(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    quotes, total = await fetch_quotes(app.state.db_pool, limit, offset)

    return {
        "data": quotes,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(quotes) < total,
        "next_offset": offset + limit if offset + len(quotes) < total else None,
    }


@app.post("/api/scrape/quotes", status_code=202)
async def trigger_scrape(
    request: ScrapeRequest,
    background_tasks: BackgroundTasks,
    api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    if not SCRAPE_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Scraping is not configured with an API key.",
        )

    if api_key is None or not hmac.compare_digest(api_key, SCRAPE_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key.")

    pages = request.pages or SCRAPE_PAGES
    job_id, active_job_id = await create_scrape_job(
        app.state.db_pool,
        str(request.target_url),
        pages,
        request.card_selector,
        request.quote_selector,
        request.author_selector,
        request.tags_selector,
    )
    if active_job_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A scrape job is already running: {active_job_id}",
        )
    if job_id is None:
        raise HTTPException(status_code=500, detail="Could not create scrape job.")

    background_tasks.add_task(
        run_scraper_task,
        app.state.db_pool,
        job_id,
    )

    return {
        "status": "processing",
        "job_id": job_id,
        "status_url": f"/api/scrape/jobs/{job_id}",
    }


@app.get("/api/scrape/jobs/{job_id}")
async def get_scrape_job(job_id: int):
    job = await fetch_scrape_job(app.state.db_pool, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scrape job not found.")
    return job

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=False,
    )