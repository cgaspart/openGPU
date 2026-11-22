import io
import unittest
import urllib.error
import urllib.parse
from unittest import mock
from opengpu.core import Offer, canonical_gpu, matches


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
        self.assertEqual(canonical_gpu("RTX 4000Ada"), "RTX-4000-ADA")
        self.assertEqual(canonical_gpu("RTX 4000 Ada"), "RTX-4000-ADA")
        self.assertEqual(canonical_gpu("Some New GPU"), "SOME-NEW-GPU")
        self.assertEqual(canonical_gpu("H200 SXM5 141GB"), "H200")
        self.assertEqual(canonical_gpu("B200 SXM6 180GB"), "B200")
        self.assertEqual(canonical_gpu("A100 SXM4 80GB"), "A100-SXM")
        self.assertEqual(canonical_gpu("L40S 48GB"), "L40S")
        self.assertEqual(canonical_gpu("GB200"), "GB200")

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
        self.assertEqual(vast.gpu_names("RTX 4000 Ada"), ["RTX 4000Ada"])

    def test_vram_snaps_to_marketed_size(self):
        for mib, gb in [(81559, 80), (46068, 48), (143771, 141), (183359, 180), (24564, 24),
                        (49140, 48), (95830, 94), (40960, 40), (12288, 12), (23034, 24)]:
            self.assertEqual(vast.vram_gb(mib), gb, mib)

    def test_fetch_narrows_sampled_results_to_cheapest(self):
        # Vast answers broad searches with a ~50-offer sample, not the cheapest. Worst case: the priciest 50.
        pool = [dict(ROW, id=i, machine_id=i, num_gpus=1, dph_total=float(i), min_bid=float(i)) for i in range(1, 101)]
        def fake_post(url, body, headers, timeout):
            field = "min_bid" if body["type"] == "bid" else "dph_total"
            cap = body.get(field, {}).get("lte", float("inf"))
            hits = [o for o in pool if o[field] <= cap]
            return {"offers": hits if len(hits) < 40 else hits[-50:]}
        with mock.patch("opengpu.vast.post_json", fake_post):
            offers = vast.fetch("k", 1, 5, "h100")
        for tier in ("interruptible", "on-demand"):
            self.assertEqual(min(o.price_per_gpu_hr for o in offers if o.tier == tier), 1.0, tier)

    def test_fetch_unions_parallel_samples(self):
        # Vast samples server-side: identical simultaneous calls return different subsets.
        import collections, threading
        pool = [dict(ROW, id=i, machine_id=i, num_gpus=1, dph_total=float(i), min_bid=float(i)) for i in range(1, 22)]
        calls, lock = collections.Counter(), threading.Lock()
        def fake_post(url, body, headers, timeout):
            with lock:
                n = calls[body["type"]]
                calls[body["type"]] += 1
            return {"offers": pool[n % 3::3]}  # each call of a tier sees a different third
        with mock.patch("opengpu.vast.post_json", fake_post):
            offers = vast.fetch("k", 1, 5, "h100")
        for tier in ("interruptible", "on-demand"):
            prices = sorted(o.price_per_gpu_hr for o in offers if o.tier == tier)
            self.assertEqual(prices[:4], [1.0, 2.0, 3.0, 4.0], tier)

    def test_fetch_survives_some_rate_limited_samples(self):
        import collections, threading
        calls, lock = collections.Counter(), threading.Lock()
        def fake_post(url, body, headers, timeout):
            with lock:
                n = calls[body["type"]]
                calls[body["type"]] += 1
            if n % 3:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO(b""))
            return {"offers": [dict(ROW, machine_id=1)]}
        with mock.patch("opengpu.vast.post_json", fake_post):
            self.assertEqual(len(vast.fetch("k", 1, 5, "h100")), 2)  # one offer per tier

        def all_fail(url, body, headers, timeout):
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO(b""))
        with mock.patch("opengpu.vast.post_json", all_fail), self.assertRaises(urllib.error.HTTPError):
            vast.fetch("k", 1, 5, "h100")

    def test_parse_skips_bad_rows(self):
        bad = [dict(ROW, min_bid=None), dict(ROW, min_bid=0), dict(ROW, num_gpus=0)]
        self.assertEqual(vast.parse(bad, "interruptible"), [])
        self.assertEqual(len(vast.parse([dict(ROW, geolocation=None)], "on-demand")), 1)


