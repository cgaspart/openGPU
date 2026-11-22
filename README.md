# opengpu

Live GPU rental prices from several providers, cheapest first. Grouped by GPU, VRAM, and tier, so spot prices are never ranked against on-demand.

```sh
pip install -e .            # Python 3.11+, no dependencies
opengpu H100
opengpu RTX 4090 --count 2 --tier interruptible --limit 5
```

| Option | Default | |
|---|---|---|
| `--count N` | 1 | GPUs per machine |
| `--tier` | both | `interruptible` or `on-demand` |
| `--limit N` | 10 | offers per group |

Prices are per GPU-hour in USD. Storage, bandwidth, and interruption terms go in the `fine print` column, not in the price.

## Providers

| Provider | Key (env var) | Tiers | Notes |
|---|---|---|---|
| Vast.ai | none | interruptible, on-demand | Best-effort: Vast samples search results server-side |
| RunPod | `RUNPOD_API_KEY` | on-demand | RunPod no longer sells spot pods |
| Verda (DataCrunch) | none | interruptible, on-demand | Stock not checked |
| Azure | none | interruptible, on-demand | Needs quota on your subscription |

Keys come from the environment. Providers without a key are skipped, and a failing provider doesn't stop the scan. Both are listed under the results.

```sh
set -a; . ./.env; set +a    # load keys from .env
```

## Tests

```sh
python -m unittest
```
