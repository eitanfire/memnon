from __future__ import annotations

import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.public_static_server import create_public_static_server


def main():
    public_dir = REPO_ROOT / "public"
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5050"))
    api_origin = os.environ.get("MEMNON_API_ORIGIN", "https://api-4hth6oktaa-uc.a.run.app")
    server = create_public_static_server(
        host=host,
        port=port,
        public_dir=public_dir,
        api_origin=api_origin,
    )
    print(f"Serving Memnon static app at http://{host}:{port} (api proxy: {api_origin})")
    server.serve_forever()


if __name__ == "__main__":
    main()
