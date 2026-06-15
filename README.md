# DMPC — Dimigo Mock Programming Contest

NYPC Rookie Contest 대비용 **휴리스틱(최적화) 온라인 채점 플랫폼**.
`@dimigo.hs.kr` 계정으로 로그인하며, 보안을 1순위로 설계했다.

## 핵심

각 모의고사는 두 파트로 구성되고 **만점 1,000,000점을 2:8로 합산**한다 (모든 문제는 cost 최소화).

- **Step Up (20%)** — 출력만 제출(코드 X). 미션별로 입력이 주어지고, 직접/시뮬레이터로 만든 출력을 낸다. 체커가 비용을 계산해 기준 이하면 만점, 아니면 부분점수. 샌드박스 불필요.
- **Challenge (80%)** — 코드 제출(다언어). 숨겨진 무작위 케이스에 대해 샌드박스 실행 후 상대 등수로 채점. 대회 기간 중 정기 평가가 돈다.

문제는 플러그인 방식이다 — `problems/<key>/problem.py` 한 모듈(생성기·체커·메타)을 추가하면 레지스트리가 자동 인식한다. 브라우저 시뮬레이터도 `web/lib/simulators.ts`에 키로 등록하면 끝.

## 빠른 시작 (로컬)

```bash
# 프론트만 (백엔드 불필요 — mock 모드로 전 기능 동작)
cd web && npm install && npm run dev          # http://localhost:3000

# 풀스택 (Docker): postgres가 db/schema.sql 자동 적용
docker compose up postgres api web            # web :3000  api :8000/healthz

# 백엔드 테스트 전체 (외부 의존성 없음)
bash scripts/run_tests.sh
```

OAuth 로그인을 실제로 쓰려면 Google OAuth 클라이언트가 필요하다 — [docs/DEPLOY.md](docs/DEPLOY.md) §1.
운영자/관리자/테스터 이메일과 모든 시크릿은 **환경변수**로만 주입한다(소스에 두지 않음, `api/.env.example` 참고).

## 스택

| 레이어 | 선택 |
|---|---|
| 프론트 | Next.js (TypeScript) |
| 백엔드 | FastAPI (Python) |
| DB / 큐 | PostgreSQL (`SKIP LOCKED` 큐 겸용) |
| 채점 워커 | isolate 샌드박스 (**웹/API와 별도 호스트**) |

**철칙: 웹/API 티어는 유저 코드를 절대 실행하지 않는다.** 제출은 큐에 적재되고 별도 워커가 샌드박스에서 실행한 뒤 결과만 기록한다.

## 레이아웃

```
judge/      채점 코어 (스코어링·언어설정·샌드박스·문제 레지스트리·라운드 평가)
problems/   문제 플러그인 (problems/<key>/problem.py)
db/         PostgreSQL 스키마
api/        FastAPI (도메인 제한 OAuth·보안세션·CSRF·소유권 가드)
web/        Next.js 프론트 (로그인·대시보드·인브라우저 시뮬레이터)
worker/     그레이더 (샘플 큐 컨슈머 + 라운드 평가 + 스케줄러; Linux+isolate)
docs/       DEPLOY.md (배포 단계) · GRADING_ENV.md (채점 환경/시간보정)
```

## 배포

무카드 무료 구성(Vercel + Render + Neon + GitHub Actions)의 단계별 안내는 [docs/DEPLOY.md](docs/DEPLOY.md) 참고.

> **대회 보안**: 이 레포에는 문제 생성기·기준 풀이가 들어 있다. 실제 대회를 운영한다면 저장소를
> **비공개(private)** 로 두는 것을 권장한다(공개 시 풀이가 노출돼 부정행위가 가능). 비공개로 하면
> GitHub Actions 무료 분에 월 한도가 걸리니 채점 크론 빈도/구성은 [docs/DEPLOY.md](docs/DEPLOY.md) §12 참고.
