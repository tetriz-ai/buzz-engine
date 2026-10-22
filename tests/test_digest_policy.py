"""Policy regression tests for human research briefings."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('digest_policy', ROOT / 'scripts/buzz-digest-pdf.py')
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)


class TestDigestPolicy(unittest.TestCase):
    def item(self, platform):
        return {'cand': {'platform': platform, 'title': 'A useful thread',
                         'url': 'https://example.org/thread',
                         'top_voters': [{'name': 'Legacy voter identity'}]},
                'draft': {'comment_draft': 'We hit this at forty engineers.',
                          'comment_label': 'ready to post'}, 'makers': []}

    def test_hn_draft_is_omitted_in_single_and_mixed_digests(self):
        for channel in ('hn', 'buzz'):
            with self.subTest(channel=channel):
                item = self.item('hn')
                item['draft']['research_notes'] = 'The thread discusses <script>limits</script>.'
                result = digest.render_html({}, '2026-10-22', [item], [], [], [], channel)
                self.assertIn('The thread discusses &lt;script&gt;limits&lt;/script&gt;.', result)
                self.assertNotIn('<script>limits</script>', result)
                self.assertNotIn('We hit this at forty engineers.', result)
                self.assertIn('research only', result.lower())
                self.assertIn('AI-edited', result)

    def test_other_platform_copy_is_guidance(self):
        for platform in ('reddit', 'ph'):
            with self.subTest(platform=platform):
                result = digest.render_html({}, '2026-10-22', [self.item(platform)], [], [], [], platform)
                self.assertIn('guidance', result.lower())
                self.assertIn('own original content', result)
                self.assertIn('Disclose', result)
                self.assertNotIn('ready to post', result)
                self.assertNotIn('Paste the comment', result)
                self.assertNotIn('Legacy voter identity', result)

    def test_legacy_upvote_action_is_filtered_signal(self):
        candidate = {'url': 'https://example.org/item', 'title': 'Useful'}
        carded, signals, skipped, missing = digest.merge(
            [candidate],
            [{'ph_link': 'https://example.org/item', 'action': 'upvote'}])
        self.assertEqual(len(signals), 1)
        result = digest.render_upvote(signals)
        self.assertIn('Filtered signals', result)
        self.assertIn('independently', result)
        self.assertNotIn('Upvote targets', result)

    def test_browser_sandbox_enabled(self):
        source = (ROOT / 'scripts/buzz-digest-pdf.py').read_text()
        self.assertFalse('--no-sandbox' in source, 'Chrome must retain its sandbox')
        self.assertIn('chromium_sandbox=True', source)
