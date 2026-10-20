"""Status bergerak kendaraan untuk aturan "pelanggaran hanya saat kendaraan bergerak" (config moving, sejak 19 Okt 2026).

Permintaan klien 19 Okt: orang di bak kendaraan yang DIAM bukan pelanggaran; orang di bak kendaraan yang BERGERAK
(termasuk mundur/manuver) adalah pelanggaran. Modul ini hanya menilai gerak kendaraan dari kotak deteksi mentah per
track; keputusan orang di bak tetap dari aturan yang sudah ada (geometri, aturan bukti, veto, penyaring bak terbuka,
voting). Gerbang alarm ada di rules.apply_moving_rule.

Ukuran gerak per selang 1 s (kotak mentah, tepi yang menempel tepi frame tidak dipakai):
- min_edge        : cara lama vv.rules.box_shift (tepi kiri/kanan yang bergeser PALING SEDIKIT, median dy).
                    Kendaraan yang mendekat/menjauh/mundur sering terbaca diam.
- max_edge        : tepi kiri/kanan dan atas/bawah yang bergeser PALING BANYAK. Tepi atas ikut berubah saat orang
                    berdiri/naik di bak (Triton 3 Agu), jadi kendaraan diam bisa terbaca bergerak.
- max_edge_no_top : seperti max_edge tetapi dy hanya dari tepi bawah.
- edge_no_top     : per tepi (kiri, kanan, bawah): bergerak bila ada SATU tepi yang sama yang bergeser >= ambang di
                    setiap selang syarat durasi, dan (same_direction) arahnya sama di semua selang. Goyangan kotak yang
                    meloncat lalu kembali gagal syarat searah; tepi atas tidak dipakai.
Syarat durasi D: rangkaian sefase t, t-1 s, ..., t-D s (selisih <= setengah langkah record) dan tiap selang memenuhi
ambang. max_gap_s (config moving.max_gap_s, sejak review 19 Okt): bila observasi sefase berikutnya tidak ada (deteksi
kendaraan hilang satu frame, track tetap sama), dipakai observasi sefase yang lebih awal (t-2 s, t-3 s, ...) selama
jaraknya ke observasi rangkaian sebelumnya <= max_gap_s; kecepatan selang dibagi selisih waktu sebenarnya. 0 = cara
19 Okt (setiap t-k s wajib ada). Pada record rapat (render 5 fps) rangkaian sefase ini sama dengan scan 1 s yang
menyampel t, jadi keputusan frame rapat = keputusan scan 1 s fase itu. Hanya masa lalu yang dipakai di evaluate;
render meneruskan keputusan scan ke frame rapat lewat render.decision_from (bawaan "nearest" = scan terdekat, bisa
sampai setengah langkah sesudah frame itu; "past" = scan terakhir yang <= waktu frame).
"""
from __future__ import annotations

import bisect
import math

EDGE_FRAC = 0.01                      # sama dengan rules.box_shift dan evidence.clipped_edges
MEASURES = ("min_edge", "max_edge", "max_edge_no_top", "edge_no_top")
LEFT, TOP, RIGHT, BOTTOM = 0, 1, 2, 3


def clipped(b, W, H):
    """(kiri, atas, kanan, bawah) kotak menempel tepi frame (terpotong)."""
    return (W is not None and b[0] <= EDGE_FRAC * W, H is not None and b[1] <= EDGE_FRAC * H,
            W is not None and b[2] >= (1 - EDGE_FRAC) * W, H is not None and b[3] >= (1 - EDGE_FRAC) * H)


def _mean_w(a, b):
    return max(1.0, ((a[2] - a[0]) + (b[2] - b[0])) / 2.0)


def edge_speeds(a, b, dt, W, H, edges=(LEFT, RIGHT, BOTTOM)):
    """{tepi: pergeseran / dt / rerata lebar kotak} untuk tepi yang tidak terpotong di kedua kotak (bertanda)."""
    ca, cb = clipped(a, W, H), clipped(b, W, H)
    w = _mean_w(a, b)
    dt = max(1e-3, dt)
    return {j: (b[j] - a[j]) / dt / w for j in edges if not (ca[j] or cb[j])}


def interval_vector(a, b, W, H, measure):
    """(dx, dy) piksel satu selang untuk ukuran vektor (min_edge, max_edge, max_edge_no_top)."""
    if measure == "min_edge":
        from .rules import box_shift
        return box_shift(a, b, W)
    ca, cb = clipped(a, W, H), clipped(b, W, H)
    ex = [b[j] - a[j] for j in (LEFT, RIGHT) if not (ca[j] or cb[j])]
    dx = max(ex, key=abs) if ex else ((b[0] + b[2]) - (a[0] + a[2])) / 2.0
    if measure == "max_edge":
        ey = [b[j] - a[j] for j in (TOP, BOTTOM) if not (ca[j] or cb[j])]
        dy = max(ey, key=abs) if ey else ((b[1] + b[3]) - (a[1] + a[3])) / 2.0
    elif measure == "max_edge_no_top":
        dy = (b[3] - a[3]) if not (ca[3] or cb[3]) else 0.0
    else:
        raise ValueError(f"ukuran gerak {measure!r} bukan ukuran vektor")
    return dx, dy


