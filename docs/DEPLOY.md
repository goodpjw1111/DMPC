# DMPC — 실행 & 배포 가이드

## 1. Google OAuth 클라이언트 만들기 (필수)

1. [Google Cloud Console](https://console.cloud.google.com/) → 프로젝트 생성.
2. **APIs & Services → OAuth consent screen**:
   - 이 프로젝트가 **dimigo.hs.kr Workspace 소유**라면 User type을 **Internal**로
     설정하세요. 그 자체로 조직 외 계정이 차단되어 **방어가 한 겹 더** 생깁니다.
     (그래도 서버측 `hd`+email 검증은 그대로 유지 — 다중 방어.)
3. **Credentials → Create Credentials → OAuth client ID → Web application**:
   - **Authorized redirect URIs**에 정확히 추가:
     - 개발: `http://localhost:8000/auth/callback`
     - 운영: `https://<api-도메인>/auth/callback`
   - 생성된 **Client ID / Client secret**를 `api/.env`에 넣습니다.

> 리다이렉트 URI는 `GOOGLE_REDIRECT_URI`와 **글자 단위로 동일**해야 합니다.

## 2. 환경변수

```bash
cp api/.env.example api/.env
# SECRET_KEY 생성:
python -c "import secrets; print(secrets.token_urlsafe(48))"
# -> api/.env 의 SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET 채우기
cp web/.env.example web/.env.local
```

## 3. 로컬 실행 (Docker)

```bash
docker compose up postgres redis api web
# Postgres 최초 기동 시 db/schema.sql 이 자동 적용됩니다.
# web:  http://localhost:3000   api: http://localhost:8000/healthz
```

채점 워커(그레이더)는 Linux + isolate가 필요해 기본 기동에서 제외됩니다:
```bash
docker compose --profile grader up worker   # Linux 호스트에서만
```

## 4. 로컬 실행 (Docker 없이)

```bash
# Postgres (로컬) 에 스키마 적용
psql "postgresql://dmpc:dmpc@localhost:5432/dmpc" -f db/schema.sql

# API
cd api && python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Web (다른 터미널)
cd web && npm install && npm run dev
```

### 4-1. 프론트 ↔ 실 API 연동 테스트 (목 기본 / 옵트인)

웹은 **기본이 목(localStorage) 데모**라 백엔드 없이도 그대로 돈다. 실제 API에 붙여
보려면 브라우저 콘솔에서 한 줄로 켠다(빌드 불필요):

```js
localStorage.setItem('dmpc_api','1'); location.reload();   // 끄기: removeItem 후 새로고침
```

- 켜지면 세션이 없을 때 **로그인 게이트**가 뜨고 `/auth/login`(구글 OIDC)으로 보낸다.
- OAuth 클라이언트를 아직 안 만들었으면 **dev-login**으로 우회한다(개발 전용, `ENV=prod`면 404):
  ```bash
  curl -i -c jar.txt -H 'Content-Type: application/json' \
    -d '{"email":"tester@dimigo.hs.kr"}' http://localhost:8000/auth/dev-login
  ```
  같은 도메인(`ALLOWED_EMAIL_DOMAIN`) 계정만 통과한다. 응답 쿠키로 `/api/me`→닉네임 설정→
  `/api/contests`까지 흐른다.
- 연동된 화면(실데이터): 대회 목록·**상세 점수/총점(2:8)**·문제 지문·시뮬레이터·**스텝업/챌린지
  제출**(챌린지는 multipart + data.bin)·**제출 내역**·**중간 평가(본인 per-case 점수/등수)**·
  **최종 랭킹(standings)**·**알림(폴링 + 읽음 처리)**·**관리자 대회 저작**(`/create`). 모두 옵트인
  시에만 API를 호출하고, 끄면 목 데모로 즉시 복귀한다.
- **관리자 저작**(`POST /api/admin/contests`, 어드민만): **파라미터 청소로봇 생성기**(`clean_robot`)
  위에 제목·지문·시드·**격자/먼지 범위**·시간/메모리·챌린지 시드범위/라운드/cost_eps를 얹어 **예약 대회**로
  저장(일정 규칙 적용). 저작 시드/범위는 `scoring_config` → `effective_meta`로 채점·입력 생성에 반영돼
  **즉시 채점 가능**. `clean_robot.generate`는 프론트 `genClean`의 **bit-exact 포팅**이라 시뮬레이터·
  미리보기 격자 = 서버 채점 격자. 관리자 승격은 §5. 커스텀 생성기 **코드**(관리자 코드 실행)는 샌드박스 필요 — 후속.
- **리플레이/시상**(종료 대회): 최종 **상위 3위**가 풀이를 작성·공유(`POST /api/contests/{cid}/replay`),
  관리자가 승인(`/api/admin/replays/{rid}/moderate`)하면 공개. 랭킹 탭에 **시상대**(상위 3위) + 공개 풀이.
  풀이 본문은 보안상 **서식 없는 텍스트로 이스케이프 렌더**(공유 글의 XSS 차단; 모더레이션은 큐레이션용).
- **대회 등록(참가)**: 진행/예정 대회에 참가 신청(`POST /api/contests/{cid}/register`)·취소(`/unregister`)로
  참가자 로스터 집계(`registrations` 테이블). 제출은 등록과 무관하게 열려 있음(등록=의사표시·명단). 종료 후 변경 불가.
- 미연동(후속): 커스텀 생성기 코드(샌드박스 실행)·리플레이 마크다운(안전한 렌더러).

## 5. 첫 관리자 지정

**부트스트랩(권장, SQL 불필요)**: `ADMIN_EMAILS`(쉼표구분)에 적힌 이메일은 **로그인 시 자동으로
`admin`으로 승격**된다(멱등 — 매 로그인 적용, **강등은 없음**). 기본값은 `goodpjw2008@dimigo.hs.kr`.
다른 운영자를 쓰려면:
```bash
ADMIN_EMAILS=you@dimigo.hs.kr,other@dimigo.hs.kr   # .env / 배포 환경변수
```
해당 계정으로 한 번 로그인(또는 dev-login)하면 `/create`가 열리고 `POST /api/admin/contests`가 통한다.

**수동 승격(대안)**: 이미 로그인한 유저를 직접 올릴 때:
```sql
UPDATE users SET role='admin' WHERE email='you@dimigo.hs.kr';
```
(부트스트랩은 강등하지 않으므로, 목록에 없어도 수동 승격된 admin은 유지된다.)

## 6. 운영 전 체크 (README §3 보안 체크리스트와 함께)

- [ ] `APP_ENV=prod`, `COOKIE_SECURE=true` (→ Secure 쿠키 + `__Host-` + HSTS 자동).
- [ ] CSP를 한동안 `CSP_REPORT_ONLY=true`로 운영 후 위반 0 확인 → `false`로 강제.
- [ ] Cloudflare 뒤에 두고 origin 방화벽을 CF IP 대역만 허용.
- [ ] PostgreSQL은 관리형 + 비공개망 + 강제 TLS + 최소권한 유저.
- [ ] **그레이더 워커는 web/api와 다른 호스트**, microVM(Firecracker/Kata) 또는 gVisor.
- [ ] 시크릿은 `.env` (git 제외) 또는 시크릿 매니저, OAuth secret 주기적 회전.
- [ ] 암호화 백업 + **복원 테스트** 완료.

## 7. 테스트

```bash
python judge/test_scoring.py            # 채점 공식 25/25
python api/tests/test_oidc_claims.py    # 도메인 게이트 13/13
python api/tests/test_prod_config.py    # prod 부팅 가드 9/9
python problems/example_clean/test_pipeline.py   # 예시 채점 6/6
```

## 8. 무료 배포 (0원) — 권장 단계

전부 무료 자원(README §4). web↔grader 분리 유지.

1. **Oracle Cloud Always Free** 가입 → VM 2대 생성 (둘 다 무료):
   - VM1: API + 스케줄러 + (선택) Caddy로 프론트 동시 서빙.
   - VM2: 그레이더 — `gVisor(runsc)` + `isolate` 설치(둘 다 KVM 불필요).
2. **Neon**(무료 Postgres) 프로젝트 생성 → `DATABASE_URL` 획득, `db/schema.sql` 적용.
   PG가 큐를 겸하므로 Redis는 불필요.
3. **DuckDNS**(무료 서브도메인) + VM1에 **Caddy** → 자동 HTTPS.
   Caddy가 `/`→Next, `/api`·`/auth`→FastAPI로 **단일 오리진** 리버스 프록시.
4. 프론트를 따로 Cloudflare Pages/Vercel에 둘 경우에만 별도 오리진 모드:
   `COOKIE_SAMESITE=none` + `COOKIE_SECURE=true` 로 전환.
5. `APP_ENV=prod` 설정 — 시크릿/HTTPS/CSP가 미설정이면 **부팅이 거부**되어 안전.
6. 크론(09:00·18:00 KST)은 VM1의 systemd timer.

> 쿠키 모드 결정: **단일 오리진(권장)** → `SameSite=Lax`+`__Host-` 그대로 OK, CORS 불필요.
> **별도 오리진** → `SameSite=None;Secure` 필수(보안 리뷰 반영).

## 9. 업로드 전 체크리스트 + 프로덕션 런북

### 업로드 전 체크리스트
- [ ] `bash scripts/run_tests.sh` 전부 통과 (CI [.github/workflows/ci.yml] 동일하게 게이트)
- [ ] 레포에 시크릿 없음 — `api/.env` 미커밋(.gitignore), `gitleaks` 스캔, OAuth secret 회전
- [ ] **prod 부팅 가드 통과**: `APP_ENV=prod`면 SECRET_KEY(32+)·COOKIE_SECURE·OAuth·CSP(enforce)·non-localhost 미설정 시 **부팅 거부** ([api/app/_prodcheck.py](../api/app/_prodcheck.py))
- [ ] Google OAuth: 동의화면 Internal(가능 시), redirect URI = `https://<운영도메인>/auth/callback`
- [ ] DB 스키마 적용(`db/schema.sql`) + `seed_example.py`(선택) + 첫 관리자 승격(§5)
- [ ] 그레이더 호스트: isolate + microVM/gVisor, `DMPC_CALIBRATION_FACTOR` 측정([GRADING_ENV.md](GRADING_ENV.md) §4)
- [ ] 암호화 백업 + **복원 테스트** 완료

### 프로덕션 런북 (무료 티어)
1. **VM1 (앱·Oracle Always Free)**: 레포 클론 → `api/.env` 작성 → 스키마 적용 → `uvicorn app.main:app`(:8000) + `npm run build && npm start`(:3000) → **Caddy([deploy/Caddyfile](../deploy/Caddyfile), DuckDNS)** 가 단일 오리진으로 프록시 + 자동 TLS.
2. **DB**: Neon 무료 Postgres → `DATABASE_URL`.
3. **VM2 (그레이더·별도 호스트)**: isolate + 12개 툴체인(또는 GitHub Actions 그레이더, [.github/workflows/grade.yml](../.github/workflows/grade.yml)), `worker/worker.py`를 `DATABASE_URL` + `DMPC_CALIBRATION_FACTOR`로 실행, gVisor/microVM 내부.
4. **점수 평가**: 평가창에만 AWS Spot **c7a.large**(학생/AWS 크레딧)로 동일 환경 확보 — GRADING_ENV.md.
5. **CI**: 푸시/PR마다 GitHub Actions가 테스트+웹 빌드 게이트.

### 운영 주의 (백엔드 리뷰 반영)
- **스키마는 최초 1회만 자동 적용**: `db/schema.sql`은 Postgres 최초 init에서만 실행됨.
  이후 컬럼 추가/변경은 자동 반영되지 **않으므로** 운영 중 변경은 수동 `ALTER`로 적용.
  (`schema.sql`은 `CREATE` 위주라 라이브 DB에 재실행하면 에러. 대회 중 스키마 변경 지양.)
- **그레이더 box-id는 프로세스당 유일**: `isolate` 박스는 동시 공유가 안전하지 않음.
  워커는 프로세스당 1개의 `ISOLATE_BOX_ID`를 점유. 늘리려면 `ISOLATE_BOX_ID=0..N-1`로
  **여러 프로세스**를 띄울 것(한 프로세스에서 동시 실행 금지).
- **워커 리스/복구**: 제출은 클레임 시 `claimed_at` 리스를 잡고, `WORKER_LEASE_TIMEOUT_S`
  (기본 300s) 초과로 멈춘 행은 주기적으로 **자동 재큐**됨(워커 크래시/재부팅 복구). 인프라
  실패(`INTERNAL`)는 점수화하지 않고 `WORKER_MAX_ATTEMPTS`(기본 5)까지 재큐.
- **레이트리밋은 프로세스-로컬**: API를 **단일 워커**로 띄우거나 Caddy/Cloudflare 엣지
  레이트리밋 뒤에 둘 것. 멀티 uvicorn 워커면 인증 버킷(10/min)이 프로세스 수만큼 곱해짐.

### 평가 라운드 & 스케줄러 (Phase 2 코어)
- **`EVAL_SEED_SECRET` (그레이더 호스트 필수)**: 라운드별 **비공개 시드**를 결정적으로
  파생(`derive_seeds`)하는 서버 비밀. **API 쿠키 서명키(SECRET_KEY)와 다른 비밀**을 쓸 것
  (트러스트 도메인 분리). 미설정 시 Challenge 라운드는 `status='failed'`로 안전 실패.
  생성: `python -c "import secrets;print(secrets.token_urlsafe(48))"`.
- **스케줄러**: `worker/scheduler.py`가 (1)대회 status `scheduled→live→ended` 전이,
  (2)09/18 KST 라운드 생성(멱등, 다운타임 후 catch-up), (3)멈춘 라운드 복구(리스),
  (4)due 라운드를 `grade_round.evaluate_round`로 로컬 채점. systemd timer(5분 간격) 또는
  `docker compose --profile grader up scheduler`. 워커와 **다른 `ISOLATE_BOX_ID`** 필수.
- **라운드 멱등성**: 모든 선택은 `scheduled_at` as-of 컷오프(`created_at <= scheduled_at`)로
  고정 → 늦게/재실행돼도 동일 결과. 라운드당 트랜잭션 1개(DELETE→INSERT)로 부분쓰기 방지.
  isolate INTERNAL(인프라)은 점수화 금지 → 라운드 abort/failed 후 재실행(상대채점 n_total
  오염 방지). 최종 랭킹은 `type='final' AND status='done'` 라운드만 `/standings`에 공개.
- **중간평가 자기조회**: `GET /api/contests/{cid}/my-eval`은 **본인 데이터만**(per-case
  점수/등수+총점/등수) 반환 → 대회 중에도 열람 가능, 상대는 종료 전까지 비공개 유지.
- **Challenge 문제 `scoring_config` 규약(jsonb)**: `{"seed_range":[lo,hi], "round_seeds":K,
  "cost_eps":0.0, "params":{...}}`. `round_seeds`=라운드당 비공개 케이스 수(기본 20),
  `cost_eps`=부동소수 비용 동점 허용오차(정수 비용이면 0). Step Up은 `{"cost_ref":...}`.

### 백엔드 구조 (요약)
- **웹/API 티어는 유저 코드 미실행**. Step Up 채점은 API(`judge/grader.py` — 샌드박스 불필요). Challenge 실행은 **별도 그레이더**(`judge/sandbox.py` isolate, `judge/challenge_grader.py` 파이프라인).
- **상대채점·랭킹**: `judge/standings.py`(per-case 상대점수→Challenge 점수→총점·등수) + `judge/round_scoring.py`(라운드 결과 순수 조립: case_results + standings). **라운드 평가**: `worker/grade_round.py`(DB+샌드박스 드라이버: claim→as-of rep선택→비공개 시드→채점→트랜잭션 기록) + `api/app/round_service.py`(멱등 라운드 생성·status 전이) + `worker/scheduler.py`(09·18 KST 러너·catch-up·복구). **평가 라운드 정체성**: `judge/eval_round.py`(idem 키 + 비공개 시드 파생) + `api/app/schedule.py`. **시간보정**: `judge/calibration.py`.

---

## 10. 무료 24시간 구동 — 한 박스 (Oracle Always Free, 0원 영구)

가장 단순한 24/7 절차. **한 대의 무료 VM**에서 `docker-compose.prod.yml`(루트)로 전 서비스를
띄운다. 모두 영구 무료: Oracle Always Free(상시) + DuckDNS(서브도메인) + Let's Encrypt(Caddy 자동 TLS) + Google OAuth.

> 채점 공정성: Oracle 무료의 큰 티어는 **ARM(Ampere A1)** 이다. **한 박스에서 모두 같은 CPU로
> 돌면 참가자 상대 채점은 공정**하다(내부 일관성). NYPC c7a(x86)와 **정확히** 맞추려면 평가창에만
> x86 그레이더를 붙인다(§8 · GRADING_ENV.md). 교내 모의고사는 ARM 단일 박스로 충분.

### 10-1. 사전 준비 (모두 무료, 5분)
1. **Oracle Cloud Always Free** 가입 → Compute 인스턴스 1대 생성: **Ampere A1 (ARM), Ubuntu 22.04,
   ~4 OCPU / 24 GB**(무료 한도 내). 공인 IP 발급.
2. 인스턴스 보안: VCN **Security List**(또는 NSG)에서 **TCP 80, 443** 인그레스 허용. VM 안에서도:
   `sudo ufw allow 80,443/tcp && sudo ufw allow OpenSSH && sudo ufw enable`.
3. **DuckDNS**(duckdns.org, Google 로그인) → 서브도메인 1개 생성(예 `dmpc.duckdns.org`) →
   현재 IP를 인스턴스 공인 IP로 지정.
4. **Google OAuth**(§1): 승인된 **리디렉션 URI = `https://dmpc.duckdns.org/auth/callback`**.

### 10-2. VM 세팅 + 배포 (복붙)
```bash
# Docker + compose 플러그인
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

git clone <레포> dmpc && cd dmpc

# 1) api/.env 작성 (PROD 값). 시크릿 2개 생성:
python3 -c "import secrets;print('SECRET_KEY='+secrets.token_urlsafe(48))"
python3 -c "import secrets;print('EVAL_SEED_SECRET='+secrets.token_urlsafe(48))"
cp api/.env.example api/.env && nano api/.env   # 아래 PROD 값으로 채움

# 2) DOMAIN / DB 비밀번호 (compose가 읽는 .env)
printf 'DOMAIN=dmpc.duckdns.org\nPOSTGRES_PASSWORD=%s\n' "$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" > .env

# 3) 기동 — 사이트가 바로 LIVE (Caddy가 자동으로 HTTPS 발급)
docker compose -f docker-compose.prod.yml up -d --build
```
`api/.env`의 PROD 값(나머지는 §2/§5 그대로):
```
APP_ENV=prod
API_BASE_URL=https://dmpc.duckdns.org
WEB_ORIGIN=https://dmpc.duckdns.org
GOOGLE_REDIRECT_URI=https://dmpc.duckdns.org/auth/callback
COOKIE_SECURE=true
CSP_REPORT_ONLY=false
SECRET_KEY=<위에서 생성>
EVAL_SEED_SECRET=<위에서 생성>
ADMIN_EMAILS=goodpjw2008@dimigo.hs.kr
GOOGLE_CLIENT_ID=...   GOOGLE_CLIENT_SECRET=...
```
이때 **web+api+postgres+caddy** 가 뜬다 → `https://dmpc.duckdns.org` 접속 → **구글 로그인**(@dimigo.hs.kr) →
**스텝 업**(API가 즉시 채점, 샌드박스 불필요)·**대회 저작**·**참가 신청**·**랭킹**이 모두 동작. (스키마는
postgres 최초 init에서 자동 적용.)

### 10-3. Challenge 채점 + 09/18 평가 켜기
```bash
docker compose -f docker-compose.prod.yml --profile grader up -d --build
```
**worker**(샘플 비용 채점) + **scheduler**(대회 status 전이 · 매일 09·18 KST 평가 · 상대 랭킹 산정)가
추가된다. isolate는 `privileged`로 동작. ※ 한 박스에서 **워커/스케줄러는 서로 다른 `ISOLATE_BOX_ID`**
(compose에 0/1로 분리됨). 더 늘리려면 §9 "운영 주의" 참고.

### 10-4. 관리자 + 운영
- `goodpjw2008@dimigo.hs.kr`로 한 번 로그인하면 `ADMIN_EMAILS` 부트스트랩으로 **자동 admin** →
  우상단 `+ 새 모의고사`(`/create`)에서 대회를 만든다(등록일 기준 **D+1 09:00 시작·3일**).
- **24/7 유지**: 모든 서비스 `restart: unless-stopped` + Oracle VM 상시 가동(요금 0). 업데이트는
  `git pull && docker compose -f docker-compose.prod.yml --profile grader up -d --build`.
- **상태/로그**: `docker compose -f docker-compose.prod.yml ps` · `... logs -f api caddy scheduler`.
- **백업(무료)**: `docker compose -f docker-compose.prod.yml exec postgres pg_dump -U dmpc dmpc | gzip > backup.sql.gz`
  를 cron으로. (Neon 무료 PG를 쓰면 `DATABASE_URL`만 바꾸고 postgres 서비스는 빼도 된다.)
- **사전 점검**: 업로드 전 `bash scripts/run_tests.sh`(전부 통과) + §9 체크리스트.

---

## 11. 카드(신용카드)가 없을 때 — 무카드 무료 24/7

Oracle/AWS/GCP는 **본인확인용 카드**가 필요하다. 카드 없이 24/7 돌리는 검증된 두 길:

> **열쇠 = GitHub Student Developer Pack**: `@dimigo.hs.kr`로 신청(education.github.com/pack,
> 학생증/재학증명서로 인증)하면 **카드 없이** ▶**Microsoft Azure $100 크레딧**(Azure for Students)
> ▶**Namecheap 무료 도메인 1년(.me)** ▶기타. 아래 두 경로 모두 이걸로 해결된다.

### 11-A. Azure for Students (무카드, 클라우드 VM, 권장)
`@dimigo.hs.kr`로 **Azure for Students** 가입(azure.microsoft.com/free/students) — **카드 불필요**,
학생 인증만($100/년, 재학 중 갱신). 그 다음은 **§10과 100% 동일** — Oracle 대신 Azure VM일 뿐:
1. Azure Portal → **Virtual Machine** 생성: **Ubuntu 22.04**, 크기 **B1s/B2s**(저렴 → 크레딧으로
   ~1년), 공인 IP. 네트워크 보안 그룹에서 **22, 80, 443** 인바운드 허용.
2. SSH 접속 → **§10-2부터 그대로**(Docker 설치 → `api/.env` → `.env`(DOMAIN/PW) →
   `docker compose -f docker-compose.prod.yml up -d --build`). 도메인은 DuckDNS(무료) 또는
   Student Pack의 Namecheap `.me`(무료). Caddy가 자동 HTTPS.
3. Challenge 채점은 `--profile grader` 추가(§10-3). 관리자·운영은 §10-4.
> $100 크레딧은 "영원히 무료"는 아니지만 B1s 기준 ~1년+, 재학 중 매년 갱신이라 교내 대회엔 충분.

### 11-B. 자체 호스팅 + Cloudflare Tunnel (영구 $0, 카드·클라우드 불필요)
**상시 켜둘 수 있는 기기**(집 PC·학교 서버·라즈베리파이)만 있으면 클라우드도 카드도 필요 없다.
**Cloudflare Tunnel**(무료, 무카드)이 공개 HTTPS 엣지를 제공하고, 기기에서 **바깥으로** 연결하므로
**포트 개방·공인 IP·NAT 설정이 전혀 필요 없다**.
1. 도메인 1개를 Cloudflare에 추가(무료) — Student Pack의 Namecheap `.me`(무료)를 Cloudflare DNS로.
2. **Cloudflare Zero Trust**(무료) → *Networks ▸ Tunnels ▸ Create a tunnel* → Public hostname =
   `dmpc.<도메인>`, **Service = `http://caddy:80`** → 발급된 **토큰** 복사.
3. 기기에서 (Linux + Docker):
   ```bash
   git clone <레포> dmpc && cd dmpc
   cp api/.env.example api/.env && nano api/.env     # §10 PROD 값, URL은 https://dmpc.<도메인>
   printf 'POSTGRES_PASSWORD=%s\n' "$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" > .env
   DOMAIN=:80 TUNNEL_TOKEN=<2번 토큰> \
     docker compose -f docker-compose.prod.yml -f docker-compose.tunnel.yml --profile grader up -d --build
   ```
   `DOMAIN=:80` → Caddy는 평문 HTTP로 경로 라우팅(/api,/auth→api, 그 외→web), **TLS는 Cloudflare가
   종단**. `cloudflared`가 터널로 노출. `api/.env`는 `COOKIE_SECURE=true`(브라우저는 HTTPS) +
   `TRUSTED_IP_HEADER=cf-connecting-ip` 권장.
4. `https://dmpc.<도메인>` 접속 → 동작 동일. 기기를 끄지 않는 한 24/7, **요금 0원·카드 0**.
> 라즈베리파이 등 ARM이면 §10 상단의 "ARM 단일박스=내부 상대채점 공정" 참고.

### 11-C. (참고) DB만 분리 — Neon 무료 Postgres
어느 경로든 로컬 Postgres 대신 **Neon**(무료, 무카드)을 쓰려면: Neon에서 프로젝트 생성 →
`db/schema.sql` 적용 → `api/.env`의 `DATABASE_URL`을 Neon 것으로 → compose에서 `postgres` 서비스와
그 `DATABASE_URL` override만 제거. (앱·그레이더는 동일.)

### 11-D. Vercel / Render 는? — 전체 스택엔 부적합 (이유)
**결정타: isolate 그레이더는 `privileged` 리눅스(cgroups)가 필요** → 두 플랫폼 모두 불가(챌린지 채점 불가).
- **Vercel**: Next.js **프론트엔드엔 최고**(무료·무카드·24/7·빠름). 그러나 API는 **서버리스 함수**라
  영속 프로세스 가정이 깨진다 — 인메모리 **레이트리밋/JWKS 캐시**가 인스턴스마다 분리, 실행시간 제한,
  **백그라운드 워커/스케줄러·Postgres 불가**. Step Up 인라인 채점조차 함수로 욱여넣어야 함.
- **Render**: 영속 서버는 되지만 무료 웹은 **15분 유휴 후 슬립**(24/7 아님), 무료 Postgres는 **90일 후 삭제**,
  **백그라운드 워커는 유료**, **privileged/isolate 불가**, 무료 크론 제약. → 09/18 평가·챌린지 채점 불가.

**언제 쓸 만한가 (하이브리드)**: `web=Vercel`(빠른 프론트) + `api=작은 VM 또는 자체호스팅 터널`(§11-A/B)
+ `grader=GitHub Actions`([.github/workflows/grade.yml](../.github/workflows/grade.yml), 무료·일회용 VM=샌드박스)
+ `DB=Neon`. 단 프론트/백엔드가 **다른 오리진**이 되므로 쿠키를 `SameSite=None;Secure`(§4 별도오리진 모드)로
전환해야 하고, 약간의 배선이 필요. **가장 단순·견고한 무카드 24/7은 여전히 단일 박스(§11-A Azure / §11-B 터널).**

---

## 12. 카드·기기 없이 챌린지까지 — Vercel + Render + Neon + GitHub Actions

**무료·무카드·무기기**로 **챌린지(코드 채점)까지** 돌리는 유일한 길. 4조각이지만 **브라우저는 오직
Vercel 도메인 하나만** 본다(Vercel이 `/api`·`/auth`를 Render로 프록시 → 쿠키 first-party, §4 단일오리진).
이미 산출물(`render.yaml`, `.github/workflows/{grade-samples,evals,keepalive}.yml`)이 들어있다.

| 조각 | 무료 서비스 | 역할 |
|---|---|---|
| web | **Vercel** Hobby | Next.js + `/api`·`/auth`를 Render로 프록시 |
| api | **Render** Free | FastAPI(세션·제출·읽기·Step Up 즉시채점) |
| DB | **Neon** Free | Postgres(큐 겸용) |
| 그레이더·평가 | **GitHub Actions** | 챌린지 샘플채점 + 09/18 평가(일회용 VM=샌드박스, isolate) |

### 12-1. 준비
1. 이 레포를 **PUBLIC** GitHub 레포로 push(공개 레포라야 Actions 무제한 무료). `goodpjw1111@gmail.com`이
   **로그인 허용+관리자**로 이미 들어가 있음(§5, `ALLOW_EMAILS`/`ADMIN_EMAILS`).
2. 시크릿 2개 생성: `python -c "import secrets;print(secrets.token_urlsafe(48))"` 를 두 번 →
   `SECRET_KEY`, `EVAL_SEED_SECRET`.

### 12-2. Neon (DB)
neon.tech 가입(무카드) → 프로젝트 생성 → **SQL Editor**에 `db/schema.sql` 전체를 붙여 실행 →
연결 문자열(`postgresql://...?sslmode=require`)을 `DATABASE_URL`로 메모.

### 12-3. Render (API)
render.com 가입(무카드) → **New ▸ Blueprint** → 이 레포 선택(`render.yaml` 자동 인식) → 대시보드에서
`sync:false` 값 채움: `DATABASE_URL`(Neon), `SECRET_KEY`, `EVAL_SEED_SECRET`, `GOOGLE_CLIENT_ID/SECRET`,
그리고 **URL 3개는 일단 비워두고** 배포 → 발급된 주소 `https://dmpc-api.onrender.com` 메모.

### 12-4. Vercel (Web)
vercel.com 가입(무카드) → **Add New ▸ Project** → 이 레포 import → **Root Directory = `web`** →
Environment Variables에 추가:
```
NEXT_PUBLIC_API = 1                                  # 실 API 모드(목 아님)
API_PROXY_TARGET = https://dmpc-api.onrender.com     # next.config가 /api,/auth를 여기로 프록시
```
배포 → 발급된 주소 `https://<your-app>.vercel.app` 메모.

### 12-5. URL 교차 연결(중요) + Google OAuth
- **Render** 대시보드에서 세 값을 **Vercel 주소**로 채움(저장하면 자동 재배포):
  `WEB_ORIGIN=https://<your-app>.vercel.app` · `API_BASE_URL=` 동일 ·
  `GOOGLE_REDIRECT_URI=https://<your-app>.vercel.app/auth/callback`.
- **Google Cloud Console**(§1) OAuth 클라이언트 → **승인된 리디렉션 URI = `https://<your-app>.vercel.app/auth/callback`**.
- (`APP_ENV=prod`라 위 값들이 다 채워지기 전엔 API가 일부러 부팅 거부 — 안전장치.)

### 12-6. GitHub Actions (그레이더·평가)
레포 **Settings ▸ Secrets and variables ▸ Actions**:
- **Secrets**: `DATABASE_URL`(Neon, Render와 동일), `EVAL_SEED_SECRET`(Render와 **반드시 동일**).
- **Variables**: `API_HEALTH_URL = https://<your-app>.vercel.app/healthz`.
- **Actions** 탭에서 워크플로 활성화(공개 레포 첫 진입 시 “I understand…” 클릭). 이제:
  - `grade-samples` (~10분마다): 챌린지 제출의 **샘플 비용** 채점(즉시 원하면 *Run workflow*).
  - `evals` (09·18 KST + 15분마다): 대회 status 전이 + **중간/최종 평가**(상대 등수 산정).
  - `keepalive` (~10분마다): Render 무료 인스턴스 슬립 방지.

### 12-7. 끝 — 로그인 & 운영
`https://<your-app>.vercel.app` 접속 → **구글 로그인**(`goodpjw1111@gmail.com`) → 도메인 예외+부트스트랩으로
**자동 관리자** → `+ 새 모의고사`로 대회 생성. Step Up은 즉시 채점, Challenge는 다음 `grade-samples` 틱에
샘플 비용이, 09·18시 `evals`에 상대 점수가 붙는다.

> **알아둘 한계**: ⓐ Actions 스케줄은 **지연·스킵** 가능(샘플 채점 최대 ~10–15분 지연, 평가도 약간 늦을 수
> 있음 — 라운드는 as-of 컷오프라 결과는 동일). ⓑ **isolate가 Actions 러너에서 셋업 실패하면 챌린지 채점이
> 안 됨**(가장 취약한 부분; 로그 확인 → 안 되면 전용 리눅스 그레이더 필요, GRADING_ENV.md). ⓒ 그레이더 잡이
> `DATABASE_URL`을 들고 유저 코드를 isolate에서 실행 — 교내 대회 수준 신뢰 가정. 더 엄격히는 무-DSN HMAC
> 콜백 분리(`.github/workflows/grade.yml`, Phase 2 split). ⓓ x86 EPYC 러너라 NYPC c7a와 시간보정만 맞추면
> 공정(`DMPC_CALIBRATION_FACTOR`, GRADING_ENV.md §4).
