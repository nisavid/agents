#!/usr/bin/env python3
"""Record exact release evidence; the live guard verifies restored allowance."""
import argparse
import json
from pathlib import Path
from controller import ROOT, save
from safeguard import pause_requires_release

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--account', required=True, choices=['main', 'daybreak'])
parser.add_argument('--episode', required=True)
parser.add_argument('--evidence-reference', required=True)
parser.add_argument('--hold-released', action='store_true', help='Only after verifying actual release of any pause hold')
parser.add_argument('--state-dir', required=True)
args = parser.parse_args()
runtime = Path(args.state_dir)
episode = json.loads((runtime / f'{args.account}.json').read_text())['episode']
if episode['id'] != args.episode or not args.evidence_reference.strip():
    parser.error('Exact current episode and actual release/reset evidence required')
if pause_requires_release(episode) and not args.hold_released:
    parser.error('Foreman must first verify authorization to release the protective hold')
save(runtime / f'release-{args.account}.json', {'episodeId': args.episode,
     'accountId': episode['accountId'], 'evidenceReference': args.evidence_reference,
     'holdReleased': args.hold_released})
print('Release request recorded; no threads resumed and no reset redeemed.')
