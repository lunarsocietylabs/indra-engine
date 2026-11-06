# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lunar Society Technologies
"""native_sa.py — whole-canon suffix array via libsais (C) + Kasai/range search helpers, through ctypes.

build:  python3 -m indra.native_sa --build      (clones libsais, compiles to ./native/build)
"""
import ctypes, os, subprocess, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.path.join(os.path.dirname(HERE), "native")
BUILD = os.path.join(NATIVE, "build")


def build():
    os.makedirs(BUILD, exist_ok=True)
    src = os.path.join(BUILD, "libsais")
    if not os.path.isdir(src):
        subprocess.check_call(["git", "clone", "-q", "--depth", "1", "https://github.com/IlyaGrebnov/libsais.git", src])
    so = os.path.join(BUILD, "libindra.so")
    cmd = ["gcc", "-O3", "-fPIC", "-shared", "-fopenmp", "-DLIBSAIS_OPENMP", "-I" + os.path.join(src, "include"),
           os.path.join(src, "src", "libsais.c"), os.path.join(NATIVE, "sa_tools.c"), "-o", so]
    r = subprocess.run(cmd)
    if r.returncode:                                        # no OpenMP -> single thread
        subprocess.check_call([c for c in cmd if c not in ("-fopenmp", "-DLIBSAIS_OPENMP")])
    return so


def lib():
    so = os.path.join(BUILD, "libindra.so")
    if not os.path.exists(so):
        build()
    L = ctypes.CDLL(so)
    P = ctypes.c_void_p
    L.kasai32.argtypes = [P, P, P, P, ctypes.c_int32]
    L.sa_range32.argtypes = [P, P, ctypes.c_int32, P, ctypes.c_int32, P, P]
    try:
        L.libsais_int_omp.argtypes = [P, P, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
        L.libsais_int_omp.restype = ctypes.c_int32
        L.has_omp = True
    except AttributeError:
        L.has_omp = False
    L.libsais_int.argtypes = [P, P, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
    L.libsais_int.restype = ctypes.c_int32
    return L


class NativeIndex:
    """T: int32 array with values in [0,k). Builds SA (and optionally LCP)."""
    def __init__(self, T, k, threads=0, want_lcp=True):
        L = self.L = lib()
        self.T = np.ascontiguousarray(T, dtype=np.int32)
        self.n = n = len(self.T)
        self.SA = np.zeros(n, dtype=np.int32)
        if L.has_omp:
            rc = L.libsais_int_omp(self.T.ctypes.data, self.SA.ctypes.data, n, int(k), 0, int(threads))
        else:
            rc = L.libsais_int(self.T.ctypes.data, self.SA.ctypes.data, n, int(k), 0)
        if rc != 0:
            raise RuntimeError(f"libsais failed rc={rc}")
        self.lcp = None
        if want_lcp:
            rank = np.zeros(n, dtype=np.int32)
            self.lcp = np.zeros(n, dtype=np.int32)
            L.kasai32(self.T.ctypes.data, self.SA.ctypes.data, rank.ctypes.data, self.lcp.ctypes.data, n)
            del rank

    def range(self, q):
        q = np.ascontiguousarray(q, dtype=np.int32)
        lo, hi = ctypes.c_int32(), ctypes.c_int32()
        self.L.sa_range32(self.T.ctypes.data, self.SA.ctypes.data, self.n, q.ctypes.data, len(q),
                          ctypes.byref(lo), ctypes.byref(hi))
        return lo.value, hi.value


def selftest():
    import random
    rnd = random.Random(7)
    for trial in range(20):
        n = rnd.randint(1, 400); k = rnd.choice([2, 3, 6, 40])
        T = np.array([rnd.randrange(k) for _ in range(n)], dtype=np.int32)
        ix = NativeIndex(T, k)
        sa_ref = sorted(range(n), key=lambda i: T[i:].tolist())
        assert ix.SA.tolist() == sa_ref, "SA mismatch"
        for r in range(1, n):
            a, b = sa_ref[r - 1], sa_ref[r]; h = 0
            while a + h < n and b + h < n and T[a + h] == T[b + h]:
                h += 1
            assert ix.lcp[r] == h, "LCP mismatch"
        for _ in range(30):
            m = rnd.randint(1, 5); q = [rnd.randrange(k) for _ in range(m)]
            lo, hi = ix.range(q)
            cnt = sum(1 for i in range(n - m + 1) if T[i:i + m].tolist() == q)
            assert hi - lo == cnt, "range mismatch"
    print("native_sa selftest: OK (SA, LCP, range vs brute force)")


if __name__ == "__main__":
    if "--build" in sys.argv:
        print(build())
    else:
        selftest()
