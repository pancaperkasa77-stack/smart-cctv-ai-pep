import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from app.demo_live.runtime import Models


class DemoRuleWiringTests(unittest.TestCase):
    def models(self):
        m = Models.__new__(Models)
        p = Mock()
        p.status = {'ready': True}
        p.infer.return_value = []
        p.detector.detections = []
        p.last_assessments = [{'box': (1, 1, 5, 5), 'status': {'helmet': 'absent'},
                               'exempt': False, 'reason': None}]
        m.pipelines = {1: p}
        m.vehicle_pipelines = {}
        m.generations = {1: 1}
        m.last_apd_events = {}
        m.last_vehicle_events = {}
        m.worker_gate = 'mati'
        m.vehicle = Mock()
        m.vehicle.detect.return_value = [{'boxes': [], 'labels': [], 'scores': []}]
        m.box = Mock()
        m.encode = Mock(return_value=b'jpeg')
        return m

    def test_current_assessment_overlay_survives_event_ack(self):
        m = self.models()
        images, events = m.infer(1, np.zeros((10, 10, 3), np.uint8), 1, 1)
        self.assertEqual(set(images), {'apd', 'vehicle'})
        self.assertEqual(events, [])
        self.assertIn('RULE ABSENT: helmet', [c.args[2] for c in m.box.call_args_list])
        self.assertIn('Worker gate: mati', m.encode.call_args_list[0].args[1][1])
        self.assertIn('UNCALIBRATED', m.encode.call_args_list[1].args[1][0])

    def test_configured_vehicle_runs_full_rules(self):
        m = self.models()
        rules = Mock()
        rules.status = 'ready'
        rules.infer.return_value = [SimpleNamespace(box=(1, 1, 4, 4), label='Penumpang di bak kendaraan')]
        m.vehicle_pipelines[1] = rules
        frame = np.zeros((10, 10, 3), np.uint8)
        m.infer(1, frame, 2, 2)
        rules.reset.assert_called_once()
        rules.infer.assert_called_once_with(frame, 2)
        self.assertIn('Penumpang di bak kendaraan', [c.args[2] for c in m.box.call_args_list])
        self.assertEqual(m.encode.call_args_list[1].args[1][0], 'VEHICLE RULES: ready')


if __name__ == '__main__':
    unittest.main()
