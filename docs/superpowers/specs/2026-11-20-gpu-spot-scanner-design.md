# GPU spot price scanner — design

Date: 2026-11-20 · Status: draft, awaiting approval

## Intent

GPU rental prices move constantly, especially on spot/interruptible markets
(Vast.ai bids, RunPod spot). We want one command that asks every provider for
live prices and shows the cheapest comparable offer right now.

This is step 1. Step 2 (later, separate design) is a worker that runs jobs on
the cheapest spot GPU via SkyPilot and moves them when interrupted. SkyPilot
already handles launching and failover, but it picks clouds from cached price
lists (refreshed every 7 h at best; never auto-refreshed for Vast, Shadeform,
Hyperbolic, Fluidstack, Nebius) and launches the first matching Vast offer, not
the cheapest. Live prices from this scanner fill that gap.

## Goals

- `opengpu <GPU>` prints live offers, cheapest first, from all configured providers.
- Apples to apples: same GPU model, count, tier, priced per GPU-hour.
- Spot/interruptible is the focus; on-demand shown alongside for comparison.
- MVP is done when Vast.ai and RunPod work end to end.

## Non-goals (for now)

- Renting or launching anything (that's step 2, via SkyPilot).
- Price history, alerts, web UI, database.
- GPU arbitrage/resale (parked; needs price history first).

## Anti-goals

- Ranking spot against on-demand as if they were the same thing.
- Hiding fine print that makes a cheap offer not actually cheap
  (minimums, extra fees, interruption terms).
- One provider failing or being slow breaks the whole scan.

## Constraints

- Every provider needs an account + API key to read prices (user is creating them).
- Provider APIs, names and fields differ and change; each connection is isolated.

## Approaches considered

- **A. Run SkyPilot's catalog fetchers on a timer.** Least code, output feeds
  SkyPilot directly. Rejected: fetchers are lossy (Vast fetcher buckets machines
  and keeps the *highest* min bid per bucket; Shadeform fetcher records no spot
  price), no interruption terms, SkyPilot clouds only.
- **B. Own scanner, one small connection per provider — CHOSEN.** We control
  what "cheapest" means, keep fine print, can cover providers SkyPilot can't
  launch on (display only). Costs one short file per provider.
- **C. Aggregators only (Shadeform + Prime Intellect).** Two integrations, but
  mostly on-demand and no Shadeform spot prices. Rejected: misses the spot
  market, which is the point.

## Decided by the user

- MVP; user creates accounts on all relevant platforms.
- Compare apples to apples.
- Spot/interruptible is where the savings are; interruptions are acceptable.
- Scanner first; SkyPilot for the later worker.
- Approach B.

## Calls I made (correct any)

- **Python**, same as SkyPilot, for an easy hand-off in step 2.
- **Terminal output**, no storage. Example:

  ```
  $ opengpu H100 --count 1
  H100-SXM × 1 · interruptible                         scanned 14:02 UTC
   $/GPU-hr  provider       region  VRAM  fine print
     1.12    vast           US      80GB  outbid → paused, disk kept · reliability 0.97 · +bandwidth fees
     1.29    runpod         CA      80GB  ~5 s warning, volume kept
  H100-SXM × 1 · on-demand
     2.10    runpod         US      80GB
  skipped: hyperbolic (no API key) · failed: shadeform (timeout)
  ```

- **Common offer shape** every connection returns: provider, gpu (canonical
  name), gpu_count, vram_gb, tier (`interruptible` | `on-demand`),
  price_per_gpu_hr (USD), region, fine_print (short text), link or offer id.
- **GPU names** cleaned by one shared mapping table (e.g. "H100 80GB HBM3",
  "H100 SXM5" → `H100-SXM`; PCIe vs SXM kept distinct). Unmapped names pass
  through unchanged so nothing silently disappears.
- **Price** = provider's hourly price for the machine ÷ GPU count. Extra fees
  (storage, bandwidth) and minimums (Fluence 20 min, CoreWeave 8-GPU nodes,
  SF Compute blocks) go in fine print, not in the price.
- SF Compute is listed as `on-demand` with its block length in fine print
  (market-priced but not interruptible once bought).
- **Providers queried in parallel**, each with its own timeout; missing API key
  → skipped, error → listed as failed; the rest still print.
- **API keys from environment variables** (e.g. `VAST_API_KEY`, `RUNPOD_API_KEY`).
- **Filters:** GPU model (required), `--count`, `--tier`, `--limit` (default 10 per tier).
- **Provider order:** Vast, RunPod (MVP) → Verda spot, Nebius spot, Hyperbolic,
  Shadeform, SF Compute, TensorDock, Prime Intellect, then the rest.
- One small test covering name cleanup, per-GPU pricing and tier separation.

## Left to the builder

- Exact API calls and field mapping per provider (verify against current docs).
- Contents of the GPU name mapping table.
