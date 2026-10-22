#!/usr/bin/env python3
"""Persist new seen IDs only for lanes whose validated PDF was delivered."""
import importlib.util
import json
from pathlib import Path
import subprocess


def retained_ids(previous, incoming, lanes):
    for values in (previous, incoming):
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError('Seen state must be a JSON array of string IDs')
    return sorted(set(previous) | {value for value in incoming if value.partition(':')[0] in lanes})


def main():
    root = Path.cwd().resolve()
    state = root / 'buzz/state/seen.json'
    if state.is_symlink() or not state.is_file() or state.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('Expected a regular seen-log no larger than 10 MiB')
    spec = importlib.util.spec_from_file_location('delivery', Path(__file__).with_name('deliver-digests.py'))
    delivery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delivery)
    lanes = set()
    for path in delivery.digest_files(root):
        lane = path.name.split('-digest-', 1)[0]
        lanes.update(('hn', 'reddit') if lane == 'buzz' else (lane,))
    baseline = subprocess.run(['git', 'show', 'HEAD:buzz/state/seen.json'],
                              capture_output=True, text=True, check=False, cwd=root)
    if baseline.returncode:
        tracked = subprocess.run(['git', 'ls-files', '--error-unmatch', 'buzz/state/seen.json'],
                                 capture_output=True, check=False, cwd=root)
        if tracked.returncode != 1:
            raise RuntimeError('Cannot read baseline seen-log')
        previous = []
    else:
        previous = json.loads(baseline.stdout)
    result = retained_ids(previous, json.loads(state.read_text()), lanes)
    state.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
