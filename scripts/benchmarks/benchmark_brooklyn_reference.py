from __future__ import annotations

import json
from pathlib import Path

from stormready_v3.reference import evaluate_brooklyn_reference


def main() -> None:
    evaluation = evaluate_brooklyn_reference()
    payload = evaluation.as_dict()
    output_path = Path(__file__).resolve().parents[2] / "reference_assets" / "brooklyn" / "benchmark_results.json"
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
