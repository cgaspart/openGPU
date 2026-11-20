import os
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from opengpu import azure, runpod, vast, verda
from opengpu.core import Offer, matches

PROVIDERS = {
    "vast": ("VAST_API_KEY", vast.fetch),
    "runpod": ("RUNPOD_API_KEY", runpod.fetch),
    "verda": (None, verda.fetch),
    "azure": (None, azure.fetch),
}


@dataclass
class ScanResult:
    offers: list[Offer]
    skipped: list[str]
    failed: dict[str, str]


def _reason(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        # The body says why (e.g. Vast answers a bad key with 404 "Invalid user key").
        try:
            body = " ".join(e.read(200).decode(errors="replace").split())
        except Exception:
            body = e.reason
        return f"HTTP {e.code}: {body}"[:120]
    return f"{type(e).__name__}: {e}"[:80]


def scan(gpu_count: int, providers=PROVIDERS, env=os.environ, timeout: float = 20, query: str = "") -> ScanResult:
    # var None = public prices, no key needed.
    keys = {name: env.get(var) if var else "" for name, (var, _) in providers.items()}
    skipped = [name for name, (var, _) in providers.items() if var and not keys[name]]
    active = [name for name in providers if name not in skipped]
    offers, failed = [], {}
    with ThreadPoolExecutor() as pool:
        futures = {name: pool.submit(providers[name][1], keys[name], gpu_count, timeout, query) for name in active}
        for name, fut in futures.items():
            try:
                offers += fut.result()
            except Exception as e:
                failed[name] = _reason(e)
    return ScanResult(offers, skipped, failed)


def rank(offers: list[Offer], query: str, tier: str | None = None, limit: int = 10) -> dict[tuple[str, int, str], list[Offer]]:
    groups: dict[tuple[str, int, str], list[Offer]] = {}
    for o in offers:
        if matches(query, o.gpu) and tier in (None, o.tier):
            groups.setdefault((o.gpu, o.vram_gb, o.tier), []).append(o)
    order = sorted(groups, key=lambda k: (k[0], k[1], 0 if k[2] == "interruptible" else 1))
    return {k: sorted(groups[k], key=lambda o: o.price_per_gpu_hr)[:limit] for k in order}
