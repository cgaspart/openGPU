# GPU Spot Price Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `opengpu <GPU>` asks Vast.ai and RunPod for live prices and prints the cheapest comparable offers, spot and on-demand separately.

**Architecture:** One file per provider turns that provider's API response into a list of `Offer`s (one shared shape). `scan.py` calls every provider whose API key is set, in parallel, and collects offers plus skipped/failed providers. `rank` filters and groups offers by (GPU, VRAM, tier); `__main__.py` prints the table.

**Tech Stack:** Python ≥ 3.11, standard library only (`urllib`, `json`, `concurrent.futures`, `argparse`, `dataclasses`, `unittest`). No dependencies.

**Spec:** `docs/superpowers/specs/2026-11-20-gpu-spot-scanner-design.md`

## Global Constraints

- Standard library only; no new dependencies (including test frameworks — use `unittest`).
- Tiers are exactly `"interruptible"` and `"on-demand"`; never ranked together.
- Price = provider's hourly price for the machine ÷ GPU count, in USD. Fees and minimums go in `fine_print`, never in the price.
- Unmapped GPU names pass through (cleaned), never dropped.
- API keys only from env vars `VAST_API_KEY`, `RUNPOD_API_KEY`; never printed.
- Missing key → provider listed as `skipped`; any error → listed as `failed` with a short reason; other providers still print.
- Every HTTP request sends `User-Agent: opengpu/0.1` and a timeout (default 20 s).
- Run tests with: `python -m unittest -v` from the repo root.

## Review Focus

1. **GPU query matches nothing** (typo like `H10O`): print `No offers for H10O × 1.` plus the GPU names that were seen, exit 1 — not an empty table. → Task 5 test.
2. **Provider rows with null/zero price, zero GPUs, or no stock** (Vast `min_bid: null`, RunPod spot price `null` or `stockStatus: null`): skip that row, keep the rest. → Task 2 and Task 3 tests.
3. **No API keys set at all**: say which env vars to set, exit 1, make no network calls. → Task 5 test.
4. **One provider errors** (HTTP 401 bad key, timeout, `errors` in GraphQL reply): appears in `failed` with a short reason that doesn't contain the key; other providers' offers still returned. → Task 4 test.
5. **Query spelling variants** (`h100`, `H100 SXM`, `h100-sxm`, `4090`) match; `A10` must not match `A100-SXM`. → Task 1 test.

---

### Task 1: Offer shape, GPU name cleanup, HTTP helper

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `opengpu/__init__.py` (empty), `opengpu/core.py`
- Test: `tests/__init__.py` (empty), `tests/test_opengpu.py`

**Interfaces:**
- Produces (all in `opengpu/core.py`):
  - `@dataclass(frozen=True) class Offer: provider: str; gpu: str; gpu_count: int; vram_gb: int; tier: str; price_per_gpu_hr: float; region: str; fine_print: str; offer_id: str`
  - `ALIASES: dict[str, str]` — cleaned name → canonical name
  - `canonical_gpu(name: str) -> str`
  - `matches(query: str, gpu: str) -> bool`
  - `post_json(url: str, body: dict, headers: dict, timeout: float) -> dict`

- [ ] **Step 1: Write the failing tests** in `tests/test_opengpu.py`

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opengpu'`

- [ ] **Step 3: Implement `opengpu/core.py`**

- `canonical_gpu`: uppercase; drop the words `NVIDIA`, `GEFORCE`, `TESLA`; replace runs of non-alphanumerics with `-`; strip `-`; then return `ALIASES.get(cleaned, cleaned)`.
- Starter `ALIASES`: `"H100-80GB-HBM3": "H100-SXM"`, `"H100-SXM5": "H100-SXM"`, `"A100-SXM4": "A100-SXM"`, `"RTX-6000ADA": "RTX-6000-ADA"`. Grow it from real names seen in Task 5's live check.
- `matches`: every `-`-token of `canonical_gpu(query)` is one of the `-`-tokens of `gpu` (set subset).
- `post_json`: `urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")` with headers `Content-Type: application/json`, `User-Agent: opengpu/0.1` plus `headers`; return `json.load(response)`. Let `HTTPError`/`URLError`/timeouts raise.

