"""B01 offline scorer calibration entrypoint; no provider calls."""

from bidops.screening.offline_eval import main

if __name__ == "__main__":
    raise SystemExit(main())
