"""Offline regression checks for scanner identity and discussion-only PH details."""
import importlib.util
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRedditIdentity(unittest.TestCase):
    def setUp(self):
        self.scan = load('buzz-scan')

    def test_missing_identity_fails_before_network(self):
        with mock.patch.object(self.scan.urllib.request, 'urlopen') as network:
            with self.assertRaisesRegex(ValueError, 'reddit.user_agent'):
                self.scan._fetch_rss_raw('example', 'new')
            with self.assertRaisesRegex(ValueError, 'reddit.user_agent'):
                self.scan._fetch_search_rss_raw('example', 'week')
            network.assert_not_called()

    def test_configured_identity_is_used_for_listing_and_search(self):
        identity = 'linux:my-reader:1.0 (by /u/example)'
        cfg = {'reddit': {'user_agent': identity}}
        response = mock.Mock()
        response.read.return_value = b'<?xml version="1.0"?><feed/>'
        response.headers = {'Content-Type': 'application/atom+xml'}
        with mock.patch.object(self.scan.urllib.request, 'urlopen', return_value=response) as network:
            self.scan._fetch_rss_raw('example', 'new', self.scan._rf_cfg(cfg))
            self.scan._fetch_search_rss_raw('example', 'week', identity)
        self.assertEqual([call.args[0].get_header('User-agent') for call in network.call_args_list],
                         [identity, identity])

    def test_access_denial_is_not_retried_on_another_host(self):
        identity = 'linux:my-reader:1.0 (by /u/example)'
        denied = urllib.error.HTTPError('https://www.reddit.com', 403, 'Forbidden', {}, None)
        with mock.patch.object(self.scan.urllib.request, 'urlopen', side_effect=denied) as network:
            with self.assertRaises(urllib.error.HTTPError):
                self.scan._fetch_rss_raw('example', 'new', self.scan._rf_cfg(
                    {'reddit': {'user_agent': identity}}))
            self.assertEqual(network.call_count, 1)
            network.reset_mock()
            with self.assertRaises(urllib.error.HTTPError):
                self.scan._fetch_search_rss_raw('example', 'week', identity)
            self.assertEqual(network.call_count, 1)

    def test_placeholder_identity_rejected(self):
        with self.assertRaisesRegex(ValueError, 'reddit.user_agent'):
            self.scan._rf_cfg({'reddit': {'user_agent': 'FILL_IN_REDDIT_USER_AGENT'}})


class TestPhDiscussion(unittest.TestCase):
    def test_details_fetch_only_anonymous_discussion(self):
        scan = load('buzz-ph-scan')
        candidate = {'ph_id': '1', 'name': 'Example', 'makers': [], 'discussion': []}
        data = {'post': {'votes': {'edges': [{'node': {'user': {'username': 'voter'}}}]},
                         'comments': {'edges': [{'node': {'body': 'A useful question',
                                                         'votesCount': 2,
                                                         'user': {'username': 'commenter'}}}]}}}
        cfg = {'ph': {'detail_fetch_top': 1, 'comments_limit': 10}}
        with mock.patch.object(scan, 'gql', return_value=data) as query, mock.patch.object(scan.time, 'sleep'):
            scan.fetch_details(cfg, 'not-a-real-token', [candidate])
        self.assertNotIn('votes(', scan.DETAIL_QUERY)
        self.assertNotIn('user {', scan.DETAIL_QUERY)
        self.assertNotIn('voters', query.call_args.args[2])
        self.assertEqual(candidate['discussion'], [{'body': 'A useful question', 'votes': 2}])
        self.assertNotIn('top_voters', candidate)
        self.assertNotIn('top_commenters', candidate)

class TestRedditCoverage(unittest.TestCase):
    def test_blocked_feeds_are_reported_as_missing(self):
        scan = load('buzz-scan')
        cfg = {'reddit': {'user_agent': 'test:reader:1.0 (contact: example)',
                          'fetch': {'deferred_sweep': False, 'pace_s': 0, 'pace_jitter_s': 0}},
               'subreddits': [{'name': 'example', 'tier': 1, 'barrier': 'low'}],
               'keywords': {'precise': ['test']}}
        denied = urllib.error.HTTPError('https://www.reddit.com', 403, 'Forbidden', {}, None)
        with mock.patch.object(scan, 'reddit_rss_entries', side_effect=denied), \
             mock.patch.object(scan.time, 'sleep'):
            _, diagnostics = scan.scan_reddit(cfg, 0)
        self.assertEqual(diagnostics['feeds_ok'], 0)
        self.assertEqual(diagnostics['subs_missing'], ['r/example'])

