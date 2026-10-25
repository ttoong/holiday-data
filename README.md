# 대한민국 공휴일 자료 공급

아침결 앱 소스와 분리한 정적 공휴일 자료 배포 저장소입니다.
공식 원본은 한국천문연구원 특일 정보의 `getRestDeInfo`입니다.

## 현재 단계

**연결 준비 중입니다.** 아직 공휴일 자료나 정기 갱신을 제공하지 않습니다.
처음에는 준비 화면·상태·공개 검증키만 배포하고, 공휴일 수집·검토·서명·게시·재조회는 후속 단계로 연결합니다.
준비 화면 배포 성공은 공휴일 데이터 공급 성공을 의미하지 않습니다.

- 대상: Cloudflare Pages `mochacookie-holidays`, 무료 정적 파일.
- 예정 사용자 주소: `https://holidays.mochacookie.com` (DNS/HTTPS 연결 전).
- Actions: 공개 저장소의 표준 Ubuntu runner만 사용. 유료 상품·Functions·별도 캐시/아티팩트 저장 없음.
- 초기 배포: Actions의 `Bootstrap Pages`를 수동 실행합니다. 정기 실행은 아직 없습니다.
- 업로드 폴더는 `site`로 고정하고 허용 파일만 검사합니다. 저장소 전체를 업로드하지 않습니다.
- 기존 배포가 있고 준비 파일과 일치하지 않으면 준비 화면으로 덮어쓰지 않습니다.
- 서명 개인키·API 키·게시 토큰은 GitHub Actions Secrets에만 연결합니다. 공개 검증키로 개인키를 복구할 수 없습니다.
- 공개키는 배포자의 서명을 검증하기 위한 값이며 KASI가 직접 서명한 자료를 의미하지 않습니다.

## 비밀과 복구

`CLOUDFLARE_API_TOKEN`, `KASI_SERVICE_KEY`, `HOLIDAY_SIGNING_PRIVATE_KEY`는 비밀값입니다.
`CLOUDFLARE_ACCOUNT_ID`는 계정 식별 변수입니다.
초기 준비 화면 워크플로는 Pages 토큰만 사용하고 KASI/서명 개인키를 읽지 않습니다.
개인키의 별도 안전한 복구 보관과 키 교체 절차는 공급 시작 전 확인해야 합니다.
GitHub Secrets의 원문은 다시 조회할 수 없습니다. 이 저장소는 비밀의 복구 백업이 아닙니다.

## 검증

```sh
python3 -m unittest discover -s tests -v
python3 tools/bootstrap.py validate
```

최초 검증 절차는 허용 파일 검사 → 기존 Pages 배포 확인 → 업로드 → 정확한 HTTPS 바이트 재조회입니다.
실패하면 GitHub 작업이 실패로 표시됩니다. 이메일 수신과 미실행 감시는 아직 검증하지 않았습니다.
실제 앱·알람·iPhone·사용자 계정 데이터는 이 저장소에 포함하지 않습니다.

공식 참고: [Cloudflare CI 업로드](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/), [GitHub 표준 runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
