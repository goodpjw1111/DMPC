# 채점 환경 — c7a.2xlarge를 무예산으로 등가화하기

> NYPC 실제 채점기: **AWS c7a.2xlarge** · AMD EPYC 9R14(Zen4 "Genoa") @ 3.7GHz ·
> x86-64 전용코어 8개(SMT 없음) · 16GiB · Ubuntu 24.04 · **2초 / 1024MB**.

## TL;DR

**무료로 c7a 실리콘을 24시간 굴리는 건 불가능**합니다. 대신 두 갈래로 해결합니다.

1. **예산 채점(점수에 반영되는 중간/최종 평가)**: **AWS Spot `c7a.large`** 를 **평가 시간대(09·18시)에만** 띄워 채점 → **같은 Genoa 코어라 단일스레드 속도가 c7a.2xlarge와 동일**(점수가 그대로 전이). 비용 ≈ **월 $1~5**, 그마저도 **학생/신규 크레딧으로 사실상 $0**.
2. **완전 무료 대안 + 샘플 채점**: **GitHub Actions(공개 레포) 러너**(AMD EPYC 7763, x86, 무제한 무료, 일회용 VM=샌드박스) → c7a보다 느리지만 **시간제한 보정(calibration)** 으로 *연산 예산*을 공정하게 맞춤.

**핵심 기법**: CPU를 못 맞추면 **시간을 맞춘다.** 고정 벤치마크로 "이 기계가 c7a보다 몇 배 느린가(factor)"를 재서 `유효 CPU 제한 = 2초 × factor`로 스케일. 실제 저지(DMOJ per-judge multiplier, Codeforces/ICPC)가 쓰는 표준 방법. 구현: [judge/calibration.py](../judge/calibration.py) + [judge/calib_kernel.cpp](../judge/calib_kernel.cpp).

## 1. 왜 단일스레드 속도가 중요한가

휴리스틱 점수는 "2초 안에 얼마나 많은 계산을 했나"에 달려 있고 대부분 **싱글스레드**입니다. 그래서 채점기 코어의 단일스레드 성능이 **공정성**과 **연습→실전 전이**를 좌우합니다. (Geekbench6 싱글코어 기준)

| 기계 (무료/대상) | CPU | 단일코어 점수 | c7a 대비 | 필요한 시간제한 |
|---|---|---|---|---|
| **c7a.2xlarge (목표)** | EPYC 9R14 Zen4 3.7GHz | ~1,994 | **1.00x** | 2.0초 |
| GitHub Actions x86 | EPYC 7763 Milan | ~1,300–1,450 | ~0.68x | **~3.0초** |
| Oracle Ampere A1 (ARM) | Neoverse-N1 ~3.0GHz | ~1,000–1,070 | ~0.52x | ~3.8초 |
| Oracle AMD micro | EPYC 7551, **1/8 공유** | 실효 매우 낮음 | ~0.1–0.15x | ~15초(노이즈 큼) |

> **c7a.large(가장 싼 c7a)는 c7a.2xlarge와 단일스레드가 동일** — 최종 보정·실채점용으로 이걸 쓰면 됩니다.

## 2. 권장 토폴로지 (티어드 채점)

```
[24/7 무료]  Oracle Always Free A1(ARM) : 웹앱 + API + Postgres + 스케줄러
                                          (웹/DB엔 ARM 무방)
      │ 제출/평가 잡을 큐(PG SKIP LOCKED)에 적재
      ▼
[샘플 채점·즉시 피드백]  무료 x86 (GitHub Actions 공개레포 러너 / 또는 작은 x86)
                          → 보정 factor 적용, "참고용 타이밍" 라벨
[점수 평가·09·18시/종료]  AWS Spot c7a.large (학생/신규 크레딧)  ← 진짜 답
                          → c7a와 동일 실리콘, 평가창에만 가동 후 종료
```

- **앱/DB는 Oracle A1(무료 영구)** 에 둡니다. ARM이어도 웹·DB엔 문제없음.
- **점수 나가는 평가(중간/최종)** 는 가능하면 **c7a.large Spot**(크레딧)로 — 실리콘이 같아 점수가 정확히 전이.
- **샘플(피드백용)** 은 무료 x86에서 보정 채점. (ARM에서 *점수* 채점은 금지 — §5)

## 3. 무료/저가 자원 (출처는 리서치 기준)

