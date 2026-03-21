from __future__ import annotations

from pathlib import Path

from vpsdash.config import load_config
from vpsdash.host_agent_daemon import create_host_agent_app


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    config = load_config(root)
    app = create_host_agent_app(root)
    app.run(host=config.host_agent_bind_host, port=config.host_agent_port)