Also create:
- `pyproject.toml`: `[project] name = "opengpu"`, `version = "0.1.0"`, `requires-python = ">=3.11"`, `[project.scripts] opengpu = "opengpu.__main__:main"`, `[build-system] requires = ["setuptools>=68"]`, `build-backend = "setuptools.build_meta"`.
- `.gitignore`: `__pycache__/`, `*.egg-info/`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m unittest -v`
Expected: 2 tests, OK

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore opengpu/ tests/
git commit -m "feat: offer shape, GPU name cleanup, HTTP helper"
```

---

### Task 2: Vast.ai connection

**Files:**
- Create: `opengpu/vast.py`
- Test: `tests/test_opengpu.py` (add `TestVast`)

**Interfaces:**
- Consumes: `Offer`, `canonical_gpu`, `post_json` from Task 1.
- Produces: `parse(offers: list[dict], tier: str) -> list[Offer]`; `fetch(api_key: str, gpu_count: int, timeout: float) -> list[Offer]`.

API facts (from vast-cli source):
- `POST https://console.vast.ai/api/v0/bundles/`, header `Authorization: Bearer <key>`.
- Body: `{"verified": {"eq": True}, "external": {"eq": False}, "rentable": {"eq": True}, "rented": {"eq": False}, "num_gpus": {"eq": gpu_count}, "type": T, "order": [["dph_total", "asc"]], "limit": 5000, "allocated_storage": 5.0}` with `T` = `"bid"` (→ tier `interruptible`) then `"on-demand"` (→ tier `on-demand`). Two requests.
- Reply: `{"offers": [...]}`. Fields used: `id`, `gpu_name`, `num_gpus`, `gpu_ram` (MiB), `dph_total` ($/h whole machine, incl. 5 GB disk), `min_bid` ($/h minimum interruptible bid, whole machine), `reliability2` (0–1), `geolocation` (e.g. `"Texas, US"`).

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest -v`
Expected: FAIL — `ImportError: cannot import name 'vast'`

- [ ] **Step 3: Implement `opengpu/vast.py`**

- `parse`: price field is `min_bid` for `interruptible`, `dph_total` for `on-demand`; skip rows where it is missing/≤0 or `num_gpus` is falsy. `vram_gb = round(gpu_ram / 1024)`. `region` = text after the last comma of `geolocation`, stripped; `"-"` if missing. Leave the `reliability …` part out of the fine print if `reliability2` is missing.
- `fetch`: the two requests above; concatenate `parse(reply["offers"], tier)`. Add `# ponytail: cheapest 5000 offers across all GPU models per tier; filter gpu_name server-side if rare GPUs get cut off`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m unittest -v`
Expected: all tests OK

- [ ] **Step 5: Commit**

```bash
git add opengpu/vast.py tests/test_opengpu.py
git commit -m "feat: Vast.ai connection"
```

---

### Task 3: RunPod connection

**Files:**
- Create: `opengpu/runpod.py`
- Test: `tests/test_opengpu.py` (add `TestRunPod`)

**Interfaces:**
- Consumes: `Offer`, `canonical_gpu`, `post_json` from Task 1.
- Produces: `parse(gpu_types: list[dict], gpu_count: int) -> list[Offer]`; `fetch(api_key: str, gpu_count: int, timeout: float) -> list[Offer]`.

