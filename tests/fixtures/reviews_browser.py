"""Actual review HTTP integration on the existing signed-OIDC/DBOS fixture.

Only external IdP transport and authored UNIT model answers are simulated. The
production review service, identities, source ACLs, events and DBOS are real.
No approval or authentication endpoint is added to the application.
"""

import os
from contextlib import asynccontextmanager

import uvicorn
from bidops.review.config import ReviewSettings
from bidops.review.service import ReviewService
from pydantic_ai import models

os.environ["BIDOPS_REVIEW_ENABLED"] = "false"
os.environ["BIDOPS_JOBS_BROWSER_DATA"] = os.environ["BIDOPS_REVIEWS_BROWSER_DATA"]
os.environ["BIDOPS_JOBS_BROWSER_ORIGIN"] = os.environ["BIDOPS_REVIEWS_BROWSER_ORIGIN"]

import jobs_browser as fixture  # noqa: E402

models.ALLOW_MODEL_REQUESTS = False
app = fixture.app
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(app):
    async with original_lifespan(app):
        app.state.review = ReviewService(
            app.state.screening,
            app.state.jobs,
            ReviewSettings(enabled=True, _env_file=None),
        )
        yield


app.router.lifespan_context = lifespan
if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["BIDOPS_REVIEWS_BROWSER_PORT"]),
        log_level="warning",
    )
