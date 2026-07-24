#!/usr/bin/env python3
"""Emit a separate exact-lease remote-ref deletion plan."""

import argparse
import json
import sys
from pathlib import Path

from git_publication.adapter import MalformedRequest, load_request_json
from git_publication.deletion import plan_remote_ref_deletion


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
        result = plan_remote_ref_deletion(args.repo, raw)
    except MalformedInvocation as error:
        print(json.dumps(error_document("MALFORMED_INVOCATION", error)))
        return 2
    except (MalformedRequest, json.JSONDecodeError, OSError) as error:
        print(json.dumps(error_document("MALFORMED_DELETION_REQUEST", error)))
        return 2
    except Exception as error:
        print(json.dumps(error_document("INTERNAL_FAILURE", error)))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
