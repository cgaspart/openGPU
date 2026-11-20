import unittest
from opengpu.core import canonical_gpu, matches


class TestNames(unittest.TestCase):
    def test_canonical_gpu(self):
        self.assertEqual(canonical_gpu("NVIDIA H100 80GB HBM3"), "H100-SXM")
        self.assertEqual(canonical_gpu("H100 SXM5"), "H100-SXM")
        self.assertEqual(canonical_gpu("H100 SXM"), "H100-SXM")
        self.assertEqual(canonical_gpu("H100 PCIe"), "H100-PCIE")
        self.assertEqual(canonical_gpu("NVIDIA GeForce RTX 4090"), "RTX-4090")
        self.assertEqual(canonical_gpu("RTX 4090"), "RTX-4090")
        self.assertEqual(canonical_gpu("A100 SXM4"), "A100-SXM")
        self.assertEqual(canonical_gpu("RTX 6000Ada"), "RTX-6000-ADA")
        self.assertEqual(canonical_gpu("Some New GPU"), "SOME-NEW-GPU")

    def test_matches(self):
        for q in ("h100", "H100 SXM", "h100-sxm"):
            self.assertTrue(matches(q, "H100-SXM"), q)
        self.assertTrue(matches("4090", "RTX-4090"))
        self.assertFalse(matches("A10", "A100-SXM"))
        self.assertFalse(matches("H100 PCIe", "H100-SXM"))


from opengpu import vast

ROW = {"id": 42, "gpu_name": "H100 SXM", "num_gpus": 2, "gpu_ram": 81559,
       "dph_total": 5.0, "min_bid": 2.4, "reliability2": 0.973, "geolocation": "Texas, US"}


class TestVast(unittest.TestCase):
    def test_parse_interruptible(self):
        [o] = vast.parse([ROW], "interruptible")
        self.assertEqual((o.provider, o.gpu, o.gpu_count, o.vram_gb, o.tier, o.region, o.offer_id),
                         ("vast", "H100-SXM", 2, 80, "interruptible", "US", "vast:42"))
        self.assertAlmostEqual(o.price_per_gpu_hr, 1.2)
        self.assertEqual(o.fine_print, "outbid → paused, disk kept · reliability 0.97 · +bandwidth fees")

    def test_parse_on_demand(self):
        [o] = vast.parse([ROW], "on-demand")
        self.assertAlmostEqual(o.price_per_gpu_hr, 2.5)
        self.assertEqual(o.fine_print, "reliability 0.97 · +bandwidth fees")

    def test_parse_skips_bad_rows(self):
        bad = [dict(ROW, min_bid=None), dict(ROW, min_bid=0), dict(ROW, num_gpus=0)]
        self.assertEqual(vast.parse(bad, "interruptible"), [])
        self.assertEqual(len(vast.parse([dict(ROW, geolocation=None)], "on-demand")), 1)


from opengpu import runpod

GT = {"id": "NVIDIA H100 80GB HBM3", "displayName": "H100 SXM", "memoryInGb": 80,
      "securePrice": 2.99, "communityPrice": 2.69, "secureSpotPrice": 1.75,
      "communitySpotPrice": None, "lowestPrice": {"stockStatus": "High"}}


class TestRunPod(unittest.TestCase):
    def test_parse(self):
        offers = runpod.parse([GT], 2)
        got = {(o.tier, o.fine_print, o.price_per_gpu_hr) for o in offers}
        self.assertEqual(got, {
            ("on-demand", "secure cloud", 2.99),
            ("on-demand", "community cloud", 2.69),
            ("interruptible", "secure cloud · ~5 s warning, volume kept", 1.75),
        })
        o = offers[0]
        self.assertEqual((o.provider, o.gpu, o.gpu_count, o.vram_gb, o.region, o.offer_id),
                         ("runpod", "H100-SXM", 2, 80, "-", "runpod:NVIDIA H100 80GB HBM3"))

    def test_parse_skips_out_of_stock(self):
        self.assertEqual(runpod.parse([dict(GT, lowestPrice={"stockStatus": None})], 1), [])
        self.assertEqual(runpod.parse([dict(GT, lowestPrice=None)], 1), [])
