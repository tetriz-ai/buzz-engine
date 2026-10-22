"""Unsent lanes retain their new IDs for a later run."""
import importlib.util
from pathlib import Path
import unittest
import json
import os
import subprocess
import tempfile
from unittest import mock

spec = importlib.util.spec_from_file_location(
    'delivery_state', Path(__file__).resolve().parents[1] / 'scripts/retain-delivered-state.py')
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


class TestDeliveredState(unittest.TestCase):
    def test_partial_delivery_does_not_consume_other_lane(self):
        self.assertEqual(state.retained_ids(['ph:old'], ['hn:new', 'reddit:new', 'ph:new'], {'hn'}),
                         ['hn:new', 'ph:old'])

    def test_no_delivery_preserves_only_existing_state(self):
        self.assertEqual(state.retained_ids(['hn:old'], ['reddit:new', 'ph:new'], set()), ['hn:old'])

    def test_invalid_state_rejected(self):
        with self.assertRaises(ValueError):
            state.retained_ids([], {'ph:new': True}, {'ph'})

    def test_main_retains_baseline_and_only_delivered_lanes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seen = root / 'buzz/state/seen.json'
            seen.parent.mkdir(parents=True)
            seen.write_text('["hn:old"]')
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            env = dict(os.environ, GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
                       GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid')
            subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'fixture'],
                           cwd=root, env=env, check=True)
            seen.write_text('["hn:old", "hn:new", "ph:new", "reddit:new"]')
            output = root / 'buzz/working'
            output.mkdir()
            (output / 'ph-digest-22-10-2026.pdf').write_bytes(b'%PDF-fixture')
            with mock.patch.object(state.Path, 'cwd', return_value=root):
                state.main()
            self.assertEqual(json.loads(seen.read_text()), ['hn:old', 'ph:new'])
