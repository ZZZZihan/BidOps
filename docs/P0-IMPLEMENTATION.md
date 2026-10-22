# P0 implementation notes

This document supplements the frozen phase charter; it does not expand P0.

## Data flow and identity

`import-urls` registers references, and never fetches or creates notices. A lawfully held
HTML/text file can be imported with an explicit operator attestation. The optional HTTP
path requires all three: network enabled, `fetch --execute`, and reviewed source permission
with an exact URL allowlist. No redirects, login, CA files, CAPTCHA or attachment downloads.

Raw bytes are stored at `data/raw/<sha256>` (mode 0600). SHA256 covers the original bytes,
not normalized text. Snapshot ID is SHA256 of `canonical_url + newline + content_hash`.
Same URL + bytes is idempotent and retains the first observation metadata; different URLs
retain independent provenance but reuse one raw blob. Different bytes at the same URL
create a new snapshot and notice. Project codes are indexed for review, but are not
sufficient evidence to merge separate records or establish which correction is current.

SQLAlchemy manages file-backed SQLite transactions. Numbered SQL migrations live under
`backend/bidops/migrations/` and are tracked in `schema_migrations`. Each snapshot and
parsed notice is inserted in one transaction. Audit events record each ingest/fetch/CLI
attempt, including duplicate observations, independently of the immutable snapshot.
Unexpected disk failure can leave an unreferenced blob; corrupt existing bytes are
detected and never silently replaced. This is a single-operator local prototype.

## Evidence and conservative parsing

Parser `p0.2` uses UTF-8 or explicitly identified GB18030 (GBK/GB2312 HTTP aliases included).
Malformed encoding, empty data, oversized bodies and unexpected content types fail.
HTTP 200 is insufficient: challenge/error pages and bodies lacking recognizable notice
fields are rejected. This is structural verification, not a guarantee of legal permission
or field-level factual correctness. Manual attestation records the operator's assertion;
neither a file hash nor an imported manifest authenticates a government publisher.

`normalized_text_v2` removes script/style/template/noscript/head, scopes CCGP pages to
the notice container, preserves nested layout tables and block boundaries, and flattens
only data rows into pipe-separated cells. Inline fragments stay adjacent; horizontal
whitespace is collapsed. Evidence is
a Python Unicode-code-point interval `[start,end)` within exactly that text, with quoted
text and snapshot ID. It is **not** a raw-byte offset or a JavaScript UTF-16 index.
Canonical text is deterministically reconstructed from the raw bytes and lockfile.

Supported prototype layouts: explicit Chinese field labels; Chinese/ISO numeric dates
and common Chinese-numeral calendar dates; explicitly labeled CNY `元`/`万元` amounts;
line-oriented package headers (`第1包`, `包1`, `采购包1`) with labeled amounts, and
contiguous package tables with an explicit budget/unit column. `08点30分` and deadlines
directly under the standard submission section are supported. Repeated titles are
deduplicated; equal numeric budgets in different units/formatting are consistent.
Publication dates can supplement a same-day timestamp without inventing a clock time.
Date-only publication is stored as `published_date`; `published_at` remains null.
Date-only deadlines remain null. Exact `Decimal` arithmetic never rounds away sub-cent
precision or assumes the unit of an unlabelled number. Package sums are never used to
invent a total. Conflicting values cause abstention. SME conditions are quotations only,
not pass/fail eligibility decisions. Correction/termination/award records require review.

Not yet supported or validated: arbitrary package-table column layouts, PDF/DOCX/OCR,
client-side rendering, unlabeled fields, prose-only date ranges, amendment reconciliation,
cross-source project equivalence. All six seed bodies were captured and replayed on
2026-10-22; see the collection follow-up. Missing/unsupported data
must stay null with a reason; prototype coverage is not a measured real-data accuracy.

## Export and restoration

`export-manifest` emits canonical sorted JSON without run-specific generation timestamps;
repeat imports do not change snapshot metadata or manifest hashes. Optional local bundles
include `raw/<sha256>`. Restoration checks schema version, parser version, URL, ID, size,
and hash before changing the database. Metadata-only exports require separately available
raw blobs to restore. Manifests and bundles contain provenance, possibly private text,
and remain local. They are not public datasets and do not confer redistribution rights.
Version 1 manifests from parser p0.1 remain importable; imported bytes are reparsed with
p0.2 and receive v2 evidence offsets. Existing v1 notice evidence remains readable until
an explicit `parse` operation. Do not interpret v1 offsets using v2 normalization.

## API, UI and logs

FastAPI exposes four read-only routes. `/notices` returns bounded pagination; there is no
write route, authentication, open CORS policy, LLM or external action. Run on loopback.
The UI displays actual database records only, with an empty state, original-source links,
null states, package amounts, cited spans and a hash. It has no bundled notice data.
All displayed times use Asia/Shanghai; snapshots capture UTC observation time.

Structured event logs are in SQLite `audit_events`; command output is JSON. Local audit
evidence and execution logs are under `data/artifacts/`. No response bodies, cookies,
authorization headers or credential-bearing URLs enter the public report. URL imports
reject query strings, userinfo, fragments, unregistered hosts and ambiguous paths.

## P0 source review procedure

`scripts/audit_sources.py --execute-metadata-audit` performs only bounded public robots,
homepage and visible same-host terms-link reads, serially with at least five seconds
between requests. This explicit audit is separate from notice collection and cannot
automatically grant permission. A 404 robots file, redirect, network error or homepage
copyright footer does not become `allowed`. A reviewer must establish both robots/path
and terms permissions, record evidence and timestamp, then edit the registry before any
future enabled collection. No source is currently approved for that recurring path.

The separate, explicitly user-requested six-URL access check on 2026-10-22 observed
robots 404, homepage 200 with no visible terms link, and six notice-body 200 responses.
It used ordinary anonymous HTTPS, the same declared user agent, serial requests and
no retries or redirect following. No authentication, CA or attachment endpoint was used.
These captured responses are HTTP provenance, not manual-file attestations. The check
does not establish bulk/reuse permission or turn an unknown registry status into allowed.
