import argparse
import json
import logging
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from bidops.company import CompanyService
from bidops.company.retention import prune_company_materials
from bidops.config import Settings
from bidops.enterprise.backup import backup, restore
from bidops.enterprise.config import EnterpriseSettings
from bidops.enterprise.operations import SingleWorker
from bidops.enterprise.sessions import prune_private_history
from bidops.storage import Store


def main():
    parser = argparse.ArgumentParser(
        description="Operate the single-organization BidOps deployment"
    )
    parser.add_argument("--env-file", default="private/enterprise.env")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8765, type=int)
    sub.add_parser("check-config")
    sub.add_parser("prune")
    save = sub.add_parser("backup")
    save.add_argument("--output", type=Path, required=True)
    save.add_argument("--retention-days", type=int, choices=range(1, 31), default=7)
    recover = sub.add_parser("restore")
    recover.add_argument("--input", type=Path, required=True)
    recover.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    settings = Settings()
    if args.command == "backup":
        print(
            json.dumps(backup(settings, args.output.resolve(), retention_days=args.retention_days))
        )
    elif args.command == "restore":
        print(json.dumps(restore(args.input.resolve(), args.output.resolve())))
    else:
        enterprise = EnterpriseSettings()
        if not enterprise.enabled:
            parser.error(
                "Set BIDOPS_ENTERPRISE_ENABLED=true; this command never falls back to demo mode"
            )
        if args.command == "check-config":
            print(
                json.dumps(
                    {
                        "valid": True,
                        "deployment": enterprise.deployment,
                        "secure_cookies": enterprise.secure_cookies,
                    }
                )
            )
        elif args.command == "prune":
            worker = SingleWorker(settings.database_url)
            store = None
            try:
                store = Store(settings)
                print(
                    json.dumps(
                        {
                            "history": prune_private_history(store.db, enterprise.history_days),
                            "company": prune_company_materials(
                                CompanyService(store.db, settings.data_dir)
                            ),
                        }
                    )
                )
            finally:
                if store:
                    store.db.engine.dispose()
                worker.close()
        elif args.command == "serve":
            logging.basicConfig(level=logging.INFO, format="%(message)s")
            for name in ("httpx", "httpx2", "httpcore", "authlib"):
                logging.getLogger(name).setLevel(logging.WARNING)
            # OAuth callback query strings must not appear in generic access logs.
            uvicorn.run(
                "bidops.api:app", host=args.host, port=args.port, workers=1, access_log=False
            )


if __name__ == "__main__":
    main()
