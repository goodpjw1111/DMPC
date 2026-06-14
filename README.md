# DMPC — Dimigo Mock Programming Contest (휴리스틱 채점 플랫폼)

NYPC Rookie Contest 대비용 **휴리스틱(최적화) 온라인 채점 플랫폼**.
`@dimigo.hs.kr` 계정만 허용하며, 첫 공개 배포인 만큼 보안을 1순위로 설계한다.

> 이 문서는 **살아있는 스펙**이다. 결정이 바뀌면 여기부터 고친다.

---

## 1. 핵심 개념

각 모의고사(=problem-set)는 두 파트로 구성되고 **1,000,000점 만점을 2:8로 분배**한다.

| 파트 | 만점 · 총점반영 | 제출 모델 | 채점 |
|---|---|---|---|
| **Step Up** | 1,000,000 · 20% | **출력만 제출** (코드 X). 미션(테스트케이스)별로 입력이 미리 주어지고, 시뮬레이터나 오프라인 최적화로 만든 **출력 결과**를 미션별로 제출. | 체커가 출력→cost 계산, 기준 cost 이하면 만점, 아니면 부분점수. **샌드박스 불필요.** |
| **Challenge** | 1,000,000 · 80% | **코드 제출** (C++ 등 12개 언어). 소스 ≤ **1MB**, 선택적 **`data.bin` ≤ 10MB** 동봉 가능. 입력=stdin·출력=stdout, `data.bin`은 실행 폴더에서 **파일 입출력**으로 읽음. 언어별 입출력 예시(스켈레톤) 자동 제공. | 범위 내 무작위 생성 케이스에 대해 코드를 **샌드박스 실행** → 상대 등수. 매일 09:00·18:00(KST) 중간평가. |

모든 문제는 **최소화**(cost가 낮을수록 좋음).

> **중요**: 코드 실행(샌드박스·12개 언어)은 **Challenge에만** 적용된다. Step Up은 유저가
> 만든 출력 텍스트를 받아 체커로 채점할 뿐이라 코드 실행이 없다 — 공격 표면이 훨씬 작다.
> Step Up 제출 = `(미션/시드 → 출력텍스트)` 묶음; Challenge 제출 = `(언어, 소스 ≤1MB, 선택 data.bin ≤10MB)`.
>
> **Step Up 만점 보장(불변식)**: 각 미션의 기준 `Cost'`는 **반드시 도달 가능한 비용**으로
> 설정한다 — 문제 저자가 *기준 출력(reference solution)*을 제공하고 그 비용을 `Cost'`로 쓴다.
> 그러면 그 출력을 그대로 내면 만점(`min(Cost'/Cost,1)=1`)이 되므로 **만점이 항상 가능**하다.
> 관리자 등록 시 "기준 출력 비용 ≥ 제출 가능한 최적"을 검증한다(불가능한 임계값 금지).
> (예시 문제 `problems/example_clean`는 greedy 출력의 비용을 `Cost'`로 써서 이 불변식을 만족.)

### 대회 일정 (`api/app/schedule.py`, 6/6 테스트)

등록일 `D` → **다음날(D+1) 09:00 KST 시작**, **3일 뒤(D+4) 09:00 KST 종료**(3일짜리).
중간 평가는 기간 내 매일 **09:00·18:00 KST**(총 7회). **마지막 평가 = 종료시각(D+4 09:00)
= 최종 테스트**이며, 이 채점이 끝나면 **최종 등수가 공개**된다(랭킹 탭).
- 제출별 알림: **Challenge**는 "채점 완료 + 샘플 점수 합"을 알림. **Step Up**은
  미션별 개별 제출이고 채점이 즉시·결정적이므로, 알림에 **그 미션의 점수(비용/기준)를 표기**한다
  ("미션 N 채점 완료 · 점수 X · 비용 c/기준 r"). 무효 출력이면 0점으로 표기.

### 확정된 점수 공식 (`judge/scoring.py`에 구현·테스트 완료)

> **점수 통일(만점 1,000,000) + 총점 2:8 가중**: 스텝 업·챌린지 **각각 만점 1,000,000**.
> 최종 총점 = `(2·StepUp + 8·Challenge) / 10` (= 만점 1,000,000). 챌린지의 8은 **마지막으로
> 평가된** 중간 평가 점수(평가 전 0). 구현: `judge/scoring.py:weighted_total`.