from opengpu import runpod

GT = {"id": "NVIDIA H100 80GB HBM3", "name": "H100 SXM", "memory": 80,
      "price": {"secure": 4.39, "community": 2.69, "serverless": 5.89}, "availability": "MEDIUM"}


class TestRunPod(unittest.TestCase):
    def test_parse(self):
        [o] = runpod.parse([GT], 2, "SECURE")
        self.assertEqual(o, Offer("runpod", "H100-SXM", 2, 80, "on-demand", 4.39, "-",
                                  "secure cloud · stock medium", "runpod:NVIDIA H100 80GB HBM3"))
        [o] = runpod.parse([GT], 1, "COMMUNITY")
        self.assertEqual((o.price_per_gpu_hr, o.fine_print), (2.69, "community cloud · stock medium"))

    def test_parse_skips_out_of_stock_or_unpriced(self):
        self.assertEqual(runpod.parse([dict(GT, availability="NONE")], 1, "SECURE"), [])
        self.assertEqual(runpod.parse([{k: v for k, v in GT.items() if k != "availability"}], 1, "SECURE"), [])
        self.assertEqual(runpod.parse([dict(GT, price={"secure": None, "community": 0})], 1, "COMMUNITY"), [])

    def test_fetch_asks_both_clouds_for_the_gpu_count(self):
        calls = []
        def fake_get(url, headers, timeout):
            calls.append((url, headers["Authorization"]))
            return {"gpus": [GT]}
        with mock.patch("opengpu.runpod.get_json", fake_get):
            offers = runpod.fetch("k", 2, 5, "h100")
        self.assertEqual([c[1] for c in calls], ["Bearer k", "Bearer k"])
        for url, cloud in zip((c[0] for c in calls), ("SECURE", "COMMUNITY")):
            self.assertTrue(url.startswith("https://api.runpod.io/v2/catalog/gpus?"), url)
            for part in ("include=AVAILABILITY", "product=POD", "count=2", f"cloud={cloud}"):
                self.assertIn(part, url)
        self.assertEqual(sorted(o.price_per_gpu_hr for o in offers), [2.69, 4.39])


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

    def test_scan_survives_unreadable_error_body(self):
        class Broken(io.BytesIO):
            def read(self, *a): raise ConnectionResetError("reset")
        def bad(key, n, t, q):
            raise urllib.error.HTTPError("u", 502, "Bad Gateway", {}, Broken())
        def html(key, n, t, q):
            raise urllib.error.HTTPError("u", 502, "Bad Gateway", {}, io.BytesIO(b"<html>\r\n<head>\n oops"))
        r = scan(1, {"a": ("K", bad), "b": ("K", html)}, env={"K": "k"}, query="h100")
        self.assertIn("HTTP 502", r.failed["a"])
        self.assertEqual(r.failed["b"], "HTTP 502: <html> <head> oops")

    def test_scan_runs_keyless_providers(self):
        got = []
        def ok(key, n, t, q): got.append(key); return []
        r = scan(1, {"free": (None, ok), "paid": ("P_KEY", ok)}, env={}, query="h100")
        self.assertEqual((got, r.skipped), ([""], ["paid"]))

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
        self.assertIn("skipped: runpod (set RUNPOD_API_KEY)", text)

    def test_no_match(self):
        # With server-side filtering, a typo returns no Vast offers at all; still list names to pick from.
        code, out, err = self.run_main(["H10O"], ScanResult([], [], {}))
        self.assertEqual(code, 1)
        self.assertIn("No offers for H10O × 1.", out)
        self.assertIn("H100-SXM", out)


from opengpu import verda, azure

