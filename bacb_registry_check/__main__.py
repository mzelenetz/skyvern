"""Command line entrypoint for the BACB registry checker."""

from __future__ import annotations

import argparse
import asyncio

from pydantic import ValidationError

from bacb_registry_check.checker import BacbRegistryChecker
from bacb_registry_check.schemas import BacbCheckRequest
from bacb_registry_check.server import config_from_env, serve


def main() -> None:
    """Run either the HTTP server or a one-shot BACB registry check."""

    parser = argparse.ArgumentParser(description="BACB registry checker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="start the HTTP service")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=8765)

    check_parser = subparsers.add_parser("check", help="run one BACB registry check and print JSON")
    check_parser.add_argument("--rbt-number", dest="rbt_number")
    check_parser.add_argument("--state")
    check_parser.add_argument("--name")
    check_parser.add_argument("--credential", choices=["RBT", "BCaBA", "BCBA", "BCBA-D"], default="RBT")

    args = parser.parse_args()
    if args.command == "serve":
        serve(host=args.host, port=args.port)
        return

    try:
        request = BacbCheckRequest.model_validate(
            {
                "rbt_number": args.rbt_number,
                "state": args.state,
                "name": args.name,
                "credential": args.credential,
            }
        )
    except ValidationError as exc:
        parser.error(exc.json())
        return

    response = asyncio.run(BacbRegistryChecker(config_from_env()).check(request))
    print(response.model_dump_json(indent=2, exclude_none=True))


if __name__ == "__main__":
    main()
