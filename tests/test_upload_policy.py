"""The allowed upload command cannot select credentials or another destination."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('uploader_policy', ROOT / 'scripts/buzz-slack-upload.py')
uploader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uploader)


class TestUploadPolicy(unittest.TestCase):
    def test_regular_report_allowed_but_escape_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'buzz/working'
            output.mkdir(parents=True)
            report = output / 'digest.pdf'
            report.write_bytes(b'synthetic report')
            secret = root / 'private.txt'
            secret.write_text('synthetic private data')
            (output / 'linked.pdf').symlink_to(secret)
            config = {'output_dir': 'buzz/working', 'slack': {'channel_id': 'C_ALLOWED'}}
            with mock.patch.object(uploader, 'REPO', directory):
                self.assertEqual(uploader.report_path(str(report), config), str(report.resolve()))
                for path in (secret, output / '../private.txt', output / 'linked.pdf'):
                    with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'output directory'):
                        uploader.report_path(str(path), config)

    def test_channel_cannot_override_configuration(self):
        config = {'slack': {'channel_id': 'C_ALLOWED'}}
        self.assertEqual(uploader.destination(None, config), 'C_ALLOWED')
        self.assertEqual(uploader.destination('C_ALLOWED', config), 'C_ALLOWED')
        for channel in ('C_OTHER', ''):
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                uploader.destination(channel, config)

    def test_text_file_is_checked_before_it_is_read(self):
        from types import SimpleNamespace
        args = SimpleNamespace(text='@/outside/private.txt', channel='C_ALLOWED', dry_run=False)
        with mock.patch.object(uploader, 'upload_config', return_value={}), \
             mock.patch.object(uploader, 'report_path', side_effect=ValueError('output directory')), \
             mock.patch.object(uploader, 'api') as network:
            with self.assertRaisesRegex(ValueError, 'output directory'):
                uploader.post_text(args)
            network.assert_not_called()
