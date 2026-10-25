"""공식 자료 수집→번호/시도 선기록→서명 게시→정확한 재조회 확인을 연결합니다.

실제 업로드는 별도 CI 단계이며 이 코드는 비밀을 출력하지 않습니다. 원본 오류/불명
상태에서 초기화하지 않고 중단합니다. pending 상태는 다음 실행이 공개 파일과 대조합니다.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ORIGINS = ('https://mochacookie-holidays.pages.dev', 'https://holidays.mochacookie.com')
SOURCE = 'KASI:getRestDeInfo'
KEY_PATH = ROOT / 'site/kr/v1/public-key.json'
PACKET_PATH = 'site/kr/v1/current.json'
LIMIT = 1_000_000


class Failed(Exception):
    """원문 URL/키/응답을 담지 않는 오류만 CI에 전달합니다."""


def require(value, message='Invalid public data'):
    if not value: raise Failed(message)


def integer(value, low=1, high=2**53 - 1):
    return type(value) is int and low <= value <= high


def decode(raw):
    """중복 JSON 키가 서로 다른 해석을 만들지 못하게 합니다."""
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON field')
            result[key] = value
        return result
    try: return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, TypeError): raise Failed('Invalid JSON') from None


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def stamp(date): return date.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def instant(value):
    try:
        require(type(value) is str)
        parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        require(stamp(parsed) == value)
        return parsed
    except ValueError: raise Failed('Invalid timestamp') from None


def digest(raw): return hashlib.sha256(raw).hexdigest()


def validate_rows(rows, year):
    """연간 응답의 범위/중복/최소 필수일을 검사합니다. 법적 정확성 전체의 증명은 아닙니다."""
    require(type(rows) is list and 1 <= len(rows) <= 1000, 'Incomplete annual data')
    seen = set()
    for row in rows:
        require(type(row) is dict and set(row) == {'date', 'name', 'sequence', 'isHoliday'})
        require(type(row['date']) is str and len(row['date']) == 8 and row['date'].isdigit())
        try: date = datetime.strptime(row['date'], '%Y%m%d')
        except ValueError: raise Failed('Invalid holiday date') from None
        require(date.year == year and date.strftime('%Y%m%d') == row['date'])
        require(type(row['name']) is str and row['name'].strip() and len(row['name']) <= 200)
        require(integer(row['sequence']) and type(row['isHoliday']) is bool)
        identity = (row['date'], row['sequence'])
        require(identity not in seen, 'Duplicate holiday record'); seen.add(identity)
    days = {row['date'] for row in rows if row['isHoliday']}
    require({str(year) + '0101', str(year) + '1225'} <= days, 'Missing minimum annual dates')
    return sorted(rows, key=lambda row: (row['date'], row['sequence']))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None


def http(url, accepted=(200,)):
    """인증키가 포함된 공식 URL도 리다이렉트하지 않고 오류 원문을 숨깁니다."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'achimgyeol-holiday-feed/1', 'Accept': '*/*'})
        try: response = urllib.request.build_opener(NoRedirect).open(req, timeout=20)
        except urllib.error.HTTPError as error:
            if error.code not in accepted: raise Failed('HTTP status ' + str(error.code)) from None
            response = error
        with response:
            require(response.status in accepted, 'Unexpected HTTP status')
            raw = response.read(LIMIT + 1); require(len(raw) <= LIMIT, 'Response too large')
            return response.status, raw
    except Failed: raise
    except Exception: raise Failed('Network read failed; details suppressed') from None


def fetch_year(year, key, fetch=http):
    require(integer(year, 1900, 2099) and key and not any(c.isspace() for c in key), 'Invalid official query')
    rows, total = [], None
    for page in range(1, 11):
        query = urllib.parse.urlencode({'ServiceKey': key, 'solYear': year, 'numOfRows': 100, 'pageNo': page, '_type': 'xml'})
        _, raw = fetch('https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo?' + query)
        require(b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper(), 'Unsupported XML declarations')
        try:
            tree = ET.fromstring(raw)
            require(tree.tag == 'response' and tree.findtext('header/resultCode') == '00', 'Official API rejected query')
            count = int(tree.findtext('body/totalCount', ''))
            require(1 <= count <= 1000 and int(tree.findtext('body/pageNo', '')) == page
                    and int(tree.findtext('body/numOfRows', '')) == 100, 'Incomplete official page')
            if total is None: total = count
            require(total == count, 'Official page total changed')
            items = tree.findall('body/items/item')
            require(len(items) == min(100, total - len(rows)), 'Missing or extra page records')
            for item in items:
                require(item.findtext('dateKind') == '01' and item.findtext('isHoliday') in ['Y', 'N'])
                rows.append({'date': item.findtext('locdate'), 'name': item.findtext('dateName'),
                             'sequence': int(item.findtext('seq', '')), 'isHoliday': item.findtext('isHoliday') == 'Y'})
        except (ValueError, ET.ParseError): raise Failed('Invalid official XML') from None
        if len(rows) == total: return validate_rows(rows, year)
    raise Failed('Annual page limit reached')


