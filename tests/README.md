# Engineering tests, not procurement samples

Test inputs are self-authored, minimal syntax fragments and HTTP protocol doubles.
`UNIT-*` identifiers and test text exist only in temporary test databases. They are not
government announcements, lawful real-source fixtures, gold annotations, demo data, or
evidence that any seed URL was collected. No fixtures are loaded by the app.

Tests cover money, date precision/timezone, missing and conflicting fields, quotes and
offsets, packages, version preservation, byte-level and URL dedupe, manifests, integrity
failures, API/CLI behavior, permission gates, redirects, HTTP errors and bounded retries.
Real fixture coverage is currently zero; that acceptance gap is tracked in Issue #6.
