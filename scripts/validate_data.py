#!/usr/bin/env python3
"""Validate generated graph files before publishing a refresh. Standard library only."""
import argparse
import json
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = tuple(f'{name}_fights.json' for name in ('flw', 'bw', 'fw', 'lw', 'ww', 'mw', 'lhw', 'hw', 'wsw', 'wflw', 'wbw', 'wfw'))


def validate_payload(data, previous=None):
    meta, nodes, links = data['meta'], data['nodes'], data['links']
    if not nodes or not links:
        raise ValueError('Empty division: refusing to publish an incomplete archive')
    ids = [node['id'] for node in nodes]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate fighter IDs')
    counts, opponents, pairs = Counter(), defaultdict(set), set()
    for link in links:
        a, b, count = link['source'], link['target'], link['value']
        if a not in ids or b not in ids or a == b:
            raise ValueError('Invalid matchup endpoint')
        pair = tuple(sorted((a, b)))
        if pair in pairs:
            raise ValueError('Duplicate matchup')
        pairs.add(pair)
        if type(count) is not int or count < 1 or len(link['bouts']) != count:
            raise ValueError('Invalid bout count')
        counts.update({a: count, b: count})
        opponents[a].add(b)
        opponents[b].add(a)
    for node in nodes:
        if node['fightCount'] != counts[node['id']] or node['uniqueOpponentCount'] != len(opponents[node['id']]):
            raise ValueError('Fighter totals disagree with matchups')
    total = sum(link['value'] for link in links)
    if (meta['fighter_count'], meta['matchup_count'], meta['bout_count']) != (len(nodes), len(links), total):
        raise ValueError('Metadata totals disagree with graph')
    snapshot_date = date.fromisoformat(meta['latest_resolved_event_date'])
    if snapshot_date > date.today():
        raise ValueError('Completed-event date is in the future')
    if previous:
        old_meta = previous['meta']
        if snapshot_date < date.fromisoformat(old_meta['latest_resolved_event_date']):
            raise ValueError('Coverage date moved backwards')
        if total < old_meta['bout_count'] * 0.9:
            raise ValueError('More than 10% of historical bouts disappeared; review the source response')
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare-ref', help='Reject suspicious losses relative to this Git revision')
    args = parser.parse_args()
    total = 0
    for filename in FILES:
        try:
            payload = json.loads((ROOT / 'docs' / 'data' / filename).read_text())
            baseline = None
            if args.compare_ref:
                baseline = json.loads(subprocess.check_output(
                    ['git', 'show', f'{args.compare_ref}:docs/data/{filename}'], cwd=ROOT, text=True
                ))
            total += validate_payload(payload, baseline)
        except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
            raise SystemExit(f'{filename}: {exc}') from exc
    print(f'Validated {len(FILES)} divisions and {total:,} bouts.')


if __name__ == '__main__':
    main()
