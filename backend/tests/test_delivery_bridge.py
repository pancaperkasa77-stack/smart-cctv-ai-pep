import unittest
from unittest.mock import Mock
import numpy as np
from app.ai.apd.pipeline import APDEvent
from app.demo_live.persistence import DetectionDelivery


class Outbox:
    def __init__(self): self.pending = {}; self.delivered = set()
    def enqueue(self,payload): self.pending.setdefault(payload['event_key'],payload); return True
    def contains(self,key): return key in self.pending
    def is_delivered(self,key): return key in self.delivered


class DeliveryBridgeTests(unittest.TestCase):
    def test_original_frame_and_time_retained_and_ack_after_commit(self):
        o=Outbox(); d=DetectionDelivery(o,True); p=Mock(); pipelines={1:p}
        e=APDEvent('Tidak memakai helm','critical',.8,(1,1,5,5),1,'helmet','session:1:helmet')
        d.submit(1,'camera',[e],[],np.zeros((10,10,3),np.uint8),1000,pipelines,1,1)
        key=next(iter(o.pending)); original=o.pending[key].copy();p.acknowledge.assert_not_called()
        d.submit(1,'camera',[e],[],np.ones((10,10,3),np.uint8),1001,pipelines,1,2)
        self.assertEqual(o.pending[key],original)
        o.delivered.add(key);d.poll_ack(pipelines);p.acknowledge.assert_called_once_with(e)

    def test_disabled_or_object_only_no_rows(self):
        o=Outbox();d=DetectionDelivery(o,False)
        d.submit(1,'camera',[],[],None,1000,{},1,1)
        self.assertFalse(o.pending)
        d=DetectionDelivery(o,True);d.submit(1,'camera',[],[],None,1000,{},1,1)
        self.assertFalse(o.pending)

    def test_enqueue_failure_never_ack(self):
        o=Outbox();o.enqueue=Mock(side_effect=OSError('disk full'));d=DetectionDelivery(o,True);p=Mock()
        e=APDEvent('test','warning',.8,(1,1,5,5),1,'helmet','e')
        d.submit(1,'camera',[e],[],np.zeros((10,10,3),np.uint8),1000,{1:p},1,1)
        self.assertIsNotNone(d.error);p.acknowledge.assert_not_called()


if __name__=='__main__':unittest.main()
