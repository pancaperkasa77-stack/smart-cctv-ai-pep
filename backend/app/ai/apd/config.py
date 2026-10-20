from __future__ import annotations

import os
from dataclasses import dataclass, field

CLASSES = [
    "person",
    "coverall_present",
    "helmet_present",
    "helmet_absent",
    "boot_present",
    "glove_absent",
    "coverall_absent",
    "eyewear_absent",
    "boot_absent",
    "eyewear_present",
    "glove_present",
]

LABEL_OFFSET = 1

PERSON_CLASS = "person"

PPE_ITEMS = ["helmet", "coverall", "boot", "glove", "eyewear"]

PAIRED_ITEMS = {"boot", "glove"}

PPE_LABEL_ID = {
    "helmet": "Helm",
    "coverall": "Wearpack",
    "boot": "Sepatu",
    "glove": "Sarung tangan",
    "eyewear": "Kacamata",
}

COLOR_PERSON_OK = (90, 200, 90)
COLOR_PERSON_VIOLATION = (40, 40, 235)
COLOR_PERSON_UNKNOWN = (0, 165, 235)
COLOR_PRESENT = (110, 190, 110)
COLOR_ABSENT = (60, 60, 240)
COLOR_TEXT = (255, 255, 255)

STATUS_PRESENT = "present"
STATUS_ABSENT = "absent"
STATUS_UNKNOWN = "unknown"

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def path_proyek(p: str) -> str:
    if not p or os.path.isabs(p) or os.path.exists(p):
        return p
    return os.path.join(_ROOT, p)


@dataclass
class Settings:
    weights: str = ""
    cpu_threads: int = 4
    device: str = "cpu"
    imgsz: int = 640

    conf_person: float = 0.09
    conf_ppe: float = 0.09

    conf_per_class: dict = field(default_factory=lambda: {"boot_present": 0.07})

    assoc_ioa: float = 0.55

    absent_margin: float = 0.25

    margin_per_item: dict = field(default_factory=lambda: {"boot": 0.25})

    ragu_present_frac: dict = field(default_factory=lambda: {"boot": 0.4})

    ambang_tuduh: dict = field(default_factory=dict)

    tahan_gambar_frame: int = 3

    saringan_orang: bool = False

    gambar_kotak_abai: bool = False
    label_lebar_acuan: int = 1280
    label_min_tinggi: float = 0.06
    label_rasio_badan: float = 2.5
    label_histeresis: float = 0.15

    two_stage: bool = False
    zoom_max_height: int = 260
    zoom_margin: float = 0.18

    mask: str = ""

    prescan: int = 60
    static_frac: float = 0.15
    static_spread: float = 0.50
    static_drift: float = 2.5

    stride: int = 1
    mulai_detik: float = 0.0
    violation_min_hits: int = 3

    vote_window: int = 15
    vote_min: int = 2
    vote_on: float = 0.6
    vote_off: float = 0.3

    min_track_hits: int = 4
    min_person_score: float = 0.25
    sure_person_score: float = 0.60
    person_aspect: tuple = (0.7, 6.0)
    kotak_gabung_x: float = 2.2
    static_track_frames: int = 90
    static_track_move: float = 0.015

    memori_diam: bool = True
    memori_diam_min_hit: int = 5
    memori_diam_min_id: int = 2
    memori_diam_gerak: float = 0.003
    memori_diam_ukuran_tinggi: float = 0.15
    memori_diam_ukuran_lebar: float = 0.25
    memori_diam_skor_maks: float = 0.60
    memori_diam_wearpack: float = 0.6
    memori_diam_seragam: float = 0.5
    memori_diam_iou_gabung: float = 0.6
    memori_diam_iou_tekan: float = 0.5
    memori_diam_nempel_ioa: float = 0.5
    memori_diam_nempel_frac: float = 0.5
    memori_diam_lahir_ulang: bool = True
    memori_diam_lupa_detik: float = 600.0

    exempt_uniform: bool = True
    petugas_on: float = 0.6
    petugas_off: float = 0.35
    seragam_alpha: float = 0.04
    waris_iou: float = 0.35
    waris_jeda: int = 90
    waris_n_maks: int = 10
    tolak_wearpack: bool = True
    tolak_wearpack_ambang: float = 0.6
    tolak_wearpack_k: int = 3
    tolak_wearpack_frac: float = 0.3
    tolak_wearpack_jendela: int = 45
    seragam_penutup: str = ""
    seragam_penutup_frac: float = 0.25
    gerbang_pekerja: str = "coverall"
    petugas_on_terkini: float = 0.5
    petugas_muda_frame: int = 15
    tahan_maks_frame: int = 0
    petugas_frac_cabut: float = 0.5
    seragam_model: str = "models/seragam_v2.npz"
    seragam_ambang: float = 0.55
    track_iou: float = 0.25
    track_max_age: int = 15

    classes: list = field(default_factory=lambda: list(CLASSES))

    @property
    def num_classes(self) -> int:
        return len(self.classes)


def item_of(label: str):
    for suffix, status in (("_present", STATUS_PRESENT), ("_absent", STATUS_ABSENT)):
        if label.endswith(suffix):
            return label[: -len(suffix)], status
    return None, None
