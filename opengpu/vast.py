from opengpu.core import Offer, canonical_gpu, matches, post_json

URL = "https://console.vast.ai/api/v0/bundles/"
PRICE_FIELD = {"interruptible": "min_bid", "on-demand": "dph_total"}
SEARCH_TYPE = {"interruptible": "bid", "on-demand": "on-demand"}

# ponytail: snapshot of Vast gpu_name values (2026-11-20). Vast returns at most 64 offers per
# search and only filters names by exact match, so we send the matching names. Refresh by paging
# /bundles/ ordered by gpu_name with gpu_name > last seen.
NAMES = (
    "A10", "A100 PCIE", "A100 SXM4", "A16", "A30X", "A40", "A800 PCIE", "B200", "B300", "CMP 170HX",
    "GB10", "GB200", "GB300", "GTX 1060", "GTX 1070", "GTX 1080", "GTX 1080 Ti", "GTX 1650", "GTX 1660",
    "GTX 1660 S", "GTX 1660 Ti", "GTX TITAN X", "H100 NVL", "H100 PCIE", "H100 SXM", "H200", "H200 NVL",
    "L4", "L40", "L40S", "Q RTX 5000", "Q RTX 6000", "Q RTX 8000", "Quadro P4000", "RTX 2000Ada",
    "RTX 2060S", "RTX 2070", "RTX 2070S", "RTX 2080 Ti", "RTX 3050", "RTX 3060", "RTX 3060 Ti",
    "RTX 3060 laptop", "RTX 3070 Ti", "RTX 3080", "RTX 3080 Ti", "RTX 3090", "RTX 3090 Ti", "RTX 4000Ada",
    "RTX 4060", "RTX 4060 Ti", "RTX 4070", "RTX 4070 Ti", "RTX 4070 laptop", "RTX 4070S", "RTX 4070S Ti",
    "RTX 4080", "RTX 4080 laptop", "RTX 4080S", "RTX 4090", "RTX 4500Ada", "RTX 5000Ada", "RTX 5060",
    "RTX 5060 Ti", "RTX 5070", "RTX 5070 Ti", "RTX 5080", "RTX 5090", "RTX 5090D", "RTX 5880Ada",
    "RTX 6000Ada", "RTX 6000D", "RTX A2000", "RTX A4000", "RTX A4500", "RTX A5000", "RTX A6000",
    "RTX PRO 4000", "RTX PRO 4500", "RTX PRO 5000", "RTX PRO 6000 Max-Q", "RTX PRO 6000 S",
    "RTX PRO 6000 WS", "T4G", "Tesla P100", "Tesla P40", "Tesla T4", "Tesla V100", "Tesla V100S",
    "Titan RTX", "Titan V",
)


def gpu_names(query: str) -> list[str]:
    """Vast gpu_name values matching the query; the raw query itself if none are known."""
    return [n for n in NAMES if matches(query, canonical_gpu(n))] or [query.strip()]


def parse(offers: list[dict], tier: str) -> list[Offer]:
    out = []
    for o in offers:
        price, gpus = o.get(PRICE_FIELD[tier]), o.get("num_gpus")
        if not price or price <= 0 or not gpus:
            continue
        notes = ["outbid → paused, disk kept"] if tier == "interruptible" else []
        if o.get("reliability2") is not None:
            notes.append(f"reliability {o['reliability2']:.2f}")
        notes.append("+bandwidth fees")
        out.append(Offer(
            provider="vast",
            gpu=canonical_gpu(o["gpu_name"]),
            gpu_count=gpus,
            vram_gb=round((o.get("gpu_ram") or 0) / 1024),
            tier=tier,
            price_per_gpu_hr=price / gpus,
            region=(o.get("geolocation") or "-").split(",")[-1].strip(),
            fine_print=" · ".join(notes),
            offer_id=f"vast:{o['id']}",
        ))
    return out


def fetch(api_key: str, gpu_count: int, timeout: float, query: str) -> list[Offer]:
    auth = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    offers = []
    for tier, search_type in SEARCH_TYPE.items():
        body = {
            "verified": {"eq": True}, "external": {"eq": False},
            "rentable": {"eq": True}, "rented": {"eq": False},
            "num_gpus": {"eq": gpu_count}, "gpu_name": {"in": gpu_names(query)}, "type": search_type,
            "order": [["dph_total", "asc"]], "limit": 64, "allocated_storage": 5.0,
        }
        reply = post_json(URL, body, auth, timeout)
        offers += parse(reply["offers"], tier)
    return offers