API facts (from runpod-python and SkyPilot's RunPod fetcher):
- `POST https://api.runpod.io/graphql`, header `Authorization: Bearer <key>`, body `{"query": Q}` with
  `Q = "query { gpuTypes { id displayName memoryInGb securePrice communityPrice secureSpotPrice communitySpotPrice lowestPrice(input: {gpuCount: %d}) { stockStatus } } }" % gpu_count`
- Reply: `{"data": {"gpuTypes": [...]}}`, or `{"errors": [{"message": ...}]}` → raise `RuntimeError(message)`.
- All four prices are **per GPU** per hour already. `lowestPrice.stockStatus` is `null` when that count isn't available.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest -v`
Expected: FAIL — `ImportError: cannot import name 'runpod'`

- [ ] **Step 3: Implement `opengpu/runpod.py`**

`parse`: skip a GPU type when `lowestPrice` is null or its `stockStatus` is null; otherwise emit one `Offer` per non-null, >0 price among the four fields: `(securePrice, on-demand, "secure cloud")`, `(communityPrice, on-demand, "community cloud")`, `(secureSpotPrice, interruptible, "secure cloud · ~5 s warning, volume kept")`, `(communitySpotPrice, interruptible, "community cloud · ~5 s warning, volume kept")`. GPU name from `displayName`. Add `# ponytail: stock checked per GPU type, not per secure/community cloud`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m unittest -v`
Expected: all tests OK

- [ ] **Step 5: Commit**

```bash
git add opengpu/runpod.py tests/test_opengpu.py
git commit -m "feat: RunPod connection"
```

---

### Task 4: Parallel scan and ranking

**Files:**
- Create: `opengpu/scan.py`
- Test: `tests/test_opengpu.py` (add `TestScan`)

**Interfaces:**
- Consumes: `vast.fetch`, `runpod.fetch`, `Offer`, `matches`.
- Produces:
  - `PROVIDERS: dict[str, tuple[str, Callable[[str, int, float], list[Offer]]]] = {"vast": ("VAST_API_KEY", vast.fetch), "runpod": ("RUNPOD_API_KEY", runpod.fetch)}`
  - `@dataclass class ScanResult: offers: list[Offer]; skipped: list[str]; failed: dict[str, str]`
  - `scan(gpu_count: int, providers=PROVIDERS, env=os.environ, timeout: float = 20) -> ScanResult`
  - `rank(offers: list[Offer], query: str, tier: str | None = None, limit: int = 10) -> dict[tuple[str, int, str], list[Offer]]` — key `(gpu, vram_gb, tier)`

- [ ] **Step 1: Write the failing test**

```python
from opengpu.core import Offer
from opengpu.scan import scan, rank

def off(price, tier="interruptible", gpu="H100-SXM", provider="vast"):
    return Offer(provider, gpu, 1, 80, tier, price, "US", "", f"{provider}:{price}")

class TestScan(unittest.TestCase):
    def test_scan_isolates_failures_and_missing_keys(self):
        def ok(key, n, t): return [off(1.0)]
        def boom(key, n, t): raise TimeoutError("timed out")
        provs = {"a": ("A_KEY", ok), "b": ("B_KEY", boom), "c": ("C_KEY", ok)}
        r = scan(1, provs, env={"A_KEY": "secret", "B_KEY": "secret"})
        self.assertEqual(r.offers, [off(1.0)])
        self.assertEqual(r.skipped, ["c"])
        self.assertEqual(list(r.failed), ["b"])
        self.assertIn("timed out", r.failed["b"])
        self.assertNotIn("secret", r.failed["b"])

    def test_rank_separates_tiers_and_sorts(self):
        offers = [off(2.0), off(1.0), off(0.5, "on-demand"), off(0.1, gpu="RTX-4090")]
        groups = rank(offers, "h100")
        self.assertEqual(list(groups), [("H100-SXM", 80, "interruptible"), ("H100-SXM", 80, "on-demand")])
        self.assertEqual([o.price_per_gpu_hr for o in groups[("H100-SXM", 80, "interruptible")]], [1.0, 2.0])
        self.assertEqual(list(rank(offers, "h100", tier="on-demand")), [("H100-SXM", 80, "on-demand")])
        self.assertEqual(len(rank(offers, "h100", limit=1)[("H100-SXM", 80, "interruptible")]), 1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opengpu.scan'`

- [ ] **Step 3: Implement `opengpu/scan.py`**

- `scan`: providers whose env var is unset or empty → `skipped` (in `providers` order). Run the rest with `ThreadPoolExecutor`; each `fetch(key, gpu_count, timeout)`; on any exception set `failed[name] = f"{type(e).__name__}: {e}"[:80]`. Keep offers in provider order.
- `rank`: keep offers where `matches(query, o.gpu)` and (`tier` is None or equal); group by `(gpu, vram_gb, tier)`; sort each group by `price_per_gpu_hr`, cut to `limit`; order groups by `(gpu, vram_gb, 0 if tier == "interruptible" else 1)`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m unittest -v`
Expected: all tests OK

- [ ] **Step 5: Commit**

```bash
git add opengpu/scan.py tests/test_opengpu.py
git commit -m "feat: parallel scan and ranking"
```

---

### Task 5: Command line and live check

**Files:**
- Create: `opengpu/__main__.py`
- Test: `tests/test_opengpu.py` (add `TestCli`)

**Interfaces:**
- Consumes: `scan`, `rank`, `ScanResult`, `PROVIDERS`, `Offer`.
- Produces: `render(groups, gpu_count: int, result: ScanResult, scanned_at: datetime) -> str`; `main(argv: list[str] | None = None, scan_fn=scan) -> int`.

Output format (one deliberate change from the spec example: VRAM moves into the group header because groups are split by VRAM, e.g. A100 40GB vs 80GB):

```
H100-SXM 80GB × 1 · interruptible                    scanned 14:02 UTC
  $/GPU-hr  provider  region  fine print  offer
     1.120  vast      US      outbid → paused, ...  vast:42
<blank line between groups>
skipped: hyperbolic (no API key) · failed: shadeform (TimeoutError: timed out)
```

Prices `{:.3f}`; columns padded with `str.ljust` to the widest value in the group; footer only when something was skipped or failed.

- [ ] **Step 1: Write the failing test**

```python
import io, contextlib
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opengpu.__main__'`

- [ ] **Step 3: Implement `opengpu/__main__.py`**

- `argparse`: positional `gpu` (`nargs="+"`, joined with spaces so `opengpu RTX 4090` works), `--count` (int ≥ 1, default 1), `--tier` (`interruptible`|`on-demand`), `--limit` (int, default 10).
- `main`: `result = scan_fn(args.count)` (`scan` makes no network call for skipped providers). If every provider was skipped (`len(result.skipped) == len(PROVIDERS)`), print `No API keys set. Set one of: VAST_API_KEY, RUNPOD_API_KEY` to stderr, return 1. If `rank` returns nothing: print `No offers for {gpu} × {count}.` and `Seen: ` + sorted distinct `o.gpu` from all offers, plus the footer; return 1. Otherwise print `render(...)` with `datetime.now(timezone.utc)`, return 0.
- End with `if __name__ == "__main__": sys.exit(main())`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m unittest -v`
Expected: all tests OK

- [ ] **Step 5: Live check with real keys** (needs the user's `VAST_API_KEY` and `RUNPOD_API_KEY`)

Run: `python -m opengpu H100` and `python -m opengpu RTX 4090 --count 2`
Expected: groups from both providers, no `failed:` line; the top Vast interruptible price is within a few cents of the cheapest matching bid on cloud.vast.ai. Add any GPU names that show up split across providers (same card, different canonical names) to `ALIASES`, with a line in `test_canonical_gpu` for each.

- [ ] **Step 6: Commit**

```bash
git add opengpu/__main__.py opengpu/core.py tests/test_opengpu.py
git commit -m "feat: opengpu command line"
```
