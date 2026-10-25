"""공휴일 전용 공개 저장소의 생성 자료만 커밋하고, 원격 이동 시 덮어쓰지 않습니다.

GitHub의 해당 저장소 전용 임시 토큰을 gh credential helper가 환경 변수로 읽습니다.
개인 토큰·키를 인자/로그에 넣지 않고 global Git 설정이나 다른 저장소를 바꾸지 않습니다.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = 'https://github.com/ttoong/holiday-data'


def git(*args):
    result = subprocess.run(['git', '-C', str(ROOT), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode: raise RuntimeError('Git operation failed; details suppressed')
    return result.stdout.decode().strip()


def main():
    try:
        origin = git('remote', 'get-url', 'origin').removesuffix('.git')
        if origin != EXPECTED: raise RuntimeError('Unexpected repository; no commit made')
        if not git('status', '--porcelain', '--', 'site', 'state'):
            print('No generated state changes'); return 0
        git('fetch', 'origin', 'main')
        if git('rev-parse', 'HEAD') != git('rev-parse', 'origin/main'):
            raise RuntimeError('Remote branch moved; no upload permitted')
        # 사용자 원본/코드가 아닌 공개 공급 파일과 운영 원장 두 디렉터리만 반영합니다.
        git('add', '--', 'site')
        if (ROOT / 'state').is_dir(): git('add', '--', 'state')
        message = {'reconcile': 'Reconcile holiday publication state',
                   'prepare': 'Record signed holiday candidate before upload',
                   'confirm': 'Confirm exact holiday publication readback'}.get(sys.argv[1] if len(sys.argv) == 2 else '')
        if message is None: raise RuntimeError('Unexpected state operation')
        git('-c', 'user.name=github-actions[bot]', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
            'commit', '-m', message)
        # 보통 push가 아닌 fast-forward만 허용합니다. 보호 규칙/동시 수정은 실패로 남깁니다.
        git('-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential', 'push', 'origin', 'HEAD:main')
        print('PASS: generated state commit and push'); return 0
    except Exception:
        print('FAILED: state commit/push; no force push performed', file=sys.stderr); return 1


if __name__ == '__main__': sys.exit(main())