def chain(times, i, n_steps, base=1.0, tol=0.5, max_gap=0.0):
    """Indeks observasi pada t_i, t_i - base, ..., t_i - n_steps x base (terdekat, selisih <= tol; seri = yang lebih
    akhir), urut mundur. None bila rangkaian tidak lengkap.

    max_gap > base: sasaran t_i - m x base yang tidak punya observasi dilewati, lalu sasaran sefase berikutnya
    (m + 1, m + 2, ...) dicoba selama jarak observasi rangkaian sebelumnya ke sasaran itu <= max_gap (+ tol). Sasaran
    selalu dihitung dari t_i, jadi rangkaian tetap sefase. max_gap <= base: setiap sasaran wajib ada (cara 19 Okt)."""
    out = [i]
    t = times[i]
    strict = not max_gap or max_gap <= base
    m = 0
    for _ in range(n_steps):
        while True:
            m += 1
            target = t - m * base
            if not strict and times[out[-1]] - target > max_gap + tol:
                return None
            a = bisect.bisect_left(times, target - tol, 0, i + 1)
            b = bisect.bisect_right(times, target + tol, 0, i + 1)
            cand = [x for x in range(a, b) if x < out[-1]]
            if cand:
                out.append(min(cand, key=lambda x: (abs(times[x] - target), -x)))
                break
            if strict:
                return None
    return out


def motion_flags(times, boxes, W, H, measure="edge_no_top", min_frac_per_s=0.08, duration=2, same_direction=True,
                 min_px_frac=0.0, base=1.0, tol=0.5, only=None, max_gap=0.0):
    """Kecepatan (lebar kendaraan per detik, selang terakhir) dan status bergerak per observasi satu track.

    times/boxes: observasi track urut waktu (kotak MENTAH). duration: jumlah selang 1 s berturut-turut yang harus
    memenuhi ambang (>= 1). min_px_frac: pergeseran minimum per detik dalam piksel = min_px_frac x lebar frame (0 = mati),
    supaya goyangan beberapa piksel pada kendaraan kecil/jauh tidak terbaca bergerak. max_gap: lihat chain (0 = setiap
    observasi sefase wajib ada). only: indeks observasi yang dihitung (alat analisis; None = semua).
    Kembalikan (speed, flags); observasi tanpa pembanding 1 s sebelumnya: speed 0, tidak bergerak."""
    if measure not in MEASURES:
        raise ValueError(f"ukuran gerak {measure!r} bukan salah satu {MEASURES}")
    n = len(times)
    dur = max(1, int(duration))
    px_min = (min_px_frac or 0.0) * (W or 0.0)
    speed, flags = [0.0] * n, [False] * n
    for i in (range(n) if only is None else only):
        c = chain(times, i, dur, base, tol, max_gap)
        if c is None:
            c1 = chain(times, i, 1, base, tol, max_gap)
            if c1 is not None:
                speed[i] = _speed_of(boxes[c1[1]], boxes[c1[0]], times[c1[0]] - times[c1[1]], W, H, measure)
            continue
        ivs = [(c[k + 1], c[k]) for k in range(dur)]          # (lebih awal, lebih akhir), selang terbaru dulu
        speed[i] = _speed_of(boxes[ivs[0][0]], boxes[ivs[0][1]], times[ivs[0][1]] - times[ivs[0][0]], W, H, measure)
        if measure == "edge_no_top":
            per = [edge_speeds(boxes[a], boxes[b], times[b] - times[a], W, H) for a, b in ivs]
            pxs = [{j: abs(boxes[b][j] - boxes[a][j]) / max(1e-3, times[b] - times[a]) for j in (LEFT, RIGHT, BOTTOM)}
                   for a, b in ivs]
            for j in (LEFT, RIGHT, BOTTOM):
                if not all(j in p for p in per):
                    continue
                vals = [p[j] for p in per]
                if any(abs(x) < min_frac_per_s for x in vals) or any(q[j] < px_min for q in pxs):
                    continue
                if same_direction and not (all(x > 0 for x in vals) or all(x < 0 for x in vals)):
                    continue
                flags[i] = True
                break
            continue
        ok = True
        prev = None
        for a, b in ivs:
            dt = max(1e-3, times[b] - times[a])
            dx, dy = interval_vector(boxes[a], boxes[b], W, H, measure)
            s = math.hypot(dx, dy) / dt / _mean_w(boxes[a], boxes[b])
            if s < min_frac_per_s or math.hypot(dx, dy) / dt < px_min:
                ok = False
                break
            if same_direction and prev is not None and dx * prev[0] + dy * prev[1] <= 0:
                ok = False
                break
            prev = (dx, dy)
        flags[i] = ok
    return speed, flags


def _speed_of(a, b, dt, W, H, measure):
    if measure == "edge_no_top":
        es = edge_speeds(a, b, dt, W, H)
        return max((abs(x) for x in es.values()), default=0.0)
    dx, dy = interval_vector(a, b, W, H, measure)
    return math.hypot(dx, dy) / max(1e-3, dt) / _mean_w(a, b)
