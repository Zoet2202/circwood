"""SSB API ingestion (JSON-stat).

Only used by the *fresh* pipeline (``use_cached_ssb=False``). Requires the
optional ``ssb`` extras (``pyjstat`` + ``requests``) and network access. The
cached pipeline never imports this module.
"""

from __future__ import annotations

import json
import os

import pandas as pd


def process_query_files(directory_query_path: str) -> dict[str, pd.DataFrame]:
    """POST each query JSON in a directory to SSB and parse the JSON-stat reply.

    Each ``*.json`` file must contain ``postUrl``, ``tableIdForQuery`` and
    ``queryObj``. Returns ``{table_id: DataFrame}``.
    """
    import requests
    from pyjstat import pyjstat

    dataset_output: dict[str, pd.DataFrame] = {}

    for filename in sorted(os.listdir(directory_query_path)):
        if not filename.endswith(".json"):
            continue
        file_path = os.path.join(directory_query_path, filename)
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)

            result = requests.post(
                data["postUrl"], json=data["queryObj"], timeout=60
            )
            result.raise_for_status()

            dataset = pyjstat.from_json_stat(json.loads(result.text))
            dataset_output[data["tableIdForQuery"]] = dataset
            print(f"Processed {filename} (table {data['tableIdForQuery']})")
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"Error processing {filename}: {exc}")

    return dataset_output
