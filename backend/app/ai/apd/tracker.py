from __future__ import annotations

from dataclasses import dataclass, field

from .ppe_rules import iou


@dataclass
class Track:
    track_id: int
    box: tuple
    age: int = 0
    hits: int = 0
    absent_streak: dict = field(default_factory=dict)
    reported: set = field(default_factory=set)
    history: dict = field(default_factory=dict)
    voted: dict = field(default_factory=dict)
    scores: list = field(default_factory=list)
    centers: list = field(default_factory=list)
    box0: tuple = None
    static: bool = False
    seragam_ema: float = None
    seragam_n: int = 0
    seragam_sendiri: int = 0
    orang_pasti: bool = False
    luas: list = field(default_factory=list)
    petugas: bool = False


class IouTracker:
    def __init__(self, iou_threshold: float = 0.35, max_age: int = 15):
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.tracks: list[Track] = []
        self._next_id = 1

    def update(self, boxes: list) -> list:
        for t in self.tracks:
            t.age += 1

        assigned = [None] * len(boxes)
        used = set()

        pairs = []
        for bi, b in enumerate(boxes):
            for ti, t in enumerate(self.tracks):
                v = iou(b, t.box)
                if v >= self.iou_threshold:
                    pairs.append((v, bi, ti))
        pairs.sort(reverse=True)

        for _, bi, ti in pairs:
            if assigned[bi] is not None or ti in used:
                continue
            t = self.tracks[ti]
            t.box, t.age = boxes[bi], 0
            t.hits += 1
            t.centers.append(((boxes[bi][0] + boxes[bi][2]) / 2, (boxes[bi][1] + boxes[bi][3]) / 2))
            assigned[bi] = t.track_id
            used.add(ti)

        for bi, b in enumerate(boxes):
            if assigned[bi] is None:
                t = Track(track_id=self._next_id, box=b, hits=1, box0=b,
                          centers=[((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)])
                self._next_id += 1
                self.tracks.append(t)
                assigned[bi] = t.track_id

        self.tracks = [t for t in self.tracks if t.age <= self.max_age]
        return assigned

    def get(self, track_id: int):
        for t in self.tracks:
            if t.track_id == track_id:
                return t
        return None
