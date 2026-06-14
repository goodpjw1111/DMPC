# 예시 문제 — 청소 로봇 (Step Up)

Phase-1 채점 파이프라인을 **끝까지 실증**하기 위한 레퍼런스 문제. 네 실제 문제도
이 4개 부품(생성기·체커·기준비용·메타)만 채우면 똑같이 동작한다.

## 부품 ↔ 플랫폼 매핑

| 파일 | 역할 | 플랫폼에서 |
|---|---|---|
| `problem.py` `generate(seed)` | 시드 → 입력(결정적) | `test_seeds`는 시드만 저장, 생성기가 입력 재생성 |
| `problem.py` `check(in,out)` | 출력 → (비용, 유효성) | 그레이더가 샌드박스 실행 후 호출(신뢰 코드) |
| `problem.py` `reference_cost` | 만점 기준 Cost′ | Step Up `scoring_config.cost_ref` |
| `problem.py` `META` | 제목·제한·예산·시뮬레이터 키 | `problems` 테이블 row |
| `sample_solution.py` / `naive_solution.py` | "유저 제출" 예시 | 실제로는 유저가 업로드한 소스 |
| `simulator.html` | 방향키·z·자동출력 시뮬레이터 | Phase 3 인브라우저 시뮬레이터(미션 탭) |

## 직접 확인하기

```bash
# 결정적 파이프라인 테스트 (생성기+체커+채점)
python problems/example_clean/test_pipeline.py      # 6/6

# E2E 데모: 제출을 서브프로세스로 돌려 점수까지
python problems/example_clean/run_demo.py
#   greedy → 1,000,000 (만점) / naive → ~786,250 (부분점수)

# 시뮬레이터: 그냥 브라우저로 열기 (무료, 서버 불필요)
#   problems/example_clean/simulator.html
```

> `run_demo.py`의 로컬 실행기는 **데모 전용**(샌드박스 없음). 실제 채점은
> `judge/sandbox.py`(isolate·무네트워크·자원제한)가 그레이더 호스트에서 수행한다.

## 채점 흐름 한눈에

```
generate(seed) ─→ 유저 코드 실행(샌드박스) ─→ stdout
                                              │
                          check(input, stdout) ─→ (cost, valid)
                                              │
        scoring.stepup_aggregate([(cost, cost_ref), ...]) ─→ 0‥1,000,000
```
