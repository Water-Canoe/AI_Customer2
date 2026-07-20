from __future__ import annotations

import sys

from ai_customer_voxcpm_native import serve


if __name__ == "__main__":
    if "--serve" not in sys.argv:
        raise SystemExit("VoxCPM runtime must be started with --serve")
    serve()
