import argparse
import json
from pathlib import Path
from urllib import request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("eml_path")
    parser.add_argument("--url", default="http://127.0.0.1:8081/forwarded-email")
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    raw_email = Path(args.eml_path).read_text(encoding="utf-8")
    payload = json.dumps({"token": args.token, "raw_email": raw_email}).encode()
    req = request.Request(args.url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req) as response:
        print(response.read().decode())


if __name__ == "__main__":
    main()