VT = {"instance_type": "1H100.80S.30V", "name": "H100 SXM5 80GB", "currency": "usd",
      "price_per_hour": "5.453", "spot_price": "2.727",
      "gpu": {"description": "1x H100 SXM5 80GB", "number_of_gpus": 1},
      "gpu_memory": {"size_in_gigabytes": 80},
      "location_prices": [{"location_code": "FIN-01", "price_per_hour": "5.241", "spot_price": "2.621"}]}


class TestVerda(unittest.TestCase):
    def test_parse_location_prices(self):
        offers = verda.parse([VT], 1)
        self.assertEqual(sorted(offers, key=lambda o: o.tier), [
            Offer("verda", "H100-SXM", 1, 80, "interruptible", 2.621, "FIN-01",
                  "can be removed any time · stock not checked", "verda:1H100.80S.30V"),
            Offer("verda", "H100-SXM", 1, 80, "on-demand", 5.241, "FIN-01",
                  "stock not checked", "verda:1H100.80S.30V"),
        ])

    def test_parse_per_gpu_and_filters(self):
        eight = dict(VT, instance_type="8H100", gpu={"number_of_gpus": 8}, gpu_memory={"size_in_gigabytes": 640},
                     location_prices=[{"location_code": "FIN-02", "price_per_hour": "40", "spot_price": None}])
        cpu = dict(VT, instance_type="CPU", gpu={"number_of_gpus": 0}, gpu_memory={"size_in_gigabytes": 0})
        [o] = verda.parse([VT, eight, cpu], 8)
        self.assertEqual((o.gpu_count, o.vram_gb, o.tier, o.price_per_gpu_hr, o.region), (8, 80, "on-demand", 5.0, "FIN-02"))
        self.assertEqual(verda.parse([cpu], 0), [])
        [o] = [x for x in verda.parse([dict(VT, location_prices=[])], 1) if x.tier == "on-demand"]
        self.assertEqual((o.price_per_gpu_hr, o.region), (5.453, "-"))  # falls back to headline price


def az(sku, price, meter_suffix=" Spot", product="Virtual Machines NDsr H100 v5 Series", region="eastus"):
    return {"armSkuName": sku, "armRegionName": region, "retailPrice": price, "unitOfMeasure": "1 Hour",
            "meterName": sku.split("_", 1)[1] + meter_suffix, "productName": product}


class TestAzure(unittest.TestCase):
    def test_skus_for(self):
        self.assertEqual(azure.skus_for("h100", 8), ["Standard_ND96isr_H100_v5"])
        self.assertEqual(azure.skus_for("H100 NVL", 1), ["Standard_NC40ads_H100_v5"])
        self.assertEqual(azure.skus_for("RTX 4090", 1), [])

    def test_parse(self):
        items = [az("Standard_ND96isr_H100_v5", 18.4), az("Standard_ND96isr_H100_v5", 98.32, ""),
                 az("Standard_ND96isr_H100_v5", 9.0, " Low Priority"),
                 az("Standard_ND96isr_H100_v5", 1.0, product="Virtual Machines NDsr H100 v5 Series Windows")]
        offers = sorted(azure.parse(items), key=lambda o: o.tier)
        self.assertEqual([(o.tier, o.price_per_gpu_hr) for o in offers], [("interruptible", 2.3), ("on-demand", 12.29)])
        o = offers[0]
        self.assertEqual((o.provider, o.gpu, o.gpu_count, o.vram_gb, o.region, o.offer_id),
                         ("azure", "H100-SXM", 8, 80, "eastus", "azure:Standard_ND96isr_H100_v5"))
        self.assertEqual(o.fine_print, "evicted with ~30 s notice · needs spot quota")

    def test_fetch_follows_pages_and_skips_unknown_gpus(self):
        urls = []
        def fake_get(url, headers, timeout):
            urls.append(url)
            if len(urls) == 1:
                return {"Items": [az("Standard_NC40ads_H100_v5", 1.5)], "NextPageLink": "https://prices.azure.com/next"}
            return {"Items": [az("Standard_NC40ads_H100_v5", 7.0, "")], "NextPageLink": None}
        with mock.patch("opengpu.azure.get_json", fake_get):
            offers = azure.fetch("", 1, 5, "H100 NVL")
            self.assertEqual(azure.fetch("", 1, 5, "RTX 4090"), [])
        self.assertEqual(len(urls), 2)
        self.assertIn("Standard_NC40ads_H100_v5", urllib.parse.unquote(urls[0]))
        self.assertEqual(sorted(o.price_per_gpu_hr for o in offers), [1.5, 7.0])


