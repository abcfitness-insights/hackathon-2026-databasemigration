"""Schema-facts client.

For the hackathon demo this loads facts from a local JSON snapshot — no live
DB connection, no risk of writes, fully deterministic for demos.

In production this should be replaced by a thin wrapper around the read-only
Synapse MCP tools that queries `sys.dm_db_partition_stats` and `sys.indexes`
for the row counts and index info.

The JSON format is simple:
{
    "app.customer": {"schema": "app", "name": "customer", "row_count": 12400000, "index_count": 3, "foreign_key_count": 2, "size_mb": 1820.5},
    ...
}
"""

from __future__ import annotations

import json
from pathlib import Path

from migguard.rules.base import TableFacts

DEFAULT_SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "schema_snapshot.json"
)


def load_schema_facts(path: Path | str | None = None) -> dict[str, TableFacts]:
    """Load schema facts from a JSON snapshot.

    If no snapshot file is provided or the file doesn't exist, returns an
    empty dict — the engine still runs, rules just won't have schema context.
    """
    target = Path(path) if path else DEFAULT_SNAPSHOT_PATH
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    out: dict[str, TableFacts] = {}
    for key, payload in raw.items():
        out[key.lower()] = TableFacts(
            schema=payload.get("schema", key.split(".", 1)[0]),
            name=payload.get("name", key.split(".", 1)[-1]),
            row_count=int(payload.get("row_count", 0)),
            index_count=int(payload.get("index_count", 0)),
            foreign_key_count=int(payload.get("foreign_key_count", 0)),
            size_mb=float(payload.get("size_mb", 0.0)),
        )
    return out
