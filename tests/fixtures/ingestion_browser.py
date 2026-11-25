"""Local production ingestion app; only the external signed OIDC transport is fake.

Generated UNIT source declarations/PDFs are engineering inputs. No external
network or model, real source permission or procurement acceptance is involved.
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

import httpx2
import uvicorn
from bidops.agent_demo.runtime import DemoSettings
from bidops.api import create_app
from bidops.config import Settings
from bidops.ingestion.config import IngestionSettings
from bidops.jobs.config import JobsSettings
from bidops.qualification.config import ScreeningSettings
from bidops.review.config import ReviewSettings
from pydantic_ai import models

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_enterprise as oidc  # noqa: E402
from test_attachment_parser import pdf  # noqa: E402
from test_ingestion_api import BODY, registry  # noqa: E402

models.ALLOW_MODEL_REQUESTS = False
data = Path(os.environ["BIDOPS_INGESTION_BROWSER_DATA"])
data.mkdir(parents=True, exist_ok=True)
origin = os.environ["BIDOPS_INGESTION_BROWSER_ORIGIN"]
oidc.ORIGIN = origin


class BrowserProvider(oidc.ProtocolProvider):
    def handle(self, request):
        if request.url.path.endswith("/token"):
            code = json.loads(parse_qs(request.content.decode())["code"][0])
            self.subject, self.roles = code["sub"], code["roles"]
            self.nonce, self.challenge = code["nonce"], code["challenge"]
        return super().handle(request)


settings = Settings(
    data_dir=data,
    database_url="sqlite:///" + str(data / "browser.sqlite3"),
    source_registry=registry(data / "sources.json"),
    network_enabled=False,
)
(data / "notice.txt").write_text(BODY)
(data / "proof.txt").write_text("UNIT_INGESTION_BROWSER plain attachment")
(data / "two-pages.pdf").write_bytes(pdf("UNIT_INGESTION first page", "UNIT_INGESTION second page"))
(data / "scan.pdf").write_bytes(pdf(None))
app = create_app(
    settings,
    DemoSettings(enabled=False, _env_file=None),
    oidc.enterprise(allow_insecure_loopback=True, requests_per_minute=120, _env_file=None),
    screening_settings=ScreeningSettings(enabled=False, live_calls_enabled=False, _env_file=None),
    jobs_settings=JobsSettings(enabled=False, _env_file=None),
    review_settings=ReviewSettings(enabled=False, _env_file=None),
    ingestion_settings=IngestionSettings(enabled=True, _env_file=None),
)
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(app):
    async with original_lifespan(app):
        app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
            BrowserProvider().handle
        )
        yield


app.router.lifespan_context = lifespan
if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["BIDOPS_INGESTION_BROWSER_PORT"]),
        log_level="warning",
    )
