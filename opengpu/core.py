import json
import re
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Offer:
    provider: str
    gpu: str
    gpu_count: int
    vram_gb: int
    tier: str  # "interruptible" | "on-demand"
    price_per_gpu_hr: float
    region: str
    fine_print: str
    offer_id: str


# Cleaned provider name -> canonical name. Grow from names seen in live scans.
ALIASES = {
    "H100-80GB-HBM3": "H100-SXM",
    "H100-SXM5": "H100-SXM",
    "A100-SXM4": "A100-SXM",
    "RTX-6000ADA": "RTX-6000-ADA",
}

_NOISE = re.compile(r"\b(NVIDIA|GEFORCE|TESLA)\b")


def canonical_gpu(name: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", "-", _NOISE.sub("", name.upper())).strip("-")
    return ALIASES.get(cleaned, cleaned)


def matches(query: str, gpu: str) -> bool:
    return set(canonical_gpu(query).split("-")) <= set(gpu.split("-"))


def post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "opengpu/0.1", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)
