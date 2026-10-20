"""Semua ambang dan setelan ada di sini, nilainya dibaca dari config.json.

Aturan main (pelajaran dari proyek APD):
- Satu sumber nilai: config.json. Default flag CLI diambil dari objek Config
  yang sudah dimuat, jadi flag yang tidak diisi tidak pernah menimpa config.
- Satu confidence threshold operasional per class (person_score, vehicle_score).
  scan.store_score_min hanya batas penyimpanan JSONL, bukan ambang keputusan.
  Tidak ada ambang confidence kedua di atasnya.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


@dataclass
class DetectorCfg:
    model_dir: str = "models/rtdetr_v2_r50vd"
    device: str = "auto"            # auto | cpu | cuda | cuda:0
    imgsz: int = 640
    batch_size: int = 4             # di CPU otomatis 1
    fp16: bool = False              # hanya berlaku di cuda
    # class COCO yang disimpan. bus/motorbike dicatat tapi bukan kendaraan target.
    keep_labels: list = field(default_factory=lambda: ["person", "car", "truck", "bus", "motorbike"])


@dataclass
class ScanCfg:
    sample_every_s: float = 1.0     # jarak antar frame sampel (detik video)
    store_score_min: float = 0.15   # batas simpan ke JSONL (bukan ambang keputusan)
    seek_ok_codecs: list = field(default_factory=lambda: ["h264"])
    probe_samples: int = 40         # frame tersebar di seluruh video untuk mencari objek diam
    flush_every: int = 1            # tulis ke disk tiap N batch (1 = tiap batch, aman bila proses mati)
    max_restarts: int = 20          # proses scan dijalankan ulang otomatis (resume) bila mati tidak normal
    box_decimals: int = 0           # pembulatan koordinat bounding box di JSONL (0 = piksel bulat); null = tanpa pembulatan


@dataclass
class RulesCfg:
    # --- ambang confidence operasional (satu per class) ---
    vehicle_labels: list = field(default_factory=lambda: ["truck", "car"])
    vehicle_score: float = 0.5
    person_score: float = 0.5
    # --- pembersihan kotak ganda ---
    nms_iou: float = 0.6
    contain_frac: float = 0.85      # kotak kendaraan skor lebih rendah yang >= ini berada di dalam kotak lain dibuang
    # --- objek diam (poster, manekin) dan zona abaikan ---
    static_iou: float = 0.5
    static_min_frac: float = 0.3    # muncul di >= 30% frame probe
    static_max_drift_px: float = 20.0
    static_min_span_s: float = 1800.0   # frame acuan objek diam harus mencakup >= 30 menit video, bila kurang saringan mati
    ignore_zones: list = field(default_factory=list)   # [[x1,y1,x2,y2], ...] piksel frame asli
    # berkas area mask KENDARAAN per kamera (masks.py): kendaraan yang titik bawah-tengah kotaknya di dalam
    # polygon diabaikan beserta orang yang terkait kepadanya; hanya berlaku bila resolusi frame sama dengan
    # field "resolusi" berkas. Dipakai evaluate dan render (bukan candidates). --no-vehicle-masks = mati.
    vehicle_masks: list = field(default_factory=list)
    # --- geometri orang di bak ---
    inside_margin: float = 0.02     # toleransi vertikal titik bawah-tengah di luar kotak, relatif tinggi kendaraan
    stationary_median_min_obs: int = 3   # kendaraan diam >= 3 frame: kotak dinilai = median rentang diam (0 = mati)
    feet_above_frac: float = 0.25   # bottom orang minimal 25% tinggi kendaraan di atas bottom kendaraan
    cab_frac: float = 0.38          # porsi panjang kotak kendaraan yang dianggap kabin
    cab_side_default: str = "unknown"   # left | right | unknown (dipakai kalau arah datang tidak teramati)
    # --- tracking kendaraan dan orang ---
    track_iou: float = 0.3
    track_center_frac: float = 0.5  # cadangan: jarak pusat <= 0.5 x lebar kendaraan
    track_max_gap_s: float = 5.0
    person_link_iou: float = 0.2    # IoU kotak orang dalam koordinat relatif kendaraan
    person_link_dist: float = 0.15  # cadangan: jarak pusat relatif
    # --- gerak / diam ---
    moving_min_frac_per_s: float = 0.08     # perpindahan >= 0.08 x lebar kendaraan per detik = bergerak
    motion_baseline_s: float = 1.0          # kecepatan diukur terhadap observasi >= 1 s sebelumnya
    cab_motion_min_frac: float = 0.25       # perpindahan horizontal minimal saat datang untuk menetapkan sisi kabin
    arrival_max_s: float = 3.0              # gerak harus mulai <= 3 s setelah kendaraan pertama terlihat
    # --- voting ---
    vote_n: int = 2
    vote_m: int = 3


@dataclass
class CandidatesCfg:
    vehicle_score: float = 0.3      # mode longgar (recall tinggi)
    person_score: float = 0.3
    overlap_min: float = 0.3        # luas irisan / luas kotak orang
    # orang "terangkat" (versi longgar aturan kaki, ketat = rules.feet_above_frac 0,25):
    feet_gap_min: float = 0.10      # bottom orang >= 0,10 x tinggi kendaraan di atas bottom kendaraan
    person_area_max_frac: float = 0.5   # kotak orang > 0,5 x luas kotak kendaraan = objek yang sama berlabel ganda
    min_frames: int = 2             # minimal frame sampel berorang terangkat per kejadian (evaluate butuh 2 dari 3)
    event_gap_s: float = 5.0
    sheet_frames: int = 4
    sheet_tile_w: int = 480
    sheet_jpeg_quality: int = 80


@dataclass
class EvidenceCfg:
    """Aturan bukti bak (evidence.py): status bed geometri tetap bed hanya bila ada bukti, selain itu uncertain.
    Hanya bisa MENGURANGI bed (tidak pernah menambah pelanggaran)."""
    enabled: bool = True
    height_min: float = 0.30        # P1: tinggi orang / tinggi kendaraan (maks track window_s ke belakang)
    head_above_min: float = -0.05   # P2: (y atas kendaraan - y atas orang) / tinggi kendaraan, deteksi ini
    width_check: bool = True        # S: lebar orang / tinggi orang yang diharapkan (butuh kalibrasi kamera)
    width_min: float = 0.22
    # P1 hanya dihitung bila tinggi badan yang TERLIHAT (tinggi kotak orang, deteksi ini) / tinggi orang berdiri yang
    # diharapkan di garis bawah kendaraan >= visible_min (butuh kalibrasi kamera; None = mati). Orang di tanah di sisi
    # jauh bak hanya terlihat kepala/bahu/dada di atas dinding bak (koreksi label user 15 Okt 2026).
    visible_min: float | None = 0.55   # dipasang 15 Okt 2026 siang (outputs/aturan_bukti/relabel_15okt)
    # syarat kaki (pose.py, outputs/aturan_bukti/kaki_15okt): P1 hanya berlaku bila pose menemukan lutut/pergelangan kaki
    # (confidence >= legs_min_conf) minimal legs_drop_min x tinggi orang berdiri yang diharapkan DI BAWAH pinggul.
    # Hanya untuk deteksi yang berbukti lewat P1 saja (P2 tidak lolos); butuh kalibrasi kamera dan video sumber
    # (tanpa video: dilewati dengan peringatan). None = mati.
    legs_drop_min: float | None = 0.20   # dipasang 15 Okt 2026 sore (outputs/aturan_bukti/kaki_15okt)
    legs_min_conf: float = 0.2
    pose_model: str = "models/vitpose_base_simple"
    pose_device: str = "auto"       # auto | cpu | cuda
    pose_cache_dir: str = "outputs/cache_pose"   # keypoint per video (dibaca ulang evaluate/render)
    window_s: float = 30.0          # jendela masa lalu maksimum P1/S (detik)
    # observasi berbukti minimum di jendela voting (rules.vote_person): >= rules.vote_n = tiap observasi bed yang
    # dihitung voting wajib berbukti (perilaku 13 Okt); 1 = cukup satu berbukti, sisanya boleh bed geometri tanpa bukti
    vote_evidence_min: int = 1
    # nama kamera kalibrasi yang dipakai tanpa melihat nama berkas video ("" = otomatis dari resolusi + nama berkas)
    camera: str = ""
    person_score_track: float = 0.3  # lintasan lebar untuk track orang global (sama dengan penilai kabin/bak)
    # penyangkal (veto) bed geometri sejak 14 Okt 2026 malam (evidence.veto_code; outputs/aturan_bukti/veto_belakang):
    # hanya MEMBATALKAN bed (menjadi ragu), tidak pernah menambah alarm. None = veto itu mati.
    veto: bool = True                     # saklar semua veto (--no-evidence-veto = mati)
    veto_top_min: float | None = None     # A: feet_gap >= ini (titik bawah orang di tepi atas kotak kendaraan);
                                          # TIDAK dipasang (mati): ikut membatalkan orang di bak asli (Traga Camera Test)
    veto_top_labels: list = field(default_factory=lambda: ["car"])   # A hanya untuk label kendaraan ini (kosong = semua)
    veto_edge_min: float | None = 0.10    # C: jarak pusat orang ke ujung kendaraan yang terlihat / lebar < ini
    veto_inside_min: float | None = None  # C: bagian lebar orang di dalam kotak kendaraan < ini (tidak dipasang)
    # B ketat: kendaraan bergeser searah >= veto_follow_move_min x lebar per detik di 2 detik berturut-turut (kotak
    # mentah, tepi terpotong tidak dihitung) dan orang tidak ikut (gerak orang / gerak ramalan <= veto_follow_max)
    veto_follow_move_min: float | None = 0.04
    veto_follow_max: float = 0.4
    # kalibrasi perspektif per kamera: tinggi orang berdiri yang diharapkan (px) = a x y_bawah + b. Dipakai bila
    # resolusi frame sama dan nama berkas video memuat video_name_contains. Kamera tanpa entri: S dilewati.
    calibration: list = field(default_factory=lambda: [
        {"camera": "POS Arah Luar", "resolution": [1920, 1080], "video_name_contains": "POS Arah Luar",
         "a": 0.60369, "b": -28.105},
        {"camera": "Camera Test POS", "resolution": [1280, 720], "video_name_contains": "Camera Test POS",
         "a": 0.55819, "b": -9.935}])


@dataclass
class ClassifierCfg:
    """Penilai kabin/bak/tanah/bukan_orang dengan ciri track (classifier.py, models/kabin_bak_v2.npz).
    Hanya bisa MEMBATALKAN status bed (tidak pernah menambah pelanggaran)."""
    enabled: bool = False           # default mati lagi sejak 14 Okt 2026 (panel skeptis: gagal uji fase frame dan
                                    # uji LORO pada t_cancel terpasang; lihat config.json/README)
    model: str = "models/kabin_bak_v2.npz"
    t_cancel: float = 0.2           # bed dibatalkan bila P(bak) track (maksimum per track) < t_cancel
    sample_s: float = 1.0           # langkah sampel data latih; record lebih rapat disubsampel untuk penilai
    render_context_s: float = 10.0  # render tanpa scan events.json: deteksi rapat diperpanjang sekian detik sebelum klip


@dataclass
class OpenBedCfg:
    """Penyaring kendaraan tanpa bak terbuka (openbed.py, C1A_T2): bed geometri di kendaraan yang dinilai tanpa bak
    terbuka (sedan/SUV/MPV) dibatalkan. Hanya membatalkan. Butuh video sumber (crop kendaraan); tanpa video dilewati
    dengan peringatan."""
    enabled: bool = True
    model: str = "models/bak_terbuka_C1A_T2.npz"
    thr_p: float | None = None          # None = ambang di model (0,0232)
    cache_dir: str = "outputs/cache_bak_terbuka"   # ciri crop per video (dibaca ulang evaluate/render)


MOVING_MEASURES = ("min_edge", "max_edge", "max_edge_no_top", "edge_no_top")


@dataclass
class MovingCfg:
    """Aturan pelanggaran hanya saat kendaraan bergerak (keputusan klien 19 Okt 2026; motion.py, rules.apply_moving_rule).
    Orang yang lolos voting di bak baru menjadi pelanggaran bila kendaraannya bergerak; orang di bak kendaraan diam
    dicatat (bed_stationary), bukan alarm. enabled false (--moving-rule off) = perilaku 15 Okt persis."""
    enabled: bool = True
    measure: str = "edge_no_top"        # ukuran gerak (motion.MEASURES); min_edge = cara lama rules.box_shift
    min_frac_per_s: float = 0.06        # pergeseran tepi >= ini x lebar kendaraan per detik
    duration: int = 2                   # jumlah selang 1 s berturut-turut yang harus memenuhi ambang
    same_direction: bool = True         # tepi yang sama bergeser searah di semua selang syarat durasi
    min_px_frac: float = 0.006          # pergeseran minimum per detik dalam piksel = nilai ini x lebar frame (0 = mati)
    # review 19 Okt: observasi sefase yang hilang (deteksi kendaraan putus satu frame) dilewati selama celah ke observasi
    # rangkaian sebelumnya <= max_gap_s detik (motion.chain); 0 = setiap t-1 s, t-2 s wajib ada (cara 19 Okt pagi)
    max_gap_s: float = 0.0
    # review 19 Okt: kendaraan in_motion dinilai dengan kotak MENTAH; rentang kotak diam yang dihaluskan
    # (rules.smooth_stationary_boxes, dipicu field moving lama) diputus di observasi itu. false = cara 19 Okt pagi
    # (bawaan: true menghilangkan alarm vid2, lihat config.json _catatan_raw_box_when_moving)
    raw_box_when_moving: bool = False
    carry_bed: bool = True              # penerusan status di bak dari saat diam ke saat mulai bergerak (dipasang setelah review 19 Okt)
    carry_max_s: float = 3.0            # paling lama sekian detik sejak observasi diam sefase dengan orang lolos voting bed
    carry_rel_tol: float = 0.15         # selisih posisi relatif kotak kendaraan (pusat horizontal, kaki) maksimum


@dataclass
class MetricsCfg:
    """Penilaian keluaran evaluate terhadap label manual (metrics.py, `python -m vv metrics`). Aturan sama dengan alat
    validasi (tools/validasi/nilai_cli.py)."""
    # sejak 19 Okt 2026 label turunan definisi bergerak (orang di bak SAAT kendaraan bergerak); definisi lama: data/labels_v1
    labels_dir: str = "data/labels_v2_bergerak"
    # sumber dengan label kejadian lengkap yang boleh dinilai (daftar BERLABEL alat validasi); berkas label lain
    # (mis. posluar_0805, camtest_0803b) hanya berisi sebagian kandidat, jadi hanya jumlah alarm yang dicetak
    labeled_sources: list = field(default_factory=lambda: [
        "posluar_0803", "posluar_0804", "posluar_0806", "camtest_0803d", "camtest_0803c", "camtest_0803a"])
    # kejadian di jalan luar yang SENGAJA diabaikan area mask (keputusan user 9 Okt): "<sumber> <start_clock>",
    # dihitung terpisah (tidak masuk recall)
    masked_events: list = field(default_factory=lambda: [
        "posluar_0804 11:39:32", "posluar_0806 09:03:35", "posluar_0806 11:29:37"])
    tol_s: float = 2.0              # toleransi tumpang tindih alarm dengan interval kejadian (detik)


RENDER_VIEWS = ("operator", "debug")
DECISION_FROM = ("nearest", "past")


@dataclass
class RenderCfg:
    width: int = 960
    det_fps: float = 5.0            # frame yang dideteksi dan ditulis per detik
    pad_s: float = 2.0
    max_s: float = 30.0
    jpeg_preview_quality: int = 80
    # tampilan klip: operator = hanya kotak orang yang melanggar (voted bed), bawaan untuk user/klien sejak 14 Okt 2026;
    # debug = semua status (pemeriksaan, --view debug)
    view: str = "operator"
    # tampilan operator: kotak pelanggar ditahan paling lama sekian detik selama n_bed kendaraan > 0 (anti kedip)
    operator_hold_s: float = 1.0
    # frame rapat mengambil keputusan dari frame sampel evaluate: "nearest" = terdekat (bisa sampai setengah langkah
    # SESUDAH frame itu, perilaku sejak 9 Okt), "past" = frame sampel terakhir yang <= waktu frame (seperti real-time)
    decision_from: str = "nearest"


@dataclass
class Config:
    detector: DetectorCfg = field(default_factory=DetectorCfg)
    scan: ScanCfg = field(default_factory=ScanCfg)
    rules: RulesCfg = field(default_factory=RulesCfg)
    candidates: CandidatesCfg = field(default_factory=CandidatesCfg)
    render: RenderCfg = field(default_factory=RenderCfg)
    evidence: EvidenceCfg = field(default_factory=EvidenceCfg)
    classifier: ClassifierCfg = field(default_factory=ClassifierCfg)
    openbed: OpenBedCfg = field(default_factory=OpenBedCfg)
    metrics: MetricsCfg = field(default_factory=MetricsCfg)
    moving: MovingCfg = field(default_factory=MovingCfg)
    outputs_dir: str = "outputs"

    project_root: str = str(PROJECT_ROOT)

    def to_dict(self) -> dict:
        return asdict(self)

    def resolve(self, p: str) -> Path:
        """Path relatif di config dianggap relatif terhadap folder proyek."""
        q = Path(p)
        return q if q.is_absolute() else Path(self.project_root) / q


_SECTIONS = {"detector": DetectorCfg, "scan": ScanCfg, "rules": RulesCfg,
             "candidates": CandidatesCfg, "render": RenderCfg, "evidence": EvidenceCfg,
             "classifier": ClassifierCfg, "openbed": OpenBedCfg,
             "metrics": MetricsCfg, "moving": MovingCfg}


def load_config(path: str | os.PathLike | None = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    cfg = Config()
    if not p.exists():
        if path:
            raise FileNotFoundError(f"config tidak ditemukan: {p}")
        return cfg
    raw = json.loads(p.read_text(encoding="utf-8"))
    for key, val in raw.items():
        if key.startswith("_"):
            continue            # komentar
        if key in _SECTIONS:
            sec = getattr(cfg, key)
            names = {f.name for f in fields(sec)}
            for k, v in val.items():
                if k.startswith("_"):
                    continue
                if k not in names:
                    raise KeyError(f"config.json: kunci tidak dikenal {key}.{k}")
                setattr(sec, k, v)
        elif key == "outputs_dir":
            cfg.outputs_dir = val
        else:
            raise KeyError(f"config.json: bagian tidak dikenal {key}")
    if cfg.render.view not in RENDER_VIEWS:
        raise ValueError(f"config.json: render.view harus salah satu dari {', '.join(RENDER_VIEWS)}, "
                         f"bukan {cfg.render.view!r}")
    if cfg.render.decision_from not in DECISION_FROM:
        raise ValueError(f"config.json: render.decision_from harus salah satu dari {', '.join(DECISION_FROM)}, "
                         f"bukan {cfg.render.decision_from!r}")
    if cfg.moving.measure not in MOVING_MEASURES:
        raise ValueError(f"config.json: moving.measure harus salah satu dari {', '.join(MOVING_MEASURES)}, "
                         f"bukan {cfg.moving.measure!r}")
    return cfg
