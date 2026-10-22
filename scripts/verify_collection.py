"""Offline replay of a private capture bundle; checks provenance, not field accuracy."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from bidops.api import create_app
from bidops.config import Settings
from bidops.parser import canonical_text
from bidops.storage import Store
from fastapi.testclient import TestClient


def verify(bundle: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="bidops-replay-") as temporary:
        root = Path(temporary)
        settings = Settings(
            data_dir=root / "data",
            database_url=f"sqlite:///{root / 'replay.sqlite3'}",
            network_enabled=False,
        )
        store = Store(settings)
        try:
            first = store.import_bundle(bundle)
            second = store.import_bundle(bundle)
            assert second == {"created": 0, "duplicates": first["created"]}
            rows = store.db.rows("SELECT notice_id FROM notices ORDER BY notice_id")
            samples = []
            span_count = 0
            for row in rows:
                notice = store.get_notice(row["notice_id"])
                source = notice.source
                raw = (store.raw / source.sha256).read_bytes()
                store.validate_snapshot(source, raw)
                text = canonical_text(raw, source.content_type, source.encoding)
                spans = [s for items in notice.field_evidence.values() for s in items]
                spans += notice.eligibility_conditions + [p.evidence for p in notice.packages]
                for span in spans:
                    assert span.source_snapshot_id == source.snapshot_id
                    assert span.locator == "normalized_text_v2"
                    assert text[span.start : span.end] == span.text
                span_count += len(spans)
                required = {
                    "project_code": notice.project_code,
                    "title": notice.title,
                    "buyer_name": notice.buyer_name,
                    "notice_kind": notice.notice_kind,
                    "budget": notice.budget,
                    "publication": notice.published_date,
                    "bid_deadline_at": notice.bid_deadline_at,
                    "eligibility_conditions": notice.eligibility_conditions,
                }
                samples.append(
                    {
                        "snapshot_id": source.snapshot_id,
                        "url": source.source_url,
                        "sha256": source.sha256,
                        "byte_size": source.byte_size,
                        "fetched_at": source.fetched_at.isoformat(),
                        "http_status": source.http_status,
                        "access_method": source.access_method,
                        "project_code": notice.project_code,
                        "budget_cny": notice.budget.amount if notice.budget else None,
                        "published_at": notice.published_at,
                        "bid_deadline_at": notice.bid_deadline_at,
                        "package_count": len(notice.packages),
                        "package_budgets_cny": [
                            p.budget.amount if p.budget else None for p in notice.packages
                        ],
                        "eligibility_excerpt_count": len(notice.eligibility_conditions),
                        "missing_required_fields": [k for k, v in required.items() if not v],
                        "abstain_reason": notice.abstain_reason,
                        "parser_version": notice.parser_version,
                    }
                )
            export1 = store.export(root / "export1", include_raw=True)
            export2 = store.export(root / "export2", include_raw=True)
            assert export1 == export2
            assert (root / "export1/manifest.json").read_bytes() == (
                root / "export2/manifest.json"
            ).read_bytes()
            with TestClient(create_app(settings)) as client:
                assert client.get("/health").json()["network_enabled"] is False
                assert client.get("/sources").status_code == 200
                listing = client.get("/notices").json()
                assert listing["total"] == len(samples)
                for sample in samples:
                    response = client.get(f"/notices/{sample['snapshot_id']}")
                    assert response.status_code == 200
                    assert response.json()["source"]["sha256"] == sample["sha256"]
                assert client.get("/notices/nonexistent").status_code == 404
            return {
                "verification_kind": "offline_provenance_and_replay_not_accuracy_evaluation",
                "first_import": first,
                "repeated_import": second,
                "evidence_spans_checked": span_count,
                "stable_export": export1,
                "input_manifest_sha256": hashlib.sha256(
                    (bundle / "manifest.json").read_bytes()
                ).hexdigest(),
                "api_checks": "passed",
                "samples": samples,
            }
        finally:
            store.db.engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.bundle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}, indent=2))
