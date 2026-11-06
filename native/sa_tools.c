// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lunar Society Technologies
// Small helpers next to libsais: Kasai LCP and SA range search over int32 texts.
#include <stdint.h>
#include <stdlib.h>

// lcp[r] = LCP(SA[r-1], SA[r]); lcp[0]=0. rank is scratch (n int32).
void kasai32(const int32_t *T, const int32_t *SA, int32_t *rank, int32_t *lcp, int32_t n) {
    for (int32_t i = 0; i < n; i++) rank[SA[i]] = i;
    int32_t h = 0;
    for (int32_t i = 0; i < n; i++) {
        int32_t r = rank[i];
        if (r > 0) {
            int32_t j = SA[r - 1];
            while (i + h < n && j + h < n && T[i + h] == T[j + h]) h++;
            lcp[r] = h;
            if (h > 0) h--;
        } else { lcp[r] = 0; h = 0; }
    }
}

static int cmp_q(const int32_t *T, int32_t n, int32_t pos, const int32_t *q, int32_t m) {
    for (int32_t k = 0; k < m; k++) {
        if (pos + k >= n) return -1;
        if (T[pos + k] != q[k]) return T[pos + k] < q[k] ? -1 : 1;
    }
    return 0;
}

// [lo,hi) = SA range of suffixes starting with q.
void sa_range32(const int32_t *T, const int32_t *SA, int32_t n, const int32_t *q, int32_t m, int32_t *lo_out, int32_t *hi_out) {
    int32_t lo = 0, hi = n;
    while (lo < hi) { int32_t mid = lo + (hi - lo) / 2; if (cmp_q(T, n, SA[mid], q, m) < 0) lo = mid + 1; else hi = mid; }
    int32_t start = lo; hi = n;
    while (lo < hi) { int32_t mid = lo + (hi - lo) / 2; if (cmp_q(T, n, SA[mid], q, m) <= 0) lo = mid + 1; else hi = mid; }
    *lo_out = start; *hi_out = lo;
}