from opengpu import skypilot

H100_INFO = "{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 8, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 655360}"


def aws_row(az_spot, region="us-east-1", name="H100", count="8.0", info=H100_INFO, price="55.04"):
    return {"InstanceType": "p5.48xlarge", "AcceleratorName": name, "AcceleratorCount": count, "GpuInfo": info,
            "Price": price, "SpotPrice": az_spot, "Region": region}


def gcp_row(name, count, region, price, spot, instance="", mib=81920):
    return {"InstanceType": instance, "AcceleratorName": name, "AcceleratorCount": count,
            "GpuInfo": f"{{'Gpus': [{{'MemoryInfo': {{'SizeInMiB': {mib}}}}}]}}" if name else "",
            "Region": region, "Price": price, "SpotPrice": spot}


class TestSkyPilot(unittest.TestCase):
    def test_aws_cheapest_zone_per_region(self):
        rows = [aws_row("24.0"), aws_row("16.0"), aws_row("20.0", region="us-west-2"),
                aws_row("", region="eu-west-1"),  # no spot here
                aws_row("8.0", count="1.0"),  # other count
                aws_row("1.0", name="Inferentia2", info="{'Gpus': [{'Name': 'Inferentia2', 'Manufacturer': 'AWS'}]}")]
        got = sorted((o.region, o.tier, o.price_per_gpu_hr) for o in skypilot.parse_aws(rows, 8))
        self.assertEqual(got, [("eu-west-1", "on-demand", 6.88), ("us-east-1", "interruptible", 2.0),
                               ("us-east-1", "on-demand", 6.88), ("us-west-2", "interruptible", 2.5),
                               ("us-west-2", "on-demand", 6.88)])
        o = skypilot.parse_aws([aws_row("16.0")], 8)[0]
        self.assertEqual((o.provider, o.gpu, o.vram_gb, o.offer_id), ("aws", "H100-SXM", 80, "aws:p5.48xlarge"))

    def test_gcp_adds_the_vm_to_the_gpu_price(self):
        rows = [gcp_row("H100", "1", "us-central1", "9.79655", "5.8339"),
                gcp_row("", "", "us-central1", "1.18251", "0.70455", instance="a3-highgpu-1g"),
                gcp_row("H100", "1", "asia-east1", "11.0", "6.0"),  # no a3 VM price there: skipped
                gcp_row("T4", "1", "us-central1", "0.35", "0.1976", mib=16384),
                gcp_row("", "", "us-central1", "0.38", "0.09", instance="n1-standard-8"),
                gcp_row("tpu-v6e-1", "1", "us-central1", "2.7", "1.0")]
        got = {(o.gpu, o.tier): (round(o.price_per_gpu_hr, 5), o.vram_gb, o.region, o.offer_id)
               for o in skypilot.parse_gcp(rows, 1)}
        self.assertEqual(got, {
            ("H100-SXM", "interruptible"): (6.53845, 80, "us-central1", "gcp:a3-highgpu-1g"),
            ("H100-SXM", "on-demand"): (10.97906, 80, "us-central1", "gcp:a3-highgpu-1g"),
            ("T4", "interruptible"): (0.2876, 16, "us-central1", "gcp:n1-standard-8"),
            ("T4", "on-demand"): (0.73, 16, "us-central1", "gcp:n1-standard-8"),
        })

    def test_gcp_price_per_gpu(self):
        rows = [gcp_row("L4", "4", "us-central1", "2.24016", "1.3408", mib=24576),
                gcp_row("", "", "us-central1", "1.0", "", instance="g2-standard-48")]  # VM has no spot price
        [o] = skypilot.parse_gcp(rows, 4)
        self.assertEqual((o.tier, o.offer_id), ("on-demand", "gcp:g2-standard-48"))
        self.assertAlmostEqual(o.price_per_gpu_hr, 3.24016 / 4)
