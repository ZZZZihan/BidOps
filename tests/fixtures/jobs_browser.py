"""Real local Jobs HTTP server with the existing signed-OIDC/offline model fixture.

The wrapper starts actual DBOS only after screening_browser has installed its
FunctionModel. No production authentication, authorization or review bypass is
added. These authored UNIT answers are engineering fixtures, not quality scores.
"""

import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from bidops.jobs.config import JobsSettings
from bidops.jobs.runtime import JobsService
from pydantic_ai import models

# The original fixture remains unchanged. Its app must not start a Jobs worker
# before its lifespan installs the offline FunctionModel, even under a dev env.
os.environ["BIDOPS_JOBS_ENABLED"] = "false"
os.environ["BIDOPS_SCREENING_BROWSER_DATA"] = os.environ["BIDOPS_JOBS_BROWSER_DATA"]
os.environ["BIDOPS_SCREENING_BROWSER_ORIGIN"] = os.environ["BIDOPS_JOBS_BROWSER_ORIGIN"]

import screening_browser as fixture  # noqa: E402

models.ALLOW_MODEL_REQUESTS = False
app = fixture.app
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(app):
    async with original_lifespan(app):
        service = JobsService(
            app.state.screening, fixture.data, JobsSettings(enabled=True, _env_file=None)
        )
        app.state.jobs = service
        try:
            await asyncio.to_thread(service.start)
            yield
        finally:
            await asyncio.to_thread(service.close)


app.router.lifespan_context = lifespan
if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["BIDOPS_JOBS_BROWSER_PORT"]),
        log_level="warning",
    )
