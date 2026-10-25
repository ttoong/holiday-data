"""준비 파일만 게시하고 실제 운영 자료의 덮어쓰기를 차단하는 검사입니다.

개인키/API 키를 입력받지 않습니다. Pages 토큰은 프로젝트 조회에만 사용하며
예외에 URL·요청 헤더·응답 원문을 싣지 않아 공개 CI 로그 노출을 방지합니다.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'mochacookie-holidays'
ORIGIN = 'https://mochacookie-holidays.pages.dev'
ALLOWED = {'index.html', '404.html', '_headers', 'status.json', 'kr/v1/public-key.json'}


class CheckFailed(Exception):
    """비밀 없는 고정 메시지만 외부로 전달합니다."""


def validate_site(folder):
    """업로드 디렉터리의 정확한 허용 목록과 공개키/준비 상태를 검사합니다."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise CheckFailed('Invalid site directory')
    paths = list(folder.rglob('*'))
    if any(p.is_symlink() for p in paths):
        raise CheckFailed('Site links are forbidden')
    files = {p.relative_to(folder).as_posix() for p in paths if p.is_file()}
    if files != ALLOWED or any((folder / name).stat().st_size > 20000 for name in files):
        raise CheckFailed('Unexpected upload files or file size')
    try:
        status = json.loads((folder / 'status.json').read_text())
        expected = {'format': 1, 'phase': 'bootstrap', 'feedAvailable': False,
                    'scheduledRefreshEnabled': False}
        if status != expected:
            raise ValueError()
        key = json.loads((folder / 'kr/v1/public-key.json').read_text())
        if set(key) != {'format', 'algorithm', 'keyId', 'publicKeyBase64', 'privateKeyIncluded'}:
            raise ValueError()
        raw = base64.b64decode(key['publicKeyBase64'], validate=True)
        if (key['format'] != 1 or key['algorithm'] != 'Ed25519' or len(raw) != 32
                or key['privateKeyIncluded'] is not False
                or key['keyId'] != hashlib.sha256(raw).hexdigest()[:16]):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise CheckFailed('Invalid public metadata') from None
    return {name: (folder / name).read_bytes() for name in sorted(files)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Bearer 인증과 검증 대상이 다른 호스트로 넘어가지 않게 합니다."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, token=None, expected_status=200):
    """정해진 호출부에서만 사용하며 제한된 본문을 읽고 실패 원문은 숨깁니다."""
    headers = {'User-Agent': 'achimgyeol-holiday-bootstrap/1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        try:
            response = urllib.request.build_opener(NoRedirect).open(
                urllib.request.Request(url, headers=headers), timeout=20)
        except urllib.error.HTTPError as error:
            # 없는 공급 파일이 HTML 200으로 위장하지 않는지 404 본문까지 검증합니다.
            # 리다이렉트나 인증 오류는 예상 성공 상태로 처리하지 않습니다.
            if error.code != expected_status:
                raise CheckFailed('Unexpected HTTP status: ' + str(error.code)) from None
            response = error
        with response:
            if response.status != expected_status:
                raise CheckFailed('Unexpected HTTP status: ' + str(response.status))
            data = response.read(1_000_001)
            if len(data) > 1_000_000:
                raise CheckFailed('Response too large')
            return data
    except CheckFailed:
        raise
    except Exception:
        raise CheckFailed('Remote read failed; response suppressed') from None


def verify_remote(files, fetch=request):
    """Pages의 정규 URL과 미게시 API 404를 검사하며 리다이렉트를 따르지 않습니다.

    Pages는 index.html을 /로, 다른 .html 경로를 확장자 없는 경로로 308 이동합니다.
    따라서 원본 파일명으로 조회하지 않고 실제 사용자 경로의 본문/상태를 대조합니다.
    """
    for name, data in files.items():
        if name == '_headers':
            continue
        path = '/' if name == 'index.html' else '/' + name
        status = 200
        if name == '404.html':
            path, status = '/kr/v1/current.json', 404
        if fetch(ORIGIN + path, expected_status=status) != data:
            raise CheckFailed('Published bytes differ')


def should_deploy(files, account, token, fetch=request):
    """최초만 업로드하며, 재실행에서는 동일 준비 파일인지 확인 후 업로드를 생략합니다.

    다른 내용의 기존 배포는 운영 자료일 수 있으므로 준비 화면으로 되돌리지 않습니다.
    실패한 직전 게시도 이 규칙에 따라 조용히 덮어쓰지 않고 사람이 확인합니다.
    """
    if (not account or len(account) != 32
            or any(c not in '0123456789abcdef' for c in account) or not token):
        raise CheckFailed('Missing or invalid Pages credentials')
    try:
        result = json.loads(fetch('https://api.cloudflare.com/client/v4/accounts/'
                                  + account + '/pages/projects/' + PROJECT, token))
        project = result['result']
        if (result.get('success') is not True or project['name'] != PROJECT
                or project['subdomain'] != PROJECT + '.pages.dev'):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise CheckFailed('Unexpected Pages project') from None
    # 누락을 최초 배포로 오인하지 않도록 필드 존재까지 요구합니다.
    if 'latest_deployment' not in project:
        raise CheckFailed('Missing deployment metadata')
    if project['latest_deployment'] is None:
        return True
    verify_remote(files, fetch)
    return False


def main():
    """명시적 작업만 수행하고 비밀/응답을 포함하지 않는 상태만 기록합니다."""
    try:
        files = validate_site(ROOT / 'site')
        mode = sys.argv[1] if len(sys.argv) == 2 else ''
        if mode == 'validate':
            print('PASS: bootstrap upload allowlist and public metadata')
        elif mode == 'guard':
            deploy = should_deploy(files, os.environ.get('CLOUDFLARE_ACCOUNT_ID'),
                                   os.environ.get('CLOUDFLARE_API_TOKEN'))
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write('deploy=' + str(deploy).lower() + '\n')
            print('First deployment allowed' if deploy else 'Identical bootstrap already published')
        elif mode == 'verify':
            for attempt in range(6):
                try:
                    verify_remote(files)
                    print('PASS: exact HTTPS readback of all public bootstrap files')
                    break
                except CheckFailed:
                    if attempt == 5:
                        raise
                    time.sleep(3)
        else:
            raise CheckFailed('Use validate, guard or verify')
    except CheckFailed as error:
        print('FAILED: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
