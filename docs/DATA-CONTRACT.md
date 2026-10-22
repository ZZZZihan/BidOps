# Canonical data contract v0.1

Every field not supported by captured evidence must be `null` plus a reason. **Never hallucinate.** `source_snapshot_id` identifies immutable raw content stored locally; `sha256` identifies bytes. Use JSON Schema / Pydantic validation in implementation.

```json
{
  "notice_id": "ccgp:TGPC-2026-A-0384:2026-10-16:original",
  "project_code": "TGPC-2026-A-0384",
  "title": "天津市生态环境监测中心环境应急及污染源执法监测仪器设备采购项目",
  "buyer_name": "天津市生态环境监测中心",
  "notice_kind": "tender_announcement",
  "location": "天津市",
  "budget": {"amount": "1120000.00", "currency": "CNY", "basis": "total"},
  "published_at": "2026-10-16T18:51:00+08:00",
  "bid_deadline_at": "2026-11-06T08:30:00+08:00",
  "observed_at": null,
  "status_as_observed": "unknown",
  "packages": [],
  "eligibility_conditions": [],
  "source": {
    "url": "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261016_27510682.htm",
    "snapshot_id": null,
    "sha256": null,
    "access_method": "not_yet_fetched",
    "source_completeness": "notice_only"
  },
  "field_evidence": {},
  "parse_warnings": ["Example of desired shape, NOT a verified parser output"]
}
```

**Example above is a specification sample, NOT an experimental result.** Do not commit it as a gold annotation unless verified against the actual notice text/snapshot, including Chinese yuan unit conversion and timezone.

### Must capture

- `source_url`, `fetched_at` ISO timestamp, HTTP status, content type, SHA256, byte size, access method and last-modified/etag when present.
- Normalized notice identity (project code + notice type + effective date), title, purchaser, raw budget string and precise normalized money, three distinct timestamps (publication, file-acquisition cutoff, bid cutoff), applicable SME/qualification rules (with original spans), source completeness, status state and observation time.
- `parse_warnings` and `abstain_reason` for unresolved data.

### Storage separation

`data/seeds/` can be public URLs. `data/raw/`, `data/processed/`, `data/manual/` stay local and gitignored. A report or curated metadata can be published only after licensing/privacy review. All automated decisions must point back to evidence snapshot+span.
