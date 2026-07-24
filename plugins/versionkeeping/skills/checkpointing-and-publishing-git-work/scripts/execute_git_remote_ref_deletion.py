#!/usr/bin/env python3
"""Execute one reviewed exact-lease remote-ref deletion plan."""

import argparse
import json
import sys
from pathlib import Path

from git_publication.deletion import (
    MalformedDeletionPlan,
    ReviewedDeletionPlanDigestMismatch,
    execute_remote_ref_deletion,
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
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--reviewed-plan-sha256", required=True)
    try:
        args = parser.parse_args(argv)
        result = execute_remote_ref_deletion(
            args.repo, args.plan.read_bytes(), args.reviewed_plan_sha256
        )
    except MalformedInvocation as error:
        print(json.dumps(error_document("MALFORMED_INVOCATION", error)))
        return 2
    except ReviewedDeletionPlanDigestMismatch as error:
        print(json.dumps(error_document("REVIEWED_PLAN_DIGEST_MISMATCH", error)))
        return 2
    except (MalformedDeletionPlan, json.JSONDecodeError, OSError) as error:
        print(json.dumps(error_document("MALFORMED_DELETION_PLAN", error)))
        return 2
    except Exception as error:
        print(json.dumps(error_document("INTERNAL_FAILURE", error)))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