**Challenge (케이스별 상대점수, 0~10⁶):**
```
Score_tc = floor( 10^6 · ( 1 − 0.5 · √( (n_lose + 0.5·n_draw) / n_total ) ) )
```
- `n_total` = 제출한 전체 참가자 수, `n_lose` = 나보다 cost가 **낮은(이긴)** 수, `n_draw` = 나와 동점인 수(나 제외).
- 경계: **1등 → 1,000,000 / 꼴찌 → 500,000 하한**(계수 0.5). 단독 참가 → 1,000,000.
- 집계: `Challenge = floor( 1000000 · mean(Score_tc) / 10^6 )` = 케이스 산술평균(만점 1,000,000).
- 무효 결과(TLE/MLE/RE/CE/형식오류)는 **그 케이스만 0점**(50만 하한 미적용). 무효 참가자는 `n_total`에 포함되되 아무도 이기지 못함.

**Step Up (기준 cost 대비 부분점수, 만점 1,000,000):**
```
points = floor( 1000000 · min( Cost' / Cost, 1 ) )
```
- `Cost ≤ Cost'` → 만점. `Cost'=0`이면 `Cost≤0`만 만점. `Cost=0` → 만점(0-division 가드).
- 감쇠는 쌍곡선형(cost 2배 → 점수 절반). 미션이 여러 개면 만점 1,000,000을 계단식으로 분배해 합산.

> 미해결 확인거리는 §8 참고.

---

## 2. 아키텍처

**철칙: 웹/API 티어는 절대 유저 코드를 실행하지 않는다.** 제출은 큐에 적재되고, **별도 호스트의 무상태 워커 풀**이 꺼내 샌드박스에서 실행한 뒤 결과만 기록한다. 큐는 마감/평가시각 5~10배 스파이크의 완충재이기도 하다.

```
브라우저 ─TLS→ Cloudflare(WAF/레이트리밋) ─→ Next.js 웹(인증 게이트 SSR, 인브라우저 시뮬레이터)
                                                  │ REST/JSON
                                                  ▼
                                            FastAPI API ──writes──> PostgreSQL(비공개망, TLS)
                                  (OAuth 검증·소유권 체크·잡 enqueue)   │  세션스토어(Redis)
                                                  │ enqueue            ▼
                                              큐(Redis 또는 PG SKIP LOCKED) ──> 오브젝트 스토리지(R2)
                                                  │ pull  (웹과 절대 동일 호스트 금지)
                                                  ▼
            채점 워커풀 [별도 호스트]: microVM(Firecracker/Kata) → isolate → 유저 코드
              · 컴파일도 샌드박스 내   · 네트워크 차단·읽기전용FS·비루트   · 결과만 좁은 채널로 반환
                                                  ▲
   스케줄러(단일 인스턴스 권장; 멱등 claim) ─┘  09:00/18:00 KST 중간평가 + 종료 시 최종평가 enqueue
   채점 서비스(무상태): raw cost 벡터로 상대 등수·점수 재계산 (각 유저의 *최신* 제출만 MAX/등수에 반영)
```

### 스택

| 레이어 | 선택 | 이유 |
|---|---|---|
| 프론트 | **Next.js (TypeScript)** | 인증 게이트 SSR, 시뮬레이터 모듈과 같은 TS |
| 백엔드 | **FastAPI (Python)** | 채점·생성기·스코어링 로직이 깔끔, CMS/isolate 선례 |
| DB | **PostgreSQL** | 관계 무결성, `SKIP LOCKED` 큐 겸용, JSONB |
| 큐 | **Redis** (초기엔 PG `SKIP LOCKED`로 시작 가능) | 스파이크 완충 |
| 워커 | **isolate (in microVM/gVisor), 별도 호스트** | microVM이 보안 경계, isolate가 실행별 제한 |
| 스토리지 | **Cloudflare R2 (S3 호환)** | 소스·리플레이를 웹루트 밖에 |
| 엣지 | **Cloudflare** | WAF·레이트리밋·TLS, origin은 CF IP만 |

데이터 모델은 `db/schema.sql`. 핵심: **raw 점수를 표시 점수와 분리 저장**(표시/상대 점수는 전체 모집단에 대한 *뷰*), **테스트는 시드만 저장**(입력은 고정 PRNG로 재생성).

---

## 3. 보안 — 단 3가지부터

