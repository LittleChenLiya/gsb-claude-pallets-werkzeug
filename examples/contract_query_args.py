"""Read query parameters with an explicit per-key contract.

Run directly::

    python examples/contract_query_args.py
    python examples/contract_query_args.py "page=bad&tag=a&tag=b"

The matching small, local test command (no Docker needed)::

    python -m pytest tests/test_contract.py -q
"""

from __future__ import annotations

import sys
from urllib.parse import parse_qsl

from werkzeug.datastructures import ContractError
from werkzeug.datastructures import Field
from werkzeug.datastructures import MultiDict
from werkzeug.datastructures import read_contract


def parse(query_string: str) -> dict[str, object]:
    args = MultiDict(parse_qsl(query_string, keep_blank_values=True))

    # Three-parameter contract:
    # page: required single value, converted to int. Repeated "page" is an
    #       error rather than silently taking the first occurrence.
    # tag:  optional multi value, occurrences kept in order.
    # size: optional single value with a default, converted to int.
    return read_contract(
        args,
        [
            Field("page", converter=int),
            Field("tag", multi=True, required=False),
            Field("size", required=False, converter=int, default=10),
        ],
    )


def main(argv: list[str]) -> None:
    query_string = argv[1] if len(argv) > 1 else "page=2&tag=a&tag=b&size=20"
    print(f"query string: {query_string}")

    try:
        result = parse(query_string)
    except ContractError as e:
        for error in e.errors:
            print(
                f"  rejected: key={error.key!r} kind={error.kind.value}"
                f" occurrence={error.occurrence} value={error.value!r}"
            )
        print("no partial result is returned")
    else:
        print(f"  parsed: {result}")


if __name__ == "__main__":
    main(sys.argv)
