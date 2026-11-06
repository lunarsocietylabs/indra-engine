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

// ---------------------------------------------------------------- genome (maximal repeats) statistics
// One bottom-up pass over the LCP intervals of the whole canon. A "gene" = an LCP interval with lcp >= L that is
// left-diverse (a maximal repeat) and occurs in at least two different documents.
// out (int64[64]): [0] genes, [1] total loci, [2..6] genes by occurrence bucket (2,3-10,11-100,101-1000,>1000),
// [7..10] genes by length (L..15,16..31,32..63,64+), [11] loci capped at 200 per gene, [12] max stack depth,
// [13..17] loci by occurrence bucket.
typedef struct { int32_t lcp, lb, mind, maxd, lch; } Fr;
static inline int32_t lmerge(int32_t a, int32_t b) { if (a == -1) return b; if (b == -1) return a; return a == b ? a : -2; }
static inline void fmerge(Fr *t, const Fr *x) {
    if (x->mind < t->mind) t->mind = x->mind;
    if (x->maxd > t->maxd) t->maxd = x->maxd;
    t->lch = lmerge(t->lch, x->lch);
}
static inline void report(const Fr *x, int32_t rb, int32_t L, int64_t *o) {
    if (x->lcp < L || x->lch != -2 || x->mind == x->maxd) return;
    int64_t occ = (int64_t)rb - x->lb + 1;
    o[0]++; o[1] += occ; o[11] += occ < 200 ? occ : 200;
    int b = occ == 2 ? 2 : occ <= 10 ? 3 : occ <= 100 ? 4 : occ <= 1000 ? 5 : 6;
    o[b]++; o[b + 11] += occ;
    int lb = x->lcp < 16 ? 7 : x->lcp < 32 ? 8 : x->lcp < 64 ? 9 : 10;
    o[lb]++;
}
void genome_stats(const int32_t *T, const int32_t *SA, const int32_t *LCP, const int32_t *DOC, int32_t n, int32_t L, int64_t *o) {
    Fr *st = (Fr *)malloc(sizeof(Fr) * (size_t)(n + 2));
    int32_t top = 0;
    st[0] = (Fr){0, 0, 0x7fffffff, -1, -1};
    int32_t prev = 0;
    for (int32_t i = 1; i <= n; i++) {
        int32_t l = i < n ? LCP[i] : 0;
        int32_t s = SA[i - 1];
        Fr leaf = {0, 0, DOC[s], DOC[s], s > 0 ? T[s - 1] : -3};
        if (l < prev) fmerge(&st[top], &leaf);
        int32_t lb = i - 1;
        while (l < st[top].lcp) {
            Fr x = st[top--];
            report(&x, i - 1, L, o);
            lb = x.lb;
            if (l <= st[top].lcp) fmerge(&st[top], &x);
            else { Fr y = {l, lb, x.mind, x.maxd, x.lch}; st[++top] = y; }
        }
        if (l > st[top].lcp) { Fr y = {l, lb, 0x7fffffff, -1, -1}; st[++top] = y; }
        if (l >= prev) fmerge(&st[top], &leaf);
        if (top > o[12]) o[12] = top;
        prev = l;
    }
    free(st);
}
