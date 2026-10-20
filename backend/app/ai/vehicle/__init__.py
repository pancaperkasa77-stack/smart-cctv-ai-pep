"""Bounded, calibrated vehicle rules adapted from vehicle_violation_20okt_v2.

Original core source retained under Apache-2.0; see LICENSE-APACHE-2.0.txt.
No COCO class is itself a violation. Confidence is person detector confidence,
not a probability that the composed moving/open-bed/evidence rule is correct.
"""
from .pipeline import Detection, VehiclePipeline

__all__ = ["Detection", "VehiclePipeline"]