def crypto(mode, raw):
    """검토한 Swift 바이너리에 공개 파일 경로만 전달합니다. 개인키는 환경 변수로만 읽습니다."""
    binary = os.environ.get('HOLIDAY_SIGNER_BIN')
    require(binary and Path(binary).is_file(), 'Signing tool not configured')
    with tempfile.TemporaryDirectory(prefix='holiday-sign-') as tmp:
        src, dst = Path(tmp) / 'input.json', Path(tmp) / 'output.json'
        src.write_bytes(raw)
        result = subprocess.run([binary, mode, str(src), str(KEY_PATH), str(dst)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        require(result.returncode == 0, 'Signature verification or signing failed')
        return dst.read_bytes() if mode == 'sign' else None


def validate_packet(raw, now, fresh=True, verify=None):
    require(len(raw) <= LIMIT)
    packet = decode(raw)
    require(type(packet) is dict and set(packet) == {'payload', 'signature'})
    try:
        payload = base64.b64decode(packet['payload'], validate=True)
        signature = base64.b64decode(packet['signature'], validate=True)
    except (ValueError, TypeError): raise Failed('Invalid signature envelope') from None
    require(len(signature) == 64 and len(payload) <= LIMIT)
    (verify or (lambda data: crypto('verify', data)))(raw)
    release = decode(payload)
    require(type(release) is dict and set(release) == {'format', 'sequence', 'source', 'publishedAt', 'expiresAt', 'cache'})
    require(integer(release['format'], 1, 1) and integer(release['sequence']) and release['source'] == SOURCE)
    cache = release['cache']
    require(type(cache) is dict and set(cache) == {'format', 'revision', 'fetchedAt', 'years'})
    require(integer(cache['format'], 1, 1) and integer(cache['revision']) and type(cache['years']) is dict)
    fetched, published, expires = (instant(cache['fetchedAt']), instant(release['publishedAt']), instant(release['expiresAt']))
    require(fetched <= published <= now and published < expires <= fetched + timedelta(days=7), 'Invalid release time')
    years = list(cache['years'])
    require(1 <= len(years) <= 3 and all(type(y) is str and y.isdigit() and len(y) == 4 and 1900 <= int(y) <= 2099 for y in years))
    numbers = sorted(map(int, years)); require(numbers == list(range(numbers[0], numbers[-1] + 1)))
    for year, rows in cache['years'].items(): validate_rows(rows, int(year))
    if fresh:
        year = now.astimezone(ZoneInfo('Asia/Seoul')).year
        require(expires > now and {str(year), str(year + 1)} <= set(years), 'Expired or missing annual coverage')
    return release


def read_file(root, name):
    path = root / name
    require(not path.is_symlink() and not any(p.is_symlink() for p in path.parents), 'State links are forbidden')
    if not path.exists(): return None
    require(path.stat().st_size <= LIMIT, 'State too large')
    return path.read_bytes()


def write_file(root, name, data):
    path = root / name
    require(not path.is_symlink() and not any(p.is_symlink() for p in path.parents), 'State links are forbidden')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.new')
    require(not temp.exists() and not temp.is_symlink(), 'Unexpected temporary state')
    temp.write_bytes(data); os.replace(temp, path)


def load_state(root, now, verify=None):
    raw = read_file(root, 'state/ledger.json')
    if raw is None:
        require(read_file(root, PACKET_PATH) is None and read_file(root, 'state/confirmed.json') is None,
                'Missing ledger with existing data')
        return {'format': 1, 'highWater': 0, 'pending': None, 'confirmed': None, 'lastAttempt': None}, None
    state = decode(raw)
    require(type(state) is dict and set(state) == {'format', 'highWater', 'pending', 'confirmed', 'lastAttempt'})
    require(integer(state['format'], 1, 1) and integer(state['highWater'], 0), 'Invalid sequence ledger')
    last = state['lastAttempt']
    if last is not None:
        require(type(last) is dict and set(last) == {'sequence', 'sha256', 'outcome'}
                and integer(last['sequence']) and last['sequence'] <= state['highWater']
                and type(last['sha256']) is str and len(last['sha256']) == 64
                and all(c in '0123456789abcdef' for c in last['sha256'])
                and last['outcome'] in ['confirmed', 'confirmed-after-restart', 'not-published-new-sequence-required'])
    for field in ['pending', 'confirmed']:
        entry = state[field]
        if entry is not None:
            require(type(entry) is dict and set(entry) == {'sequence', 'sha256'})
            require(integer(entry['sequence']) and entry['sequence'] <= state['highWater']
                    and type(entry['sha256']) is str and len(entry['sha256']) == 64
                    and all(c in '0123456789abcdef' for c in entry['sha256']))
            data = read_file(root, PACKET_PATH if field == 'pending' else 'state/confirmed.json')
            require(data is not None and digest(data) == entry['sha256'], 'State packet hash differs')
            release = validate_packet(data, now, fresh=False, verify=verify)
            require(release['sequence'] == entry['sequence'], 'State packet sequence differs')
    if state['pending'] and state['confirmed']:
        require(state['pending']['sequence'] > state['confirmed']['sequence'])
    confirmed = read_file(root, 'state/confirmed.json')
    require((confirmed is None) == (state['confirmed'] is None), 'Untracked confirmed state')
    return state, confirmed


def persist(root, state): write_file(root, 'state/ledger.json', encode(state))


def remote_packets(fetch=http):
    result = []
    for origin in ORIGINS:
        status, raw = fetch(origin + '/kr/v1/current.json', accepted=(200, 404))
        result.append(None if status == 404 else raw)
    require(result[0] == result[1], 'Public origins disagree; preserve pending state')
    return result[0]


def render_site(root, release, packet):
    """공개 설명에도 실제 조회 시각과 제공 범위를 표시하고 API 이름/계정은 넣지 않습니다."""
    cache = release['cache']
    rows = ''.join('<tr><td>' + html.escape(row['date']) + '</td><td>' + html.escape(row['name']) + '</td></tr>'
                   for year in sorted(cache['years']) for row in cache['years'][year] if row['isHoliday'])
    page = ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>아침결 공휴일 자료</title><style>body{max-width:48rem;margin:3rem auto;padding:1rem;font:16px/1.8 system-ui;'
            'color:#18243b;background:#faf7f0}td{padding:.25rem 1rem .25rem 0}h1{font-size:1.8rem}</style>'
            '<main><p>쉬는 날을 아는 알람 · 아침결</p><h1>대한민국 공휴일 자료</h1>'
            '<p>출처: 한국천문연구원 특일 정보 getRestDeInfo</p><p>공식 API 조회 시각(UTC): '
            + html.escape(cache['fetchedAt']) + '</p><p>배포 번호: ' + str(release['sequence'])
            + ' · 자료 유효기한(UTC): ' + html.escape(release['expiresAt'])
            + '</p><p>법정·대체·임시공휴일은 공식 API에 반영된 내용을 제공합니다. 서명은 배포자가 생성합니다.</p>'
            '<table><thead><tr><th>날짜</th><th>공휴일</th></tr></thead><tbody>' + rows + '</tbody></table></main></html>')
    write_file(root, PACKET_PATH, packet)
    write_file(root, 'site/index.html', page.encode())
    write_file(root, 'site/status.json', encode({'format': 1, 'phase': 'active', 'feedAvailable': True,
        'scheduledRefreshEnabled': True, 'sequence': release['sequence'], 'sourceFetchedAt': cache['fetchedAt'],
        'expiresAt': release['expiresAt'], 'packetSHA256': digest(packet)}))


def reconcile(root, now, fetch=http, verify=None):
    """직전 후보를 재업로드하지 않습니다. 양쪽 공개 주소가 알려진 파일과 같을 때만 정리합니다."""
    state, old = load_state(root, now, verify)
    live = remote_packets(fetch)
    pending = state['pending']
    if pending is None:
        require(live == old, 'Unknown live data; publishing blocked')
        return False
    candidate = read_file(root, PACKET_PATH)
    if live == candidate:
        state['confirmed'] = pending
        write_file(root, 'state/confirmed.json', candidate)
        outcome = 'confirmed-after-restart'
    else:
        require(live == old, 'Unknown live data; pending preserved')
        # 실패 후보는 Git 이력에 남고 highWater도 유지합니다. 같은 후보를 재게시하지 않습니다.
        if old is not None:
            render_site(root, validate_packet(old, now, fresh=False, verify=verify), old)
        else:
            (root / PACKET_PATH).unlink()
            write_file(root, 'site/status.json', encode({'format': 1, 'phase': 'bootstrap', 'feedAvailable': False,
                                                       'scheduledRefreshEnabled': False}))
            write_file(root, 'site/index.html', (root / 'templates/bootstrap-index.html').read_bytes())
        outcome = 'not-published-new-sequence-required'
    state['lastAttempt'] = {**pending, 'outcome': outcome}
    state['pending'] = None; persist(root, state)
    return True


def prepare(root, now, initial=False, collect=fetch_year, sign=None, verify=None):
    state, old = load_state(root, now, verify)
    require(state['pending'] is None, 'Reconcile pending publication first')
    require(state['highWater'] > 0 or initial, 'First publication requires explicit initial flag')
    key = os.environ.get('KASI_SERVICE_KEY', '')
    year = now.astimezone(ZoneInfo('Asia/Seoul')).year
    years = {str(y): validate_rows(collect(y, key), y) for y in [year, year + 1]}
    previous = validate_packet(old, now, fresh=False, verify=verify) if old else None
    if previous: require(instant(previous['cache']['fetchedAt']) <= now and instant(previous['publishedAt']) <= now, 'Clock moved backwards')
    same = previous is not None and previous['cache']['years'] == years
    revision = previous['cache']['revision'] if same else (previous['cache']['revision'] if previous else 0) + 1
    release = {'format': 1, 'sequence': state['highWater'] + 1, 'source': SOURCE, 'publishedAt': stamp(now),
               'expiresAt': stamp(now + timedelta(days=7)),
               'cache': {'format': 1, 'revision': revision, 'fetchedAt': stamp(now), 'years': years}}
    packet = (sign or (lambda data: crypto('sign', data)))(encode(release))
    require(validate_packet(packet, now, verify=verify) == release, 'Signed payload differs')
    # 후보 파일/원장은 업로드 전 별도 Git 커밋에 영속화해야 합니다. 이 함수 자체는 업로드하지 않습니다.
    render_site(root, release, packet)
    state['highWater'] = release['sequence']
    state['pending'] = {'sequence': release['sequence'], 'sha256': digest(packet)}
    persist(root, state)
    return release


def confirm(root, now, fetch=http, verify=None):
    state, _ = load_state(root, now, verify)
    require(state['pending'] is not None, 'Missing pending publication')
    packet = read_file(root, PACKET_PATH)
    validate_packet(packet, now, verify=verify)
    require(remote_packets(fetch) == packet, 'Published packet differs; pending preserved')
    for origin in ORIGINS:
        for name in ['index.html', 'status.json', 'kr/v1/public-key.json']:
            _, raw = fetch(origin + ('/' if name == 'index.html' else '/' + name))
            require(raw == (root / 'site' / name).read_bytes(), 'Published site differs; pending preserved')
    state['confirmed'] = state['pending']
    state['lastAttempt'] = {**state['pending'], 'outcome': 'confirmed'}
    state['pending'] = None
    write_file(root, 'state/confirmed.json', packet); persist(root, state)


def validate_upload(root, now, verify=None):
    paths = list((root / 'site').rglob('*'))
    allowed = {'index.html', '404.html', '_headers', 'status.json', 'kr/v1/public-key.json', 'kr/v1/current.json'}
    require(not (root / 'site').is_symlink() and not any(p.is_symlink() for p in paths), 'Upload links forbidden')
    require({p.relative_to(root / 'site').as_posix() for p in paths if p.is_file()} == allowed, 'Unexpected upload file')
    require(all(p.stat().st_size <= LIMIT for p in paths if p.is_file()), 'Upload file too large')
    state, _ = load_state(root, now, verify)
    require(state['pending'] is not None, 'Missing pending state')
    release = validate_packet(read_file(root, PACKET_PATH), now, verify=verify)
    status = decode((root / 'site/status.json').read_bytes())
    require(status.get('sequence') == release['sequence'] and status.get('packetSHA256') == state['pending']['sha256'])
    # 공식 응답이 우연히 비밀값을 포함해도 공개 파일/이력에 넣지 않습니다.
    for name in ['KASI_SERVICE_KEY', 'HOLIDAY_SIGNING_PRIVATE_KEY', 'CLOUDFLARE_API_TOKEN']:
        secret = os.environ.get(name)
        if secret:
            require(not any(secret.encode() in p.read_bytes() for p in paths if p.is_file()), 'Secret found in upload')


def main():
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        command = sys.argv[1] if len(sys.argv) > 1 else ''
        if command == 'reconcile': reconcile(ROOT, now)
        elif command == 'prepare':
            require(sys.argv[2:] in [[], ['--initial']], 'Invalid preparation arguments')
            release = prepare(ROOT, now, initial=sys.argv[2:] == ['--initial'])
            validate_upload(ROOT, now)
            print('Prepared release sequence ' + str(release['sequence']))
        elif command == 'validate': validate_upload(ROOT, now)
        elif command == 'confirm': confirm(ROOT, now)
        else: raise Failed('Use reconcile, prepare [--initial], validate or confirm')
        print('PASS: ' + command)
        return 0
    except Failed as error:
        print('FAILED: ' + str(error), file=sys.stderr); return 1
    except Exception:
        print('FAILED: local processing; details suppressed', file=sys.stderr); return 1


if __name__ == '__main__': sys.exit(main())
