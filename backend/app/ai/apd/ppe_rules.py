from __future__ import annotations

from dataclasses import dataclass, field

from .config import (PAIRED_ITEMS, PPE_ITEMS, STATUS_ABSENT, STATUS_PRESENT,
                     STATUS_UNKNOWN, item_of)


def _inter(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    x2, y2 = min(ax2, bx2), min(ay2, by2)
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def ioa(inner, outer) -> float:
    x1, y1, x2, y2 = inner
    area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    return _inter(inner, outer) / area if area > 0 else 0.0


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter = _inter(a, b)
    ua = ((ax2 - ax1) * (ay2 - ay1)) + ((bx2 - bx1) * (by2 - by1)) - inter
    return inter / ua if ua > 0 else 0.0


PPE_UTAMA = ("helmet", "coverall")


@dataclass
class PersonPPE:
    box: tuple
    score: float
    status: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    skor: dict = field(default_factory=dict)
    vote: dict = field(default_factory=dict)
    track_id: int | None = None

    @property
    def violations(self) -> list:
        return [i for i in PPE_ITEMS if self.status.get(i) == STATUS_ABSENT]

    @property
    def assessed(self) -> list:
        return [i for i in PPE_ITEMS
                if self.status.get(i) in (STATUS_PRESENT, STATUS_ABSENT)]

    @property
    def verdict(self) -> str:
        if self.violations:
            return "pelanggaran"
        if all(self.status.get(i) in (STATUS_PRESENT, STATUS_ABSENT) for i in PPE_UTAMA):
            return "lengkap"
        return "tidak_terbaca"

    @property
    def compliant(self) -> bool:
        return self.verdict == "lengkap"


def assess_frame(detections, assoc_ioa: float = 0.55, absent_margin: float = 0.25,
                 ambang_tuduh: dict | None = None,
                 margin_per_item: dict | None = None):
    persons = [d for d in detections if d.label == "person"]
    ppe = [d for d in detections if d.label != "person"]

    people = [PersonPPE(box=p.box, score=p.score,
                        status={i: STATUS_UNKNOWN for i in PPE_ITEMS},
                        evidence={i: 0.0 for i in PPE_ITEMS},
                        skor={i: (0.0, 0.0) for i in PPE_ITEMS})
              for p in persons]

    orphans = []
    buckets = [{i: [] for i in PPE_ITEMS} for _ in people]

    for d in ppe:
        item, status = item_of(d.label)
        if item is None:
            continue
        best_idx, best_ioa = -1, assoc_ioa
        for idx, p in enumerate(persons):
            v = ioa(d.box, p.box)
            if v > best_ioa:
                best_idx, best_ioa = idx, v
        if best_idx < 0:
            orphans.append(d)
        else:
            buckets[best_idx][item].append((d.score, status))

    for person, bucket in zip(people, buckets):
        for item, hits in bucket.items():
            if not hits:
                continue
            best_present = max([s for s, st in hits if st == STATUS_PRESENT], default=0.0)
            best_absent = max([s for s, st in hits if st == STATUS_ABSENT], default=0.0)
            person.skor[item] = (best_present, best_absent)
            if best_absent > 0 and best_absent < (ambang_tuduh or {}).get(item, 0.0):
                best_absent = 0.0
                if best_present <= 0.0:
                    continue
            bersaing = best_present > 0.0 and best_absent > 0.0
            if not bersaing:
                margin = 0.0
            elif item in PAIRED_ITEMS:
                margin = (margin_per_item or {}).get(item, 0.0)
            else:
                margin = absent_margin
            if best_absent >= best_present * (1.0 + margin):
                person.status[item] = STATUS_ABSENT
                person.evidence[item] = best_absent
            else:
                person.status[item] = STATUS_PRESENT
                person.evidence[item] = best_present

    return people, orphans


def frame_summary(people) -> dict:
    per_item = {}
    for p in people:
        for i in p.violations:
            per_item[i] = per_item.get(i, 0) + 1
    verdicts = [p.verdict for p in people]
    return {
        "person_count": len(people),
        "violator_count": verdicts.count("pelanggaran"),
        "compliant_count": verdicts.count("lengkap"),
        "unreadable_count": verdicts.count("tidak_terbaca"),
        "violations_per_item": per_item,
    }
