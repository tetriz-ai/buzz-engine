"""Offline guardrails for the scheduled runner's credential boundary."""
import importlib.util
import pathlib
import re
import subprocess
import tempfile
import unittest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestWorkflowPolicy(unittest.TestCase):
    def test_agent_has_no_write_credentials_or_broad_shell(self):
        workflow = yaml.safe_load((ROOT / 'templates/github-action.yml').read_text())
        scan = workflow['jobs']['scan']
        self.assertEqual(scan['permissions'], {'contents': 'read'})
        run = next(step for step in scan['steps'] if step.get('name') == 'Run /buzz-daily')
        self.assertNotIn('GH_TOKEN', run.get('env', {}))
        self.assertNotIn('SLACK_BOT_TOKEN', run.get('env', {}))
        self.assertNotIn('buzz-slack-upload.py', run['run'])
        self.assertIn('/buzz-daily --no-post', run['run'])
        allowed = run['run'].split('--allowedTools', 1)[1]
        self.assertNotIn('Read,', allowed)
        self.assertNotIn('Glob,', allowed)
        self.assertNotIn('Grep,', allowed)
        self.assertIn('Read(buzz/config.yaml)', allowed)
        self.assertNotIn('WebFetch', run['run'])
        self.assertNotIn('--allowedTools "Bash,', run['run'])
        self.assertIn('--tools', run['run'])
        for step in scan['steps']:
            if step.get('uses', '').startswith('actions/checkout@'):
                self.assertFalse(step['with']['persist-credentials'])
        self.assertNotIn('git config --global', (ROOT / 'templates/github-action.yml').read_text())

    def test_state_write_job_contains_no_agent(self):
        workflow = yaml.safe_load((ROOT / 'templates/github-action.yml').read_text())
        save = workflow['jobs']['save-state']
        self.assertEqual(save['needs'], ['scan', 'deliver'])
        self.assertIn("needs.deliver.result == 'success'", save['if'])
        self.assertNotIn("needs.deliver.result == 'skipped'", save['if'])
        self.assertIn("retain-delivered-state.py", str(save))
        token_steps = [s for s in save['steps'] if 'GH_TOKEN' in s.get('env', {})]
        self.assertEqual(len(token_steps), 1)
        self.assertIn('save-state.sh', token_steps[0]['run'])
        self.assertNotIn('claude -p', str(save))

    def test_dependencies_and_lint_are_pinned(self):
        ci = (ROOT / '.github/workflows/ci.yml').read_text()
        self.assertIn('ruff check --select E4,E7,E9,F', ci)
        self.assertNotIn('pip install pyyaml', ci)
        self.assertNotRegex(ci, r'@anthropic-ai/claude-code\s*$')
        for filename in ('requirements.txt', 'requirements-dev.txt'):
            for line in (ROOT / filename).read_text().splitlines():
                if line and not line.startswith(('#', '-r')):
                    self.assertIn('==', line)

    def test_state_writer_rejects_invalid_data_before_git(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory) / 'buzz/state'
            state.mkdir(parents=True)
            (state / 'seen.json').write_text('not JSON')
            result = subprocess.run(
                ['bash', str(ROOT / 'scripts/save-state.sh')], cwd=directory,
                capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('JSONDecodeError', result.stderr)
            self.assertNotIn('not a git repository', result.stderr)

    def test_delivery_has_fresh_checkout_and_no_agent(self):
        workflow = yaml.safe_load((ROOT / 'templates/github-action.yml').read_text())
        delivery = workflow['jobs']['deliver']
        self.assertEqual(delivery['needs'], 'scan')
        self.assertEqual(delivery['permissions'], {'contents': 'read'})
        token_steps = [s for s in delivery['steps'] if 'SLACK_BOT_TOKEN' in s.get('env', {})]
        self.assertEqual(len(token_steps), 1)
        self.assertIn('deliver-digests.py', token_steps[0]['run'])
        self.assertNotIn('claude -p', str(delivery))
        for step in delivery['steps']:
            if step.get('uses', '').startswith('actions/checkout@'):
                self.assertFalse(step['with']['persist-credentials'])


class TestDigestDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            'delivery', ROOT / 'scripts/deliver-digests.py')
        cls.delivery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.delivery)

    def test_only_regular_pdf_data_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = root / 'buzz/working'
            output.mkdir(parents=True)
            pdf = output / 'hn-digest-22-10-2026.pdf'
            pdf.write_bytes(b'%PDF-1.7\nfixture')
            self.assertEqual(self.delivery.digest_files(root), [pdf])
            payload = output / 'instructions.py'
            payload.write_text('raise SystemExit("must never execute")')
            with self.assertRaisesRegex(ValueError, 'filename'):
                self.delivery.digest_files(root)
            payload.unlink()
            pdf.write_bytes(b'not a PDF')
            with self.assertRaisesRegex(ValueError, 'PDF header'):
                self.delivery.digest_files(root)

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = root / 'buzz/working'
            output.mkdir(parents=True)
            outside = root / 'private.pdf'
            outside.write_bytes(b'%PDF-secret')
            (output / 'hn-digest-22-10-2026.pdf').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'regular file'):
                self.delivery.digest_files(root)

    def test_pdf_count_is_bounded_before_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            output = root / 'buzz/working'
            output.mkdir(parents=True)
            for day in range(1, 6):
                (output / ('hn-digest-%02d-10-2026.pdf' % day)).write_bytes(b'%PDF-1.7')
            with self.assertRaisesRegex(ValueError, 'four'):
                self.delivery.digest_files(root)

class TestSchedulingInstructions(unittest.TestCase):
    def test_source_reading_commands_do_not_prescribe_github_writes(self):
        for name in ('buzz.md', 'buzz-daily.md'):
            with self.subTest(command=name):
                command = (ROOT / 'commands' / name).read_text()
                self.assertIsNone(re.search(r'\b(?:gh pr (?:create|merge)|git push)\b', command), name)
                self.assertIn('State persistence belongs to the trusted non-model workflow', command)

    def test_retired_routine_is_rejected_before_setup(self):
        command = (ROOT / 'commands/buzz-init.md').read_text()
        retired = command.index('## Retired scheduling options')
        self.assertLess(retired, command.index('## Step 1:'))
        self.assertIn('stop before writing any files', command[retired:])
        self.assertIn('`--state=pr` or `--state=push`', command[retired:])
        self.assertNotIn('`state.commit: pr`', command)
