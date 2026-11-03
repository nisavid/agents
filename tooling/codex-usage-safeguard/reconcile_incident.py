#!/usr/bin/env python3
"""Read-only native reconciliation of one retained incident; never send or retry."""
import argparse
import json
import os
from pathlib import Path

from configuration import load_config, runtime_arguments
from controller import validate_config
from incident_delivery import reconcile_from_files
from native_bridge import NativeBridge
from transport import socket_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    runtime_arguments(parser)
    parser.add_argument('--incident', required=True)
    args = parser.parse_args()
    config = load_config(args.config, args.state_dir)
    validate_config(config, activate=False)
    if config.get('incidentRoutingEnabled') is not True:
        raise ValueError('incident_routing_not_configured')
    pipe = os.environ.get('CODEX_APP_TOOLS_PIPE_PATH')
    socket_identity(pipe)
    bridge = NativeBridge(config, pipe)
    try:
        result = reconcile_from_files(Path(args.state_dir), config, args.incident, bridge)
    finally:
        bridge.close()
    print(json.dumps({'incident': args.incident, 'result': result}))


if __name__ == '__main__':
    main()
