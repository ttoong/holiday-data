"""실제 서버·운영키 없이 수집 오류와 게시 중단/재시작을 시험합니다.

암호 연산은 별도 Swift selftest/실제 앱 검증기 대조로 확인하며 여기서는 전송을 주입합니다.
"""
import base64
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('update', ROOT / 'tools/update.py')
u = importlib.util.module_from_spec(spec); spec.loader.exec_module(u)
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def rows(year):
    return [{'date': str(year) + suffix, 'name': name, 'sequence': 1, 'isHoliday': True}
            for suffix, name in [('0101', '1월1일'), ('1225', '기독탄신일')]]


def fake_sign(payload):
    return u.encode({'payload': base64.b64encode(payload).decode(),
                     'signature': base64.b64encode(bytes(64)).decode()})


def page_xml(year, total=2, page=1, count=None, result='00'):
    records = rows(year) if count is None else rows(year)[:count]
    items = ''.join('<item><dateKind>01</dateKind><dateName>' + r['name'] + '</dateName>'
                    '<isHoliday>Y</isHoliday><locdate>' + r['date'] + '</locdate><seq>1</seq></item>' for r in records)
    return ('<response><header><resultCode>' + result + '</resultCode></header><body><items>' + items
            + '</items><numOfRows>100</numOfRows><pageNo>' + str(page) + '</pageNo><totalCount>'
            + str(total) + '</totalCount></body></response>').encode()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        shutil.copytree(ROOT / 'site', self.root / 'site')
        # 실행 당시 실제 자료와 무관한, 예약 없는 합성 공급 폴더만 만듭니다.
        (self.root / u.PACKET_PATH).unlink(missing_ok=True)
        (self.root / 'templates').mkdir()
        shutil.copyfile(ROOT / 'templates/bootstrap-index.html', self.root / 'templates/bootstrap-index.html')
        self.verify = lambda raw: None

    def tearDown(self): self.temp.cleanup()

    def prepare(self, now=NOW, initial=True, collect=None):
        return u.prepare(self.root, now, initial=initial, collect=collect or (lambda y, key: rows(y)),
                         sign=fake_sign, verify=self.verify)

    def live(self, packet):
        def fetch(url, accepted=(200,)):
            if url.endswith('/current.json'): return (404, b'missing') if packet is None else (200, packet)
            path = url.removeprefix(next(o for o in u.ORIGINS if url.startswith(o)))
            return 200, (self.root / 'site' / ('index.html' if path == '/' else path[1:])).read_bytes()
        return fetch

    def confirm(self):
        u.confirm(self.root, NOW, fetch=self.live((self.root / u.PACKET_PATH).read_bytes()), verify=self.verify)

    def test_first_publication_requires_explicit_initial_flag(self):
        with self.assertRaises(u.Failed): self.prepare(initial=False)
        self.assertFalse((self.root / 'state/ledger.json').exists())

    def test_candidate_is_pending_until_readback_confirmation(self):
        self.prepare(); state, old = u.load_state(self.root, NOW, self.verify)
        self.assertEqual(state['highWater'], 1); self.assertEqual(state['pending']['sequence'], 1)
        self.assertIsNone(old); self.assertIsNone(state['confirmed'])
        self.confirm(); state, old = u.load_state(self.root, NOW, self.verify)
        self.assertIsNone(state['pending']); self.assertEqual(state['confirmed']['sha256'], u.digest(old))

    def test_unknown_live_data_preserves_pending_and_bytes(self):
        self.prepare(); before = (self.root / 'state/ledger.json').read_bytes()
        with self.assertRaises(u.Failed): u.reconcile(self.root, NOW, self.live(b'unknown'), self.verify)
        self.assertEqual(before, (self.root / 'state/ledger.json').read_bytes())

    def test_restart_after_upload_confirms_without_new_fetch_or_signature(self):
        self.prepare(); packet = (self.root / u.PACKET_PATH).read_bytes()
        self.assertTrue(u.reconcile(self.root, NOW, self.live(packet), self.verify))
        state, old = u.load_state(self.root, NOW, self.verify)
        self.assertEqual(state['highWater'], 1); self.assertIsNone(state['pending']); self.assertEqual(old, packet)

    def test_failed_initial_upload_uses_higher_sequence_on_retry(self):
        self.prepare(); u.reconcile(self.root, NOW, self.live(None), self.verify)
        self.assertFalse((self.root / u.PACKET_PATH).exists())
        self.assertEqual(self.prepare(initial=False)['sequence'], 2)

    def test_failed_update_restores_known_site_and_retains_high_water(self):
        self.prepare(); self.confirm(); old = (self.root / u.PACKET_PATH).read_bytes()
        self.prepare(now=NOW + timedelta(seconds=2), initial=False)
        u.reconcile(self.root, NOW + timedelta(seconds=2), self.live(old), self.verify)
        self.assertEqual((self.root / u.PACKET_PATH).read_bytes(), old)
        self.assertEqual(self.prepare(now=NOW + timedelta(seconds=3), initial=False)['sequence'], 3)

    def test_unchanged_content_keeps_revision_but_advances_release(self):
        first = self.prepare(); self.confirm()
        second = self.prepare(NOW + timedelta(hours=12), initial=False)
        self.assertEqual(first['cache']['revision'], second['cache']['revision'])
        self.assertGreater(second['sequence'], first['sequence'])
        self.assertGreater(second['cache']['fetchedAt'], first['cache']['fetchedAt'])

    def test_changed_holiday_advances_content_revision(self):
        self.prepare(); self.confirm()
        def collect(year, key): return rows(year) + [{'date': str(year) + '1009', 'name': '합성 임시공휴일', 'sequence': 1, 'isHoliday': True}]
        second = self.prepare(NOW + timedelta(seconds=2), initial=False, collect=collect)
        self.assertEqual(second['cache']['revision'], 2)

    def test_collection_failure_does_not_modify_confirmed_or_ledger(self):
        self.prepare(); self.confirm()
        before = {name: (self.root / name).read_bytes() for name in [u.PACKET_PATH, 'state/ledger.json', 'state/confirmed.json']}
        def collect(year, key): raise u.Failed('synthetic offline')
        with self.assertRaises(u.Failed): self.prepare(NOW + timedelta(seconds=2), initial=False, collect=collect)
        self.assertTrue(all((self.root / name).read_bytes() == data for name, data in before.items()))

    def test_signature_failure_before_local_mutation(self):
        with self.assertRaises(u.Failed):
            u.prepare(self.root, NOW, initial=True, collect=lambda y, k: rows(y), sign=lambda _: b'invalid', verify=self.verify)
        self.assertFalse((self.root / 'state/ledger.json').exists())

    def test_divergent_origins_block_recovery(self):
        self.prepare()
        def fetch(url, accepted=(200,)): return (200, b'one') if url.startswith(u.ORIGINS[0]) else (404, b'')
        with self.assertRaises(u.Failed): u.reconcile(self.root, NOW, fetch, self.verify)

    def test_different_readback_cannot_be_marked_confirmed(self):
        self.prepare()
        with self.assertRaises(u.Failed): u.confirm(self.root, NOW, self.live(b'unknown'), self.verify)
        state, _ = u.load_state(self.root, NOW, self.verify); self.assertIsNotNone(state['pending'])

    def test_missing_ledger_with_existing_data_cannot_reset_sequence(self):
        self.prepare(); (self.root / 'state/ledger.json').unlink()
        with self.assertRaises(u.Failed): u.load_state(self.root, NOW, self.verify)

    def test_hash_mismatch_cannot_be_overwritten(self):
        self.prepare(); (self.root / u.PACKET_PATH).write_bytes(b'corrupt')
        with self.assertRaises(u.Failed): self.prepare(initial=False)

    def test_expired_known_packet_can_be_recovered_and_renewed(self):
        self.prepare(); self.confirm()
        second = self.prepare(NOW + timedelta(days=8), initial=False)
        self.assertEqual(second['sequence'], 2)
        with self.assertRaises(u.Failed):
            u.validate_packet((self.root / 'state/confirmed.json').read_bytes(), NOW + timedelta(days=8), verify=self.verify)

    def test_extra_upload_file_is_rejected(self):
        self.prepare(); (self.root / 'site/.env').write_text('FAKE_ONLY')
        with self.assertRaises(u.Failed): u.validate_upload(self.root, NOW, self.verify)

    def test_secret_text_in_upload_is_rejected(self):
        self.prepare()
        with patch.dict('os.environ', {'KASI_SERVICE_KEY': 'FAKE_SECRET_FOR_TEST'}):
            with (self.root / 'site/index.html').open('a') as stream: stream.write('FAKE_SECRET_FOR_TEST')
            with self.assertRaises(u.Failed): u.validate_upload(self.root, NOW, self.verify)

    def test_api_key_encoded_exactly_once(self):
        def fetch(url):
            self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)['ServiceKey'], ['fake+/='])
            return 200, page_xml(2026)
        self.assertEqual(u.fetch_year(2026, 'fake+/=', fetch), rows(2026))

    def test_api_rejection_missing_rows_and_wrong_page_block(self):
        for raw in [page_xml(2026, result='30'), page_xml(2026, count=1), page_xml(2026, page=2)]:
            with self.assertRaises(u.Failed): u.fetch_year(2026, 'FAKE_ONLY', lambda url: (200, raw))

    def test_invalid_date_duplicate_and_missing_minimum_are_rejected(self):
        for data in [rows(2026)[:1], rows(2026) + rows(2026), [{**r, 'date': '20260230'} for r in rows(2026)]]:
            with self.assertRaises(u.Failed): u.validate_rows(data, 2026)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(u.Failed): u.decode(b'{"format":1,"format":2}')

    def test_korean_year_boundary_fetches_new_current_and_next_year(self):
        self.prepare(); self.confirm(); queried = []
        def collect(year, key): queried.append(year); return rows(year)
        release = self.prepare(datetime(2026, 12, 31, 15, 0, tzinfo=timezone.utc), initial=False, collect=collect)
        self.assertEqual(queried, [2027, 2028])
        self.assertEqual(set(release['cache']['years']), {'2027', '2028'})

    def test_clock_rollback_preserves_saved_state(self):
        self.prepare(); self.confirm(); before = (self.root / 'state/ledger.json').read_bytes()
        with self.assertRaises(u.Failed): self.prepare(NOW - timedelta(seconds=1), initial=False)
        self.assertEqual(before, (self.root / 'state/ledger.json').read_bytes())


if __name__ == '__main__': unittest.main()
