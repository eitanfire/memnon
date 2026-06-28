from __future__ import annotations

import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.workflows_static_server import create_workflows_static_server


def main():
    public_dir = REPO_ROOT / "public"
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = create_workflows_static_server(host=host, port=port, public_dir=public_dir)
    print(f"Serving workflows static app at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
