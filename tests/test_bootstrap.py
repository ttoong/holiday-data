"""실제 계정/키/HTTP 없이 게시 범위와 재실행의 덮어쓰기 방지를 검사합니다."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import io
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bootstrap', ROOT / 'tools/bootstrap.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.site = Path(self.temp.name) / 'site'
        shutil.copytree(ROOT / 'site', self.site)
        self.files = b.validate_site(self.site)

    def tearDown(self):
        self.temp.cleanup()

    def metadata(self, deployment):
        return json.dumps({'success': True, 'result': {
            'name': b.PROJECT, 'subdomain': b.PROJECT + '.pages.dev',
            'latest_deployment': deployment}}).encode()

    def test_private_extra_file_is_rejected(self):
        (self.site / '.env').write_text('FAKE_ONLY')
        with self.assertRaises(b.CheckFailed): b.validate_site(self.site)

    def test_link_is_rejected(self):
        (self.site / 'extra').symlink_to(self.site / 'index.html')
        with self.assertRaises(b.CheckFailed): b.validate_site(self.site)

    def test_missing_404_is_rejected(self):
        (self.site / '404.html').unlink()
        with self.assertRaises(b.CheckFailed): b.validate_site(self.site)

    def test_false_ready_status_is_rejected(self):
        (self.site / 'status.json').write_text('{"feedAvailable":true}')
        with self.assertRaises(b.CheckFailed): b.validate_site(self.site)

    def test_invalid_or_private_key_metadata_is_rejected(self):
        p = self.site / 'kr/v1/public-key.json'
        doc = json.loads(p.read_text()); doc['privateKeyIncluded'] = True
        p.write_text(json.dumps(doc))
        with self.assertRaises(b.CheckFailed): b.validate_site(self.site)

    def test_initial_deployment_allowed_without_public_request(self):
        calls = []
        def fetch(url, token=None, expected_status=200):
            calls.append((url, token is not None))
            return self.metadata(None)
        self.assertTrue(b.should_deploy(self.files, 'a' * 32, 'FAKE_ONLY', fetch))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1])

    def test_existing_identical_deployment_skips_upload_and_never_forwards_token(self):
        def fetch(url, token=None, expected_status=200):
            if url.startswith('https://api.cloudflare.com/'):
                return self.metadata({'id': 'FAKE_ONLY'})
            self.assertIsNone(token)
            path = url.removeprefix(b.ORIGIN + '/')
            if path == '': path = 'index.html'
            if path == 'kr/v1/current.json':
                self.assertEqual(expected_status, 404)
                path = '404.html'
            return self.files[path]
        self.assertFalse(b.should_deploy(self.files, 'a' * 32, 'FAKE_ONLY', fetch))

    def test_existing_changed_deployment_blocks_overwrite(self):
        def fetch(url, token=None, expected_status=200):
            return self.metadata({'id': 'FAKE_ONLY'}) if token else b'different live content'
        with self.assertRaises(b.CheckFailed):
            b.should_deploy(self.files, 'a' * 32, 'FAKE_ONLY', fetch)

    def test_missing_deployment_metadata_is_not_initial(self):
        doc = json.loads(self.metadata(None)); del doc['result']['latest_deployment']
        with self.assertRaises(b.CheckFailed):
            b.should_deploy(self.files, 'a' * 32, 'FAKE_ONLY', lambda *args: json.dumps(doc).encode())

    def test_canonical_root_and_missing_feed_are_verified(self):
        calls = []
        def fetch(url, token=None, expected_status=200):
            path = url.removeprefix(b.ORIGIN)
            calls.append((path, expected_status))
            name = {'/': 'index.html', '/kr/v1/current.json': '404.html'}.get(path, path[1:])
            return self.files[name]
        b.verify_remote(self.files, fetch)
        self.assertIn(('/', 200), calls)
        self.assertIn(('/kr/v1/current.json', 404), calls)
        self.assertNotIn('/index.html', [c[0] for c in calls])
        self.assertNotIn('/404.html', [c[0] for c in calls])

    def test_expected_404_body_is_read(self):
        error = urllib.error.HTTPError('https://example.invalid/missing', 404, '', {}, io.BytesIO(b'not ready'))
        with patch.object(b.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            self.assertEqual(b.request('https://example.invalid/missing', expected_status=404), b'not ready')

    def test_redirect_is_not_accepted_or_followed(self):
        error = urllib.error.HTTPError('https://example.invalid/', 308, '', {}, io.BytesIO())
        with patch.object(b.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(b.CheckFailed): b.request('https://example.invalid/')

    def test_wrong_project_and_missing_credentials_block_upload(self):
        with self.assertRaises(b.CheckFailed): b.should_deploy(self.files, '', '', lambda *args: b'')
        doc = json.loads(self.metadata(None)); doc['result']['name'] = 'unrelated'
        with self.assertRaises(b.CheckFailed):
            b.should_deploy(self.files, 'a' * 32, 'FAKE_ONLY', lambda *args: json.dumps(doc).encode())


if __name__ == '__main__':
    unittest.main()
