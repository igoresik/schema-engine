"""``python -m schema_engine.app`` opens the window; ``--serve`` runs only the API + built UI."""

import argparse

parser = argparse.ArgumentParser(prog="python -m schema_engine.app")
parser.add_argument("--serve", action="store_true", help="Run the API only, for a browser.")
parser.add_argument("--port", type=int, default=8765)
args = parser.parse_args()
if args.serve:
    import uvicorn

    from schema_engine.app.api import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)
else:
    from schema_engine.app.desktop import main

    main()
