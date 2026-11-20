"""`python -m app.robot`: check that the robot browser works on this computer."""

from __future__ import annotations

import os
import sys

from app.robot import self_check


def main() -> int:
    result = self_check(os.environ.get("NEWSWIRE_BROWSER_PATH") or None)
    print(result["message"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
