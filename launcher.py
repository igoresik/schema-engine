"""Entry point of the packaged desktop app. With --sandbox it is the child that runs a transform."""

import sys
from pathlib import Path

if __name__ == "__main__":
    if sys.argv[1:2] == ["--sandbox"]:
        from schema_engine._runner import main as run_child

        sys.exit(run_child(Path(sys.argv[2])))
    from schema_engine.app.desktop import main

    main()
