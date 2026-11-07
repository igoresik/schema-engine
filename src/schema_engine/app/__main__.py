"""``python -m schema_engine.app --serve``: the API and built UI without the window, for development."""

import argparse

import uvicorn

from schema_engine.app.api import create_app

parser = argparse.ArgumentParser(prog="python -m schema_engine.app")
parser.add_argument("--serve", action="store_true", required=True, help="Run the API only.")
parser.add_argument("--port", type=int, default=8765)
args = parser.parse_args()
uvicorn.run(create_app(), host="127.0.0.1", port=args.port)