1. **채점은 별도 호스트 샌드박스, 웹은 코드 실행 안 함** (`judge/sandbox.py`: no-net, CPU+wall, cgroup mem, pids, fsize, open-files, RO-FS, 샌드박스 내 컴파일).
2. **서버측 OAuth 도메인 강제**: ID 토큰의 서명·`iss`/`aud`/`exp`, `email_verified===true`, `hd` 클레임 **및** 파싱한 email 도메인 둘 다 `dimigo.hs.kr`. `hd` *요청 파라미터*는 힌트일 뿐 신뢰 금지. PKCE+state+nonce.
3. **모든 상대-데이터 조회에 기본 거부 소유권 체크 + `contest_ended` 서버 게이트** (UI 플래그 아님). UUID PK로 IDOR 차단.

전체 체크리스트는 `docs/SECURITY.md`(예정). P0: 위 3개 + 시크릿 git 밖. P1: `__Host-` 쿠키·서버세션·세션ID 재생성·CSRF, XSS(리플레이 sandboxed iframe), TLS+HSTS+CSP, 제출 레이트리밋, SSRF 가드. P2: Cloudflare WAF·fail2ban, 관리형 PG 비공개망, 의존성 스캔, 감사로그, **복원 테스트한 암호화 백업**.

---

## 4. 배포/호스팅 — **전액 무료** 구성 (예산 0원 제약)

전부 무료 자원으로, **web↔grader 분리 철칙**과 강한 격리를 유지한다.

