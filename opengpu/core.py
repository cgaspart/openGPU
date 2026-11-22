import csv
import io
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
    "H100-HBM3": "H100-SXM",  # RunPod id "NVIDIA H100 80GB HBM3"
    # Only sold as SXM, and other providers name them without it.
    "H200-SXM": "H200", "B200-SXM": "B200", "B300-SXM": "B300", "GB200-SXM": "GB200", "GB300-SXM": "GB300",
}

# Vendor words and memory sizes ("80GB": VRAM is tracked separately) aren't part of the model name.
_NOISE = re.compile(r"\b(NVIDIA|GEFORCE|TESLA|\d+GB)\b")


def canonical_gpu(name: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", "-", _NOISE.sub("", name.upper())).strip("-")
    cleaned = re.sub(r"(\d)ADA\b", r"\1-ADA", cleaned)  # Vast "RTX 4000Ada" == "RTX 4000 Ada"
    cleaned = re.sub(r"-SXM\d\b", "-SXM", cleaned)  # SXM4/SXM5/SXM6 -> SXM
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


def get_json(url: str, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "opengpu/0.1", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def get_csv(url: str, timeout: float) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "opengpu/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return list(csv.DictReader(io.TextIOWrapper(resp, encoding="utf-8")))
