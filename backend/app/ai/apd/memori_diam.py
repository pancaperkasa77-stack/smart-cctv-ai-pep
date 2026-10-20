from __future__ import annotations

import math

import numpy as np

from .ppe_rules import ioa, iou


class _Jejak:
    __slots__ = ("c0", "w0", "h0", "hits", "dev", "dh", "dw", "helm", "cov", "ps", "pet",
                 "skor", "kotak", "lepas", "alasan_lepas", "nempel", "lahir", "akhir")

    def __init__(self, box, fi):
        self.c0 = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        self.w0 = max(1.0, box[2] - box[0])
        self.h0 = max(1.0, box[3] - box[1])
        self.hits = 0
        self.dev = self.dh = self.dw = 0.0
        self.helm = self.cov = self.pet = self.nempel = 0
        self.ps = []
        self.skor = []
        self.kotak = []
        self.lepas = False
        self.alasan_lepas = None
        self.lahir = fi
        self.akhir = fi


class MemoriDiam:
    def __init__(self, settings, lebar, tinggi, fps, stride):
        s = settings
        self.diag = math.hypot(lebar or 0, tinggi or 0)
        self.min_hit = int(s.memori_diam_min_hit)
        self.min_id = int(s.memori_diam_min_id)
        self.gerak = float(s.memori_diam_gerak)
        self.ukuran_tinggi = float(s.memori_diam_ukuran_tinggi)
        self.ukuran_lebar = float(s.memori_diam_ukuran_lebar)
        self.skor_maks = float(s.memori_diam_skor_maks)
        self.wearpack = float(s.memori_diam_wearpack)
        self.seragam = float(s.memori_diam_seragam)
        self.iou_gabung = float(s.memori_diam_iou_gabung)
        self.iou_tekan = float(s.memori_diam_iou_tekan)
        self.nempel_ioa = float(s.memori_diam_nempel_ioa)
        self.nempel_frac = float(s.memori_diam_nempel_frac)
        self.lahir_ulang = bool(s.memori_diam_lahir_ulang)
        # fi now carries monotonic seconds; observation counts remain frame based.
        self.lupa = float(s.memori_diam_lupa_detik)
        self.jejak = {}
        self.lokasi = []
        self.lokasi_lupa = []
        self.ids_tekan = {}
        self.frame_tekan = 0
        self.track_tekan = {}
        self._fi = None

    def prune(self, now, limit=2048):
        """Bound historical state without touching tracker delivered-event state."""
        self._lupakan(now)
        keep = sorted(((k, t) for k, t in self.jejak.items() if now - t.akhir <= self.lupa),
                      key=lambda kv: kv[1].akhir)[-limit:]
        self.jejak = dict(keep)
        keys = self.jejak.keys()
        self.ids_tekan = {k: v for k, v in self.ids_tekan.items() if k in keys}
        self.track_tekan = {k: v for k, v in self.track_tekan.items() if k in keys}
        for location in self.lokasi:
            location['ids'].intersection_update(keys)
        self.lokasi = self.lokasi[-limit:]
        self.lokasi_lupa.clear()  # No retrospective video report in streaming mode.

    def _alasan_lepas(self, t):
        if t.helm > 0:
            return "helm"
        if t.cov > 0:
            return "wearpack"
        if t.pet > 0 or (t.ps and float(np.median(t.ps)) >= self.seragam):
            return "seragam"
        if t.dev > self.gerak * self.diag:
            return "bergerak"
        if t.dh > self.ukuran_tinggi or t.dw > self.ukuran_lebar:
            return "ukuran"
        return None

    def _lupakan(self, fi):
        if fi == self._fi:
            return
        self._fi = fi
        sisa = []
        for m in self.lokasi:
            if fi - m["last"] > self.lupa:
                if m["n_tekan"] or len(m["ids"]) >= self.min_id:
                    self.lokasi_lupa.append(m)
            else:
                sisa.append(m)
        self.lokasi = sisa

    def amati(self, fi, tid, box, kotak_frame, idx, skor_orang, skor=None,
              p_seragam=None, petugas=False):
        self._lupakan(fi)
        t = self.jejak.get(tid)
        if t is None:
            t = self.jejak[tid] = _Jejak(box, fi)
        c = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        t.hits += 1
        t.akhir = fi
        t.kotak.append(list(box))
        del t.kotak[:-30]
        t.dev = max(t.dev, math.hypot(c[0] - t.c0[0], c[1] - t.c0[1]))
        t.dh = max(t.dh, abs((box[3] - box[1]) - t.h0) / t.h0)
        t.dw = max(t.dw, abs((box[2] - box[0]) - t.w0) / t.w0)
        skor = skor or {}
        h = skor.get("helmet")
        if h and (h[0] > 0 or h[1] > 0):
            t.helm += 1
        cv = skor.get("coverall")
        if cv and cv[0] >= self.wearpack:
            t.cov += 1
        if p_seragam is not None:
            t.ps.append(float(p_seragam))
        if petugas:
            t.pet += 1
        t.skor.append(float(skor_orang))
        # Long-lived stream uses bounded recent medians, not unbounded video history.
        del t.ps[:-512]
        del t.skor[:-512]

        nempel_kini = any(j != idx and ioa(box, kotak_frame[j]) >= self.nempel_ioa
                          for j in range(len(kotak_frame)))
        if nempel_kini:
            t.nempel += 1
        if not t.lepas:
            a = self._alasan_lepas(t)
            if a:
                t.lepas, t.alasan_lepas = True, a
                for m in self.lokasi:
                    m["ids"].discard(tid)
        if t.lepas or nempel_kini:
            return None
        if t.nempel >= self.nempel_frac * t.hits:
            return None

        skor_rendah = float(np.median(t.skor)) < self.skor_maks
        if t.hits >= self.min_hit and skor_rendah:
            mb = [float(v) for v in np.median(np.array(t.kotak), 0)]
            terbaik = None
            for m in self.lokasi:
                v = iou(mb, m["box"])
                if v >= self.iou_gabung and (terbaik is None or v > terbaik[0]):
                    terbaik = (v, m)
            if terbaik is None:
                m = {"box": mb, "ids": set(), "last": fi, "n_tekan": 0}
                self.lokasi.append(m)
            else:
                m = terbaik[1]
            if tid not in m["ids"]:
                serentak = any(self.jejak[o].akhir >= t.lahir for o in m["ids"])
                if not (self.lahir_ulang and serentak):
                    m["ids"].add(tid)
            if tid in m["ids"]:
                m["last"] = fi
        elif t.hits >= self.min_hit:
            for m in self.lokasi:
                m["ids"].discard(tid)

        for m in self.lokasi:
            if (len(m["ids"]) >= self.min_id and fi - m["last"] <= self.lupa
                    and iou(box, m["box"]) >= self.iou_tekan):
                self.frame_tekan += 1
                self.track_tekan[tid] = self.track_tekan.get(tid, 0) + 1
                m["n_tekan"] += 1
                for o in m["ids"]:
                    self.ids_tekan[o] = m
                return m
        return None

    def ids_dicabut(self):
        return {t for t in self.ids_tekan if not self.jejak[t].lepas}

    def alasan(self, tid):
        m = self.ids_tekan.get(tid)
        if m is None:
            return ""
        x1, y1, x2, y2 = (int(round(v)) for v in m["box"])
        return ("posisi benda diam (%d,%d,%d,%d): %d ID lahir bergantian di posisi ini; ID ini "
                "tidak pernah bergerak, terbaca helm, berwearpack, atau berseragam petugas"
                % (x1, y1, x2, y2, len(m["ids"])))

    def daftar_lokasi(self):
        hasil = []
        for m in self.lokasi_lupa + self.lokasi:
            if m["n_tekan"] or len(m["ids"]) >= self.min_id:
                hasil.append({"box": [round(v, 1) for v in m["box"]],
                              "track_ids": sorted(m["ids"]),
                              "frames_not_assessed": m["n_tekan"]})
        return hasil
