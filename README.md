# 대한민국 공휴일 자료 공급

아침결 앱 소스와 분리한 정적 공휴일 자료 배포 저장소입니다.
공식 원본은 한국천문연구원 특일 정보의 `getRestDeInfo`입니다.

## 현재 단계

**공식 공휴일 자료의 첫 수집·서명·게시·두 주소 재조회에 성공했습니다.**
[현재 자료와 조회 시각](https://holidays.mochacookie.com/)에서 상태를 확인할 수 있습니다.
정기 실행은 등록됐으며, 실제 예약 시각에 실행되는지는 이후 실행 기록으로 확인해야 합니다.

- 대상: Cloudflare Pages `mochacookie-holidays`, 무료 정적 파일.
- 사용자 주소: `https://holidays.mochacookie.com` (DNS·HTTPS 확인 완료).
- Actions: 공개 저장소의 표준 macos-15 runner(갱신)·Ubuntu runner(초기 준비 검사)만 사용. 유료 상품·Functions·별도 캐시/아티팩트 저장 없음.
- 갱신: `Update official holidays`가 매일07:15·19:15 KST에 실행되도록 등록돼 있습니다. `Bootstrap Pages`는 실제 자료가 있는 사이트를 덮어쓰지 않습니다.
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

## 자동 갱신과 복구

`Update official holidays`는 매일07:15·19:15(KST)에 공식 자료를 수집하도록 등록했습니다. 첫 수동 실행의 실제 수집/게시/재조회와 확인 커밋은 성공했고, 예약 시각 실행은 아직 관측하지 않았습니다. 공개 표준 macos-15 runner를 사용하며 Mac mini에서 실행하지 않습니다. 첫 게시만 수동 실행에서 `initial_publish=true`를 명시합니다.

수집/전체 페이지·연도/날짜/중복/최소 필수일 검증 → Ed25519 서명 → 후보/번호 원장을 Git에 먼저 반영 → 정적 업로드 → 두 HTTPS 주소의 정확한 파일 재조회 → 확인 원장 커밋 순서입니다. 해당 저장소 전용 임시 GITHUB_TOKEN의contents:write를 사용하고 다른 저장소나 global Git 설정을 바꾸지 않습니다.

공식 내용 변경도 자동 검사 통과 시 게시하는 정책을 소유자가 승인했습니다. 형식 검사는 공식 원본의 모든 법적 오류를 판별하지 않습니다. 실패하면 기존 자료를 보존하고 GitHub 작업 실패로 남깁니다. GitHub 예약의 지연/누락·전체 장애·이메일 미수신은 보장하지 않으며 독립 외부 감시는 아직 없습니다.

기존 pending이 있으면 두 공개 주소와 대조합니다. 이미 게시된 동일 파일은 재게시하지 않고 확인만 저장합니다. 이전 정상본 그대로라면 실패 후보 번호를 재사용하지 않고 새 번호/새 조회로 진행합니다. 공개 주소 불일치나 모르는 파일은 자동 덮어쓰기하지 않습니다. 캐시 내용 버전과 배포 번호는 별개이며 동일 내용의 성공 조회도 확인 시각/서명을 갱신합니다.

준비화면용 `Bootstrap Pages`는 실제 자료가 있는 사이트를 되돌리는 도구가 아닙니다. 실제 운영 자료가 생기면 준비상태 검사에서 차단됩니다. `site`와 `state`는 워크플로가 생성하는 공개 자료이며 매번 이력에 남깁니다. 서명 개인키 복구 사본은 이 공개 저장소에 포함하지 않습니다.

아침결 개발 저장소의 `Deployment/HolidayFeed/site`는 초기 템플릿 사본입니다. 실제 공급 상태와 생성 파일의 원본은 이 공개 저장소의 `site`·`state` 및 Git 이력입니다. 템플릿 사본을 운영 자료 위에 통째로 복사하지 마세요.
