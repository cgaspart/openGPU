from opengpu.core import Offer, canonical_gpu, post_json

URL = "https://console.vast.ai/api/v0/bundles/"
PRICE_FIELD = {"interruptible": "min_bid", "on-demand": "dph_total"}
SEARCH_TYPE = {"interruptible": "bid", "on-demand": "on-demand"}


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


def fetch(api_key: str, gpu_count: int, timeout: float) -> list[Offer]:
    offers = []
    for tier, search_type in SEARCH_TYPE.items():
        # ponytail: cheapest 5000 offers across all GPU models per tier; filter gpu_name server-side if rare GPUs get cut off
        body = {
            "verified": {"eq": True}, "external": {"eq": False},
            "rentable": {"eq": True}, "rented": {"eq": False},
            "num_gpus": {"eq": gpu_count}, "type": search_type,
            "order": [["dph_total", "asc"]], "limit": 5000, "allocated_storage": 5.0,
        }
        reply = post_json(URL, body, {"Authorization": f"Bearer {api_key}"}, timeout)
        offers += parse(reply["offers"], tier)
    return offers
