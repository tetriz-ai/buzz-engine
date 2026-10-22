"""Untrusted maker links are displayed for review, never fetched automatically."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('ph_fetch_policy', SCRIPTS / 'buzz-ph-scan.py')
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


class TestMakerFetchPolicy(unittest.TestCase):
    def test_untrusted_maker_urls_never_trigger_network(self):
        for website in ('file:///synthetic/private.html', 'http://127.0.0.1/private',
                        'https://example.org/redirect'):
            maker = {'name': 'Example', 'website': website,
                     'ph_profile': 'https://www.producthunt.com/@example',
                     'twitter': 'example'}
            with self.subTest(website=website), \
                 mock.patch.object(scan.urllib.request, 'urlopen') as network:
                scan.fetch_maker_socials([{'makers': [maker]}], 10)
                network.assert_not_called()
                self.assertEqual(maker['socials']['twitter'], 'https://x.com/example')
                self.assertEqual(maker['socials']['twitter_source'], 'ph_api')
                self.assertIsNone(maker['socials']['linkedin'])

    def test_invalid_api_handle_does_not_become_a_profile_url(self):
        for handle in ('../private', 'example?secret=x', 'example/other'):
            maker = {'name': 'Example', 'twitter': handle}
            scan.fetch_maker_socials([{'makers': [maker]}], 10)
            self.assertIsNone(maker['socials']['twitter'])
