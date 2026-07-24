#!/usr/bin/env python3
"""Emit a deterministic Git publication plan with explicit local effects.

Planning can fetch bounded objects and create target-local temporary refs. It
preserves FETCH_HEAD, removes its exact temporary refs, and may leave objects.
"""

import argparse
import json
import sys
from pathlib import Path

from git_publication.adapter import (
    MalformedRequest,
    load_request_json,
    plan_repository,
)


class MalformedInvocation(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise MalformedInvocation(message)


def error_document(code, error):
    return {"schema_version": 1, "error": {"code": code, "message": str(error)}}


def main(argv=None) -> int:
    parser = JsonArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        with args.request.open(encoding="utf-8") as handle:
            raw = load_request_json(handle)
        result = plan_repository(args.repo, raw)
    except MalformedInvocation as error:
        print(json.dumps(error_document("MALFORMED_INVOCATION", error)))
        return 2
    except (MalformedRequest, json.JSONDecodeError, OSError) as error:
        print(json.dumps(error_document("MALFORMED_REQUEST", error)))
        return 2
    except Exception as error:
        print(json.dumps(error_document("INTERNAL_FAILURE", error)))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
