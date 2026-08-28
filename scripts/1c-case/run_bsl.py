"""Раннер .bsl скриптов кейса: читает файл и выполняет его в 1С через
1C MCP Toolkit POST /api/execute_code. Печатает JSON-результат.

Запуск:  python scripts/1c-case/run_bsl.py <файл.bsl>
"""

import json
import pathlib
import sys

import requests

API = "http://127.0.0.1:6003/api/execute_code"


def run(path: pathlib.Path) -> dict:
    code = path.read_text(encoding="utf-8")
    r = requests.post(API, json={"code": code}, timeout=300)
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    p = pathlib.Path(sys.argv[1])
    out = run(p)
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if out.get("success") else 2)