| 구성요소 | 무료 자원 | 비고 |
|---|---|---|
| **API + 스케줄러 VM** | **Oracle Cloud Always Free** (AMD micro 또는 ARM Ampere A1) | 평생 무료 VM 2~4대 제공 → web≠grader 분리를 무료로 충족 |
| **Grader 워커 VM** | Oracle Always Free **별도 VM** + **gVisor + isolate** | gVisor는 KVM 없이도(ptrace/systrap) 동작 → 일반 무료 VM에서 강한 격리 |
| **프론트(Next.js)** | Cloudflare Pages / Vercel Hobby (무료) **또는** API VM의 Caddy가 같이 서빙 | |
| **PostgreSQL** | **Neon** 무료(자동 절전·연결 시 깨어남) 또는 Oracle Autonomous DB 무료 | PG를 **큐로 겸용**(`SKIP LOCKED`) → Redis 불필요 |
| **HTTPS/도메인** | **DuckDNS**(무료 서브도메인) + **Caddy**(자동 Let's Encrypt TLS) | 유료 도메인 없이 무료 HTTPS |
| **오브젝트 스토리지** | 소스는 작으니 **Postgres에 저장**(무료) 또는 Cloudflare R2 무료 10GB | |
| **크론(9/18 KST)** | API VM의 systemd timer / APScheduler | 무료 |

**권장 토폴로지 (가장 단순·안전, 무료):**
```
브라우저 ─TLS→ Caddy(VM1, DuckDNS 도메인, 자동 TLS)
                 ├─ /          → Next.js (정적/SSR)
                 └─ /api,/auth → FastAPI (같은 오리진!)   ──→ Neon Postgres(무료, 큐 겸용)
VM2 (별도, 무료): 그레이더 워커 + gVisor + isolate  ← PG 큐에서 잡 pull
```

> **단일 오리진**(Caddy가 프론트와 API를 같은 도메인에서 서빙)이 핵심이다. 그러면
> 쿠키가 first-party가 되어 `SameSite=Lax` + `__Host-`가 그대로 동작하고, CORS가 필요
> 없으며 CSRF 표면이 최소화된다 — 보안 리뷰가 지적한 크로스-오리진 쿠키 문제를 원천 해소.
> (프론트를 Pages/Vercel에 따로 둘 거면 API를 별도 오리진으로 두고 `COOKIE_SAMESITE=none`
> +`COOKIE_SECURE=true`로 전환해야 한다. [docs/DEPLOY.md](docs/DEPLOY.md) 참고.)

비용 0원. 트래픽이 커지면 그때 유료 박스/도메인으로 승급하면 된다.

> **채점기 성능 = NYPC c7a.2xlarge(Zen4 3.7GHz, 2초/1024MB) 등가화**는 별도 문서
> [docs/GRADING_ENV.md](docs/GRADING_ENV.md)에 정리. 요지: 무료로 c7a 실리콘을 24h 못 굴리니
> ①점수 평가는 **AWS Spot c7a.large(학생/신규 크레딧, 월 $0~5)를 평가창에만**, ②완전 무료
> 대안은 **GitHub Actions 공개레포 러너(x86, 일회용 VM)** 로 하되, **벤치마크 기반 시간제한 보정**
> (`DMPC_CALIBRATION_FACTOR`, [judge/calibration.py](judge/calibration.py))으로 *연산 예산*을 공정하게
> 맞춘다. ARM(Oracle A1)은 앱 호스팅용이며 **x86 점수 채점엔 쓰지 않는다**.

---

## 5. 언어 12종 (`judge/languages.py`)

isolate 기반에서 언어는 **설정**(소스명 + 컴파일/실행 커맨드 + 시간·메모리 배수)일 뿐이라, 12종 지원은 *코드 12배가 아니라* 그레이더 이미지에 **툴체인 설치 + 설정 row**가 핵심이다.

**웨이브 롤아웃**: 전부 이미지에 넣되 단계적으로 활성화한다.
- **Wave 1 (활성)**: C++20, C17, Python 3.12 — 파이프라인 검증.
- **Wave 2**: Java, Go, Rust, Node.
- **Wave 3**: C#, Kotlin, Swift, Ruby, PHP.

각 언어는 기준 시간제한에 곱하는 `time_multiplier`(Python ~3x, JVM ~2x 등)와 `memory_extra_mb`를 가진다. `enabled=True`는 해당 툴체인이 워커 이미지에 구워지고 배수가 보정된 뒤에만 켠다.

---

## 6. 리포지토리 레이아웃

```
DMPC/
├─ README.md            ← 이 문서 (살아있는 스펙)
├─ judge/               ← 채점 코어 (프레임워크 독립, Linux 워커에서 사용)
│  ├─ scoring.py        ← 두 점수 공식 + 집계/등수  (테스트 통과)
│  ├─ test_scoring.py   ← 경계값 22케이스
│  ├─ languages.py      ← 지원 언어 12종 설정
│  ├─ sandbox.py        ← isolate 러너 (no-net·자원제한·샌드박스 컴파일·보정 적용)
│  ├─ calibration.py    ← c7a 대비 시간제한 보정 factor (11/11 테스트)
│  ├─ calib_kernel.cpp  ← 보정용 C++ 벤치마크 (c7a·그레이더에서 측정)
│  ├─ registry.py       ← 문제 로더 (problems/<key>/problem.py)
│  ├─ grader.py         ← Step Up 채점 서비스 (출력→체커→점수, 샌드박스 불필요; 8/8)
│  ├─ challenge_grader.py ← Challenge 파이프라인 (생성→실행(주입)→체커→cost; 4/4)
│  ├─ standings.py      ← 상대채점·랭킹 재계산 (per-case 상대점수→총점·등수; 7/7)
│  ├─ round_scoring.py  ← 라운드 결과 순수 조립 (case_results + standings; eps; 8/8)
│  └─ eval_round.py     ← 평가 라운드 idempotent 키 + 비공개 시드 파생 (7/7)
├─ db/
│  └─ schema.sql        ← PostgreSQL 데이터 모델 (UUID PK, 시드 저장, 감사로그)
├─ api/                 ← FastAPI: 도메인 제한 OAuth·보안세션·CSRF·소유권 가드
│  ├─ app/oidc.py       ←   서버측 hd+email_verified+email도메인 검증 (12/12 테스트)
│  ├─ app/sessions.py   ←   서버 세션스토어·__Host- 쿠키·세션ID 재생성·CSRF
│  ├─ app/security.py   ←   보안헤더·CSP·CSRF 미들웨어
│  ├─ app/deps.py       ←   기본거부 소유권 체크·contest_ended 게이트
│  ├─ app/grading.py    ←   Step Up 제출 서비스(채점→기록→알림; 가짜 conn 5/5)
│  ├─ app/round_service.py ← 멱등 라운드 생성 + 대회 status 전이 (8/8)
│  ├─ app/schedule.py   ←   대회 일정 규칙 09·18 KST (6/6)
│  ├─ app/routers/      ←   auth·me·contests(+my-eval)·submit (22 routes)
│  └─ seed_example.py   ←   예시 라이브 대회 + Step Up 문제 시드
├─ web/                 ← Next.js 프론트 (로그인·대시보드; 시뮬레이터는 Phase 3)
├─ worker/             ← 그레이더 (sample 큐 컨슈머 worker.py + 라운드 평가 grade_round.py
│                         + 스케줄러 scheduler.py + 공유 sandbox_runner.py; Linux+isolate)
├─ deploy/Caddyfile     ← 단일 오리진 리버스 프록시(무료 배포, 자동 TLS)
├─ scripts/run_tests.sh ← 백엔드 테스트 138개 일괄 실행
├─ .github/workflows/   ← ci.yml(테스트+웹빌드 게이트) · grade.yml(무료 그레이더)
├─ docs/
│  ├─ DEPLOY.md         ← Google OAuth 설정·로컬/운영 실행 단계
│  └─ GRADING_ENV.md    ← c7a.2xlarge 무예산 등가화 전략(보정·스팟·GH Actions)
├─ .github/workflows/grade.yml  ← 무료 x86 그레이더(GitHub Actions, 일회용 VM) 스켈레톤
└─ docker-compose.yml   ← postgres(스키마 자동적용)·redis·api·web (+grader 프로파일)
```

---

## 7. 로컬 개발

전체 실행 단계는 [docs/DEPLOY.md](docs/DEPLOY.md) 참고. 빠른 시작:
```bash
# 프론트만 빠르게 (백엔드/Postgres 불필요 — mock 모드로 전 기능 동작)
cd web && npm install && npm run dev         # http://localhost:3000

# 풀스택 (Docker): postgres가 db/schema.sql 자동 적용
docker compose up postgres redis api web     # web :3000  api :8000/healthz

# 백엔드 테스트 전체 (의존성 없이 동작) — 총 138
bash scripts/run_tests.sh
#   scoring26 · calibration11 · grader8(StepUp) · standings7(상대채점/랭킹)
#   · eval_round7(라운드 시드) · challenge_grader5 · round_scoring9(라운드 조립)
#   · oidc13 · prod9 · nickname8 · schedule6 · grading_service5
#   · round_service8(라운드 생성/status) · grade_round10(라운드 드라이버) · pipeline6
```

> `worker/`(그레이더)는 **Linux + isolate** 필요 → `docker compose --profile grader up worker`
> (운영에선 web/api와 **다른 호스트**, microVM/gVisor). Windows 개발 시 WSL2 사용.
> OAuth 로그인을 실제로 쓰려면 Google OAuth 클라이언트(id/secret)가 필요 — DEPLOY.md §1.

---

## 8. 미해결 확인거리 (코딩 전 잠그기)

- [x] Challenge 공식 = 보정식(0.5 캡, 꼴찌 50만 하한). **확정.**
- [ ] `n_lose`는 strict `<` (동점은 `n_draw`로만) — 기본 채택, 확정 필요.
- [ ] 부동소수 cost 동점 epsilon 값.
- [ ] 무효 참가자를 `n_total`에 포함할지(기본 포함) / 미제출자 제외(기본 제외).
- [ ] Step Up `Cost'` 기준값 설정 주체·방식(관리자 패널).
- [ ] 집계 = 산술평균(기본) vs 합/가중, K가 참가자별 동일한지.
- [ ] 제출 쿨다운(기본 권장 ≥1분/문제), best-score-kept 정책.

---

## 9. 로드맵

- **Phase 0** — 기반: 도메인 제한 OAuth·보안세션·CSRF·소유권 가드·UUID·스키마·docker-compose **✅ 스캐폴드 완료**. 남음: CI, 관리자 UI, Google 클라이언트 연결 후 E2E 로그인 검증.
- **Phase 1** — Step Up MVP: 제출→샌드박스 샘플채점→점수→이력→알림. 
- **Phase 2** — Challenge: 시드 생성기·09/18시 KST 크론·상대채점·비공개 등수.
- **Phase 3** — 인브라우저 시뮬레이터(문제별 플러그형, 방향키·z, Step Up 자동출력 / Challenge 수동).
- **Phase 4** — 종료/리플레이: 최종평가·등수공개·top-3 리플레이(검증·모더레이션)·시상.
- **Phase 5** — 강화/스케일: Cloudflare·CSP·모니터링·백업복원·부하테스트·감사로그.
```
이번 주: ① Phase 0 인증  ② 그레이더 격리 경계를 hello-world로 증명  ③ §8 확정.
```
