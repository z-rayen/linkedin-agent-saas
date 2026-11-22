"""Writes the API's OpenAPI document. Three uses:

- no argument: print it to stdout;
- `--write PATH`: write it to PATH (LF line endings, the same bytes on every platform);
- `--check PATH`: exit 1 when PATH does not hold the same document. CI runs this, so a route change without a refreshed
  frontend/openapi.json fails the build.

Run from backend/: `.venv/bin/python scripts/dump_openapi.py --write ../frontend/openapi.json`.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from linkedin_agent.main import app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", type=Path)
    group.add_argument("--check", type=Path)
    args = parser.parse_args()
    document = app.openapi()
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    if args.write is not None:
        args.write.write_bytes(text.encode("utf-8"))
        return 0
    if args.check is not None:
        committed = json.loads(args.check.read_text(encoding="utf-8"))
        if committed != document:
            print(
                f"{args.check} is out of date with the API: run "
                "`python scripts/dump_openapi.py --write ../frontend/openapi.json` and commit it.",
                file=sys.stderr,
            )
            return 1
        print("openapi snapshot is up to date")
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
