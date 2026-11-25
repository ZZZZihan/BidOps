"""Local-only readiness alert receipts; no message is sent to an external recipient."""

from bidops.operations_acceptance.watchdog import main

if __name__ == "__main__":
    raise SystemExit(main())
