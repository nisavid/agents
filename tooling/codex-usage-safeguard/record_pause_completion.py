#!/usr/bin/env python3
"""Validate and retain private Foreman stop observations for the current pause."""
import argparse
import json
import sys

from configuration import load_config, runtime_arguments
from pause_completion import record_completion
from storage import read_private


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    runtime_arguments(parser)
    parser.add_argument('--account', choices=('main', 'daybreak'), required=True)
    parser.add_argument('--record', required=True)
    args = parser.parse_args()
    try:
        config = load_config(args.config, args.state_dir)
        result = record_completion(read_private(args.record), config, args.state_dir, args.account)
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
