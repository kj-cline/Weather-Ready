from __future__ import annotations

import json

from stormready_v3.config.runtime import runtime_configuration_dict


def main() -> None:
    print(json.dumps(runtime_configuration_dict(), indent=2))


if __name__ == "__main__":
    main()
