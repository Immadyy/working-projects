# Quote Scraper API

A FastAPI service that scrapes quotes with Playwright and stores them in PostgreSQL.

## Local setup

Create the environment file from the template:

```bash
cp .env.example .env
```

Edit `.env` and replace `your_password` with your local PostgreSQL password.
Set `SCRAPE_API_KEY` to a long random value; clients must send it in the
`X-API-Key` header when starting a scrape.

Create and activate the virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Start the API:

```bash
python main.py
```

Trigger a scrape:

```bash
curl -X POST http://127.0.0.1:8000/api/scrape/quotes \
	-H "X-API-Key: replace-with-a-long-random-key" \
	-H "Content-Type: application/json" \
	-d '{"pages": 5}'
```

The endpoint returns `202` immediately with a `job_id`. The scraper continues in
the background. Poll the returned status URL:

```bash
curl http://127.0.0.1:8000/api/scrape/jobs/1
```

Jobs move through `pending`, `running`, `completed`, or `failed`. A completed
job reports `pages_completed`, `items_inserted`, and `stopped_reason`.
`stopped_reason` is `requested_page_limit` when all requested pages were
processed, `empty_page` when pagination reached a page with no matching cards,
or `page_not_found` when a later page returned HTTP 404. These are normal
partial-completion outcomes, not errors. A missing first page or another HTTP
error marks the job as `failed` with an actionable `error_message`.

Jobs are resumable. The API stores the requested URL, selectors, and progress
in PostgreSQL. If the service restarts while a job is running, startup finds
that unfinished job and continues at the next page. A page may be visited
again if the process stopped just before its progress was saved, but the
unique quote constraint and `ON CONFLICT DO NOTHING` prevent duplicate rows.

The request can override the demo target and selectors:

```json
{
	"target_url": "https://quotes.toscrape.com/js/",
	"pages": 5,
	"card_selector": ".quote",
	"quote_selector": ".text",
	"author_selector": ".author",
	"tags_selector": ".tag"
}
```

Only one scrape job is allowed at a time in this demo, preventing accidental
duplicate work and resource contention.

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Read stored quotes:

```bash
curl "http://127.0.0.1:8000/api/quotes?limit=50&offset=0"
```

The response is paginated and includes `total`, `has_more`, and `next_offset`.
Use `next_offset` for the next request until `has_more` is `false`. The API
limits each response to 100 quotes, so a client never has to download an
unbounded response.

## Docker

Build and run the image with a local `.env` file:

```bash
docker build -t quote-scraper .
docker run --env-file .env -p 8000:8000 quote-scraper
```

Run those commands from the `scraper-pipeline` directory. Render should use
that directory as the Docker build root, with `Dockerfile` as its Dockerfile.

## Render

Create a PostgreSQL database and a web service from this repository. Add the database's internal connection string as the `DATABASE_URL` environment variable in the Render dashboard. Render provides `PORT` automatically; the application listens on it.
Also set `SCRAPE_API_KEY` in Render to a long random secret. Keep the API key
private and share it only with the authorized client.

Do not commit `.env`. Only `.env.example` belongs in the repository.

## Project layout

- `main.py`: FastAPI application, lifecycle, and API route
- `config.py`: environment configuration
- `database.py`: PostgreSQL pool, schema, quotes, and scrape jobs
- `models.py`: Pydantic validation and scrape request models
- `scraper.py`: configurable Playwright scraping and database inserts
