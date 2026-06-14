// Calibration benchmark for time-limit scaling vs the NYPC c7a.2xlarge.
//
// Build EXACTLY as the contest does:
//     g++ -O2 -std=gnu++20 -o calib calib_kernel.cpp
// Run it on (a) a real c7a.large [single-thread-identical to c7a.2xlarge] and
// (b) each grader host, both tuned: performance governor, turbo OFF, pinned core,
// nothing else running (taskset -c <core> ./calib). Take the median of a few runs.
//
//   * Paste c7a medians into judge/calibration.py NYPC_BASELINE_MS.
//   * Set each grader's DMPC_CALIBRATION_FACTOR = median(local_ms / c7a_ms).
// See docs/GRADING_ENV.md.
#include <bits/stdc++.h>
using namespace std;

static double cpu_ms(const function<void()>& f) {
    clock_t a = clock(); f(); clock_t b = clock();
    return 1000.0 * double(b - a) / CLOCKS_PER_SEC;   // CPU time, not wall time
}

int main() {
    volatile uint64_t sink = 0;

    // 1) compute: tight integer/branch loop (IPC / branch-predictor bound)
    double t_compute = cpu_ms([&] {
        uint64_t x = 88172645463325252ull, acc = 0;
        for (long i = 0; i < 300000000; i++) { x ^= x << 13; x ^= x >> 7; x ^= x << 17; acc += (x & 1) ? x : (uint64_t)i; }
        sink += acc;
    });

    // 2) memory: strided sweep over ~64 MB (cache / bandwidth bound)
    double t_memory = cpu_ms([&] {
        const size_t N = 16 * 1024 * 1024; vector<int> v(N);
        for (size_t i = 0; i < N; i++) v[i] = (int)i;
        long s = 0; for (int rep = 0; rep < 8; rep++) for (size_t i = 0; i < N; i += 16) s += v[i];
        sink += (uint64_t)s;
    });

    // 3) sort: std::sort heavy (comparison / shuffle bound)
    double t_sort = cpu_ms([&] {
        mt19937 rng(12345); vector<int> v(4000000);
        for (auto& x : v) x = rng();
        for (int rep = 0; rep < 3; rep++) { sort(v.begin(), v.end()); reverse(v.begin(), v.end()); }
        sink += (uint64_t)v[0];
    });

    // 4) simd: float loop (autovectorizes to AVX on x86, NEON on ARM -> ISA-sensitive)
    double t_simd = cpu_ms([&] {
        const size_t N = 4096; static float a[N], b[N];
        for (size_t i = 0; i < N; i++) { a[i] = i * 0.5f; b[i] = i * 0.25f; }
        double s = 0; for (int rep = 0; rep < 200000; rep++) for (size_t i = 0; i < N; i++) s += a[i] * b[i] + a[i];
        sink += (uint64_t)s;
    });

    printf("compute:%.1f memory:%.1f sort:%.1f simd:%.1f\n", t_compute, t_memory, t_sort, t_simd);
    if (sink == 424242) fprintf(stderr, " ");   // keep the optimizer honest
    return 0;
}
