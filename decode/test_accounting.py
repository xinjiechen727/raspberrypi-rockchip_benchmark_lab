"""Hardware-independent contract checks; no fabricated decoder measurements."""
import collections
import threading
import types
import unittest
from bench import Lane

class AccountingTests(unittest.TestCase):
    def lane(self):
        x=Lane.__new__(Lane)
        x.Gst=types.SimpleNamespace(SECOND=1_000_000_000,CLOCK_TIME_NONE=2**64-1)
        x.lock=threading.RLock(); x.fps=60; x.n=600
        x.input=set(); x.decoded=set(); x.delivered=set()
        x.errors=collections.Counter(); x.buffers=collections.Counter(); x.stage_last={}
        return x

    def buffer(self,k):
        return types.SimpleNamespace(pts=round(k*1_000_000_000/60))

    def test_valid_600_frame_identity(self):
        x=self.lane()
        for k in range(600):
            for stage in [x.input,x.decoded,x.delivered]:
                self.assertEqual(x.record(self.buffer(k),stage),k)
        self.assertEqual(x.input,x.delivered)
        self.assertEqual(x.buffers['decoded'],600)
        self.assertFalse(x.errors)

    def test_duplicate_is_not_an_extra_unique_frame(self):
        x=self.lane()
        for _ in range(2): x.record(self.buffer(0),x.decoded)
        self.assertEqual(len(x.decoded),1)
        self.assertEqual(x.buffers['decoded'],2)
        self.assertEqual(x.errors['decoded_duplicate'],1)

    def test_missing_invalid_pts_cannot_count(self):
        x=self.lane()
        for pts in [2**64-1,100_000_000_000,1234567]:
            self.assertIsNone(x.record(types.SimpleNamespace(pts=pts),x.input))
        self.assertEqual(len(x.input),0)
        self.assertEqual(x.buffers['input'],3)
        self.assertEqual(sum(x.errors.values()),3)

    def test_reordering_fails_even_if_counts_match(self):
        x=self.lane()
        x.record(self.buffer(1),x.delivered)
        x.record(self.buffer(0),x.delivered)
        self.assertEqual(len(x.delivered),2)
        self.assertEqual(x.errors['delivered_pts_nonmonotonic'],1)

    def test_missing_frame_visible_by_identity(self):
        x=self.lane()
        for k in range(3): x.record(self.buffer(k),x.input)
        for k in [0,2]: x.record(self.buffer(k),x.decoded)
        x.record(self.buffer(0),x.delivered)
        self.assertEqual(x.input-x.decoded,{1})
        self.assertEqual(x.decoded-x.delivered,{2})
        self.assertEqual(x.input-x.delivered,{1,2})

if __name__=='__main__': unittest.main()
