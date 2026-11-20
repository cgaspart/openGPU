import io
import unittest
import urllib.error
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

    def test_gpu_names(self):
        self.assertEqual(vast.gpu_names("4090"), ["RTX 4090"])
        self.assertEqual(vast.gpu_names("h100"), ["H100 NVL", "H100 PCIE", "H100 SXM"])
        self.assertEqual(vast.gpu_names("A100 SXM"), ["A100 SXM4"])
        self.assertEqual(vast.gpu_names("3060 ti"), ["RTX 3060 Ti"])
        self.assertEqual(vast.gpu_names(" RTX 9999 "), ["RTX 9999"])

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


from opengpu.core import Offer
from opengpu.scan import scan, rank


def off(price, tier="interruptible", gpu="H100-SXM", provider="vast"):
    return Offer(provider, gpu, 1, 80, tier, price, "US", "", f"{provider}:{price}")


class TestScan(unittest.TestCase):
    def test_scan_isolates_failures_and_missing_keys(self):
        def ok(key, n, t, q): return [off(1.0)]
        def boom(key, n, t, q): raise TimeoutError("timed out")
        provs = {"a": ("A_KEY", ok), "b": ("B_KEY", boom), "c": ("C_KEY", ok)}
        r = scan(1, provs, env={"A_KEY": "secret", "B_KEY": "secret"})
        self.assertEqual(r.offers, [off(1.0)])
        self.assertEqual(r.skipped, ["c"])
        self.assertEqual(list(r.failed), ["b"])
        self.assertIn("timed out", r.failed["b"])
        self.assertNotIn("secret", r.failed["b"])

    def test_scan_passes_query_and_shows_http_error_body(self):
        seen = []
        def ok(key, n, t, q): seen.append(q); return []
        def bad_key(key, n, t, q):
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b'{"msg":"Invalid user key"}'))
        r = scan(1, {"a": ("A_KEY", ok), "b": ("B_KEY", bad_key)}, env={"A_KEY": "k", "B_KEY": "k"}, query="h100")
        self.assertEqual(seen, ["h100"])
        self.assertIn("HTTP 404", r.failed["b"])
        self.assertIn("Invalid user key", r.failed["b"])

    def test_rank_separates_tiers_and_sorts(self):
        offers = [off(2.0), off(1.0), off(0.5, "on-demand"), off(0.1, gpu="RTX-4090")]
        groups = rank(offers, "h100")
        self.assertEqual(list(groups), [("H100-SXM", 80, "interruptible"), ("H100-SXM", 80, "on-demand")])
        self.assertEqual([o.price_per_gpu_hr for o in groups[("H100-SXM", 80, "interruptible")]], [1.0, 2.0])
        self.assertEqual(list(rank(offers, "h100", tier="on-demand")), [("H100-SXM", 80, "on-demand")])
        self.assertEqual(len(rank(offers, "h100", limit=1)[("H100-SXM", 80, "interruptible")]), 1)


import io
import contextlib
from datetime import datetime, timezone
from opengpu.scan import ScanResult
from opengpu.__main__ import main, render


class TestCli(unittest.TestCase):
    def run_main(self, argv, result):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv, scan_fn=lambda n, **kw: result)
        return code, out.getvalue(), err.getvalue()

    def test_render(self):
        r = ScanResult([off(1.12)], ["runpod"], {})
        text = render(rank(r.offers, "h100"), 1, r, datetime(2026, 11, 20, 14, 2, tzinfo=timezone.utc))
        self.assertIn("H100-SXM 80GB × 1 · interruptible", text)
        self.assertIn("scanned 14:02 UTC", text)
        self.assertIn("1.120", text)
        self.assertIn("skipped: runpod (no API key)", text)

    def test_no_keys(self):
        code, out, err = self.run_main(["H100"], ScanResult([], ["vast", "runpod"], {}))
        self.assertEqual(code, 1)
        self.assertIn("VAST_API_KEY", err)
        self.assertIn("RUNPOD_API_KEY", err)

    def test_no_match(self):
        code, out, err = self.run_main(["H10O"], ScanResult([off(1.0)], [], {}))
        self.assertEqual(code, 1)
        self.assertIn("No offers for H10O × 1.", out)
        self.assertIn("H100-SXM", out)