- **AWS Spot c7a** — 같은 하드웨어. c7a.2xlarge spot ≈ $0.16/hr, **c7a.large spot ≈ $0.04/hr**. 평가창(하루 ~1h)만 → **c7a.large ≈ 월 $1.2 / c7a.2xlarge ≈ 월 $5**. 신규 AWS **$100+$100 크레딧**, **AWS Educate $25–100**, **GitHub Student Pack**(DigitalOcean $200·Azure $100)로 수개월 $0. Spot 중단은 짧은 배치 채점엔 무해(재시도).
- **GitHub Actions(공개 레포)** — 4 vCPU/16GB x64, **무제한 무료**(비공개 2,000분/월). EPYC 7763(구형이지만 x86 AMD). **일회용 VM이라 untrusted 코드 격리에 적합**. 단, **시크릿 0인 전용 레포**로 분리하고 보정 factor 필수.
- **Oracle Always Free** — A1(ARM) 4 OCPU/24GB **영구 무료** → 앱 호스팅용. AMD micro는 1/8 공유라 채점 부적합.
- **학생 프로그램(디미고 고등학생 가능)** — GitHub Student Pack(13세+ 학교 인증), AWS Educate, (Azure for Students는 보통 대학 한정).
- 부적합: Fly.io(무료 종료), Render(슬립/스로틀), GCP e2-micro(너무 약함).

## 4. 보정 레시피 (calibration)

1. **기준 측정(딱 한 번, c7a.large에서)**: 크레딧으로 c7a.large spot 10분 띄우고 §6 튜닝 적용 후 `g++ -O2 -std=gnu++20 calib_kernel.cpp` 빌드·실행. 커널별 중앙값을 `judge/calibration.py`의 `NYPC_BASELINE_MS`에 기록(인스턴스는 바로 종료, 숫자는 영구 보관).
2. **그레이더 측정**: 같은 바이너리를 각 채점 호스트에서 실행 → `local_ms`.
3. **factor 계산**: `factor = median(local_ms / c7a_ms)`. 예) 1.6배 느리면 factor=1.6 → `유효 제한 = 2.0×1.6 = 3.2초`. 각 호스트에 `DMPC_CALIBRATION_FACTOR=1.6` 설정 → 그레이더가 자동 적용([judge/sandbox.py](../judge/sandbox.py) `run_case`).
4. **재보정**: 하드웨어·커널·컴파일러 바뀌면 다시.

**정확도 한계**: 단일 스칼라는 워크로드별 IPC/메모리대역/SIMD 차이로 완벽하지 않음. 정밀하게는 **문제별로 그 문제의 기준 솔루션을 벤치마크 삼아** 보정(진짜 저지가 하는 방식). 안전마진 ×1.05, known-good/known-TLE 레퍼런스로 검증.

## 5. ARM 경고 — 점수 채점은 x86에서만

Oracle A1(ARM)이 제일 싸지만 **x86 대상 대회에선 점수 채점 금지**:
- `#pragma GCC target("avx2")`, `_mm_*`, `__builtin` SIMD 등 **x86 전용 최적화**가 ARM에선 깨지거나 다른 NEON 코드로 바뀜 → c7a에서 이기는 풀이가 ARM에선 다르게 채점됨.
- IPC/코드젠 차이로 **두 풀이의 상대 순위가 뒤집힐 수 있어** 스칼라 factor로 못 고침.
- 따라서 ARM은 **비채점 정합성 사전검사**에만. 실제 NYPC도 x86(Zen4), Codeforces=Skylake, IOI=Core i5 전부 x86.

## 6. 하드웨어 일관성 체크리스트 (재현성)

`isolate-check-environment`가 강제하는 항목들:
- **거버너 performance**, **터보/부스트 OFF**(피크보단 *일관성*), **SMT/HT 비활성**.
- **격리 코어 핀**(`isolcpus=`+`nohz_full=`, `taskset -c <core>`/cgroup cpuset), IRQ 이동. 앱/DB는 다른 코어.
- **한 번에 한 제출만**(동시 실행은 L3/대역 공유로 CPU시간 부풀림).
- THP/스왑 끄기. 목표: 같은 코드 = 같은 CPU시간(런간 편차 <1~2%).
- 제한: `isolate --cg --time=<유효초> --wall-time=<3~4배> --cg-mem=1048576 --processes=1 ...` (**CPU시간으로 채점, wall은 안전 상한만**).

## 7. 비용 요약

| 시나리오 | 월 비용 |
|---|---|
| 앱(Oracle A1) + 샘플·평가 전부 GitHub Actions 무료 | **$0** (보정 채점) |
| 앱(Oracle A1) + 점수 평가만 c7a.large spot(크레딧) | **$0**(크레딧 소진까지), 이후 **~$1–5** |

권장: **시작은 전액 무료(A1 + GitHub Actions + 보정)**, 실전 직전/대회엔 **c7a.large spot(크레딧)로 점수 평가**해 완전 동일 환경 확보.

## 출처
AWS C7a / Spec·가격, Spare Cores 벤치, Oracle Always Free, GitHub Actions 러너(EPYC 7763)·무료정책, GitHub Student Pack/AWS Educate, isolate-check-environment, DMOJ wall_time_factor·per-judge multiplier, Codeforces x86/ARM 논의 — (리서치 워크플로 wlyvp8yue 참조).
