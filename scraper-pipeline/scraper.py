import asyncpg
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from pydantic import ValidationError

from database import claim_scrape_job, update_scrape_job
from models import QuoteItem


async def run_scraper_task(
    db_pool: asyncpg.Pool,
    job_id: int,
) -> None:
    browser = None
    pages_completed = 0
    items_inserted = 0
    stopped_reason = "requested_page_limit"

    try:
        job = await claim_scrape_job(db_pool, job_id)
        if job is None:
            return

        target_url = job["target_url"]
        page_limit = job["pages_requested"]
        card_selector = job["card_selector"]
        quote_selector = job["quote_selector"]
        author_selector = job["author_selector"]
        tags_selector = job["tags_selector"]
        pages_completed = job["pages_completed"]
        items_inserted = job["items_inserted"]

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()

            for page_number in range(pages_completed + 1, page_limit + 1):
                page_url = f"{target_url.rstrip('/')}/page/{page_number}"
                response = await page.goto(page_url, wait_until="domcontentloaded")

                if response is not None and response.status == 404:
                    if pages_completed == 0:
                        raise RuntimeError(
                            "Target returned HTTP 404 on the first page. "
                            "Check target_url and pagination format."
                        )
                    stopped_reason = "page_not_found"
                    print(f"Page {page_number} was not found; stopping pagination.")
                    break

                if response is not None and response.status >= 400:
                    raise RuntimeError(
                        f"Target returned HTTP {response.status} on page {page_number}."
                    )

                try:
                    await page.wait_for_selector(card_selector, timeout=5000)
                except PlaywrightTimeoutError:
                    if pages_completed == 0:
                        raise RuntimeError(
                            "No cards found on the first page. "
                            "Check target_url and selectors."
                        )
                    stopped_reason = "empty_page"
                    print(f"No cards found on page {page_number}; stopping pagination.")
                    break

                quote_cards = page.locator(card_selector)

                async with db_pool.acquire() as connection:
                    for index in range(await quote_cards.count()):
                        quote_card = quote_cards.nth(index)
                        content = {
                            "quote": await quote_card.locator(quote_selector).inner_text(),
                            "by": await quote_card.locator(author_selector).inner_text(),
                            "tags": await quote_card.locator(tags_selector).all_inner_texts(),
                        }

                        try:
                            quote_item = QuoteItem.model_validate(content)
                        except ValidationError as error:
                            print(f"Invalid quote: {error.errors()}")
                            continue

                        inserted_id = await connection.fetchval(
                            """
                            INSERT INTO quotes (quote, author, tags)
                            VALUES ($1, $2, $3)
                            ON CONFLICT (quote) DO NOTHING
                            RETURNING id
                            """,
                            quote_item.quote,
                            quote_item.by,
                            quote_item.tags,
                        )

                        if inserted_id is not None:
                            items_inserted += 1

                pages_completed += 1
                await update_scrape_job(
                    db_pool,
                    job_id,
                    "running",
                    pages_completed,
                    items_inserted,
                )

        await update_scrape_job(
            db_pool,
            job_id,
            "completed",
            pages_completed,
            items_inserted,
            stopped_reason=stopped_reason,
        )
        print(f"Scraping completed: {pages_completed} pages")

    except Exception as error:
        error_message = str(error)[:1000]
        await update_scrape_job(
            db_pool,
            job_id,
            "failed",
            pages_completed,
            items_inserted,
            error_message,
            stopped_reason="error",
        )
        print(f"Scraping failed: {error_message}")
    finally:
        if browser is not None:
            await browser.close()
