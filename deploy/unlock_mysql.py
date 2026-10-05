"""Kill MySQL queries that have been running long enough to block the jobs API.

Run on the IIS server, from the repo root:

    python deploy/unlock_mysql.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from jobscraper.db import _connect, mysql_config  # noqa: E402


def main() -> int:
    cfg = mysql_config()
    if not cfg:
        print("MySQL is not configured in .env")
        return 1
    quick = dict(cfg)
    quick["connect_timeout"] = 5
    quick["read_timeout"] = 15
    quick["write_timeout"] = 15
    conn = _connect(quick)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT CONNECTION_ID() AS id")
            mine = int((cur.fetchone() or {}).get("id") or 0)
            cur.execute("SHOW FULL PROCESSLIST")
            rows = list(cur.fetchall() or [])
        print(f"connections={len(rows)} this_connection={mine}")
        killed = 0
        for row in rows:
            query_id = int(row.get("Id") or 0)
            seconds = int(row.get("Time") or 0)
            command = str(row.get("Command") or "")
            info = str(row.get("Info") or "").replace("\n", " ")
            print(f"  id={query_id} time={seconds}s cmd={command} state={row.get('State')} info={info[:180]}")
            if query_id == mine or command != "Query" or seconds < 20:
                continue
            with conn.cursor() as cur:
                cur.execute(f"KILL {query_id}")
            killed += 1
            print(f"  killed {query_id}")
        print(f"killed={killed}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