class TestRedditRateLimit(unittest.TestCase):
    def setUp(self):
        self.scan = load('buzz-scan')
        self.now = 1000.0
        self.requests = []

    def sleep(self, seconds):
        self.now += seconds

    def response(self):
        response = mock.Mock()
        response.headers = {'Content-Type': 'application/atom+xml'}
        response.read.return_value = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>'
        return response

    def limited(self, delay):
        return urllib.error.HTTPError('https://www.reddit.com', 429, 'Rate limited',
                                      {'Retry-After': delay}, None)

    def cfg(self, **fetch):
        return {'reddit': {'user_agent': 'test:reader:1.0',
                           'fetch': dict({'attempts': 1, 'pace_s': 1, 'pace_jitter_s': 0}, **fetch)},
                'subreddits': [{'name': 'example', 'tier': 1, 'barrier': 'low'}],
                'keywords': {'precise': ['test']}}

    def run_with_clock(self, responses, fn):
        sequence = iter(responses)
        def request(*args, **kwargs):
            self.requests.append(self.now)
            result = next(sequence)
            if isinstance(result, Exception):
                raise result
            return result
        with mock.patch.object(self.scan.time, 'monotonic', side_effect=lambda: self.now), \
             mock.patch.object(self.scan.time, 'time', side_effect=lambda: self.now), \
             mock.patch.object(self.scan.time, 'sleep', side_effect=self.sleep), \
             mock.patch.object(self.scan.urllib.request, 'urlopen', side_effect=request):
            return fn()

    def test_search_delay_above_limit_aborts_instead_of_shortening(self):
        rf = self.scan._rf_cfg(self.cfg(attempts=2, max_retry_after_s=25))
        with self.assertRaises(self.scan._Throttled):
            self.run_with_clock([self.limited('120')],
                lambda: self.scan._fetch_search_rss_raw('example', 'week', rf=rf))
        self.assertEqual(self.requests, [1000])
        self.assertEqual(self.now, 1000)

    def test_shared_deadline_across_feeds_and_deferred_sweep(self):
        cfg = self.cfg(deferred_sweep_wait_s=2)
        _, diagnostics = self.run_with_clock(
            [self.limited('10'), self.limited('20'), self.response(), self.response()],
            lambda: self.scan.scan_reddit(cfg, 0))
        self.assertEqual(self.requests, [1000, 1010, 1030, 1031])
        self.assertEqual(diagnostics['feeds_recovered_by_sweep'], 2)

    def test_http_date_is_honored(self):
        from email.utils import formatdate
        rf = self.scan._rf_cfg(self.cfg(attempts=2))
        self.run_with_clock([self.limited(formatdate(1030, usegmt=True)), self.response()],
            lambda: self.scan._fetch_search_rss_raw('example', 'week', rf=rf))
        self.assertEqual(self.requests, [1000, 1030])

    def test_long_server_delay_stops_all_remaining_feeds_and_sweep(self):
        cfg = self.cfg(max_retry_after_s=120)
        _, diagnostics = self.run_with_clock([self.limited('600')],
            lambda: self.scan.scan_reddit(cfg, 0))
        self.assertEqual(self.requests, [1000])
        self.assertEqual(diagnostics['feeds_throttled'], 2)
        self.assertEqual(diagnostics['feeds_recovered_by_sweep'], 0)

    def test_total_wait_budget_stops_instead_of_requesting_early(self):
        cfg = self.cfg(max_total_wait_s=15)
        _, diagnostics = self.run_with_clock([self.limited('10'), self.limited('10')],
            lambda: self.scan.scan_reddit(cfg, 0))
        self.assertEqual(self.requests, [1000, 1010])
        self.assertEqual(diagnostics['feeds_throttled'], 2)
        self.assertEqual(diagnostics['feeds_recovered_by_sweep'], 0)

    def test_retry_after_is_respected_even_when_legacy_toggle_is_false(self):
        rf = self.scan._rf_cfg(self.cfg(attempts=2, respect_retry_after=False))
        self.run_with_clock([self.limited('10'), self.response()],
            lambda: self.scan._fetch_search_rss_raw('example', 'week', rf=rf))
        self.assertEqual(self.requests, [1000, 1010])

    def test_deferred_wait_budget_cannot_force_an_early_request(self):
        cfg = self.cfg(max_total_wait_s=15, deferred_sweep_wait_s=45)
        _, diagnostics = self.run_with_clock([self.limited('10'), self.response()],
            lambda: self.scan.scan_reddit(cfg, 0))
        self.assertEqual(self.requests, [1000, 1010])
        self.assertEqual(diagnostics['feeds_throttled'], 1)
        self.assertEqual(diagnostics['feeds_ok'], 1)
        self.assertIn('budget', diagnostics['rate_limit_stop_reason'])



if __name__ == '__main__':
    unittest.main()
