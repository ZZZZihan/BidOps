import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Run the local BidOps Agent Demo")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run("bidops.api:app", host="127.0.0.1", port=args.port)
