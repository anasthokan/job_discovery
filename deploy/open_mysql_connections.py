"""Raise MySQL's connection limit so the jobs API can connect again.

Does not stop MySQL, the API, or any other process, and does not kill queries.

Run on the IIS server:

    python deploy/open_mysql_connections.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from jobscraper.db import _connect, mysql_config  # noqa: E402

TARGET = 300


def _value(row: dict | None) -> str:
    if not row:
        return "?"
    return str(row.get("Value") or row.get("value") or "?")


def main() -> int:
    cfg = mysql_config()
    if not cfg:
        print("MySQL is not configured in .env")
        return 1
    quick = dict(cfg)
    quick["connect_timeout"] = 5
    quick["read_timeout"] = 10
    quick["write_timeout"] = 10
    conn = _connect(quick)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW VARIABLES LIKE 'max_connections'")
            before = _value(cur.fetchone())
            cur.execute("SHOW STATUS LIKE 'Threads_connected'")
            threads = _value(cur.fetchone())
            print(f"max_connections={before} threads_connected={threads}")
            cur.execute(f"SET GLOBAL max_connections = {TARGET}")
            cur.execute("SHOW VARIABLES LIKE 'max_connections'")
            after = _value(cur.fetchone())
        print(f"max_connections={after}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
