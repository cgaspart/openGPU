from opengpu.core import Offer, canonical_gpu, post_json

URL = "https://api.runpod.io/graphql"
QUERY = ("query { gpuTypes { id displayName memoryInGb securePrice communityPrice "
         "secureSpotPrice communitySpotPrice lowestPrice(input: {gpuCount: %d}) { stockStatus } } }")
SPOT_NOTE = " · ~5 s warning, volume kept"
# RunPod prices are already per GPU per hour.
PRICES = [
    ("securePrice", "on-demand", "secure cloud"),
    ("communityPrice", "on-demand", "community cloud"),
    ("secureSpotPrice", "interruptible", "secure cloud" + SPOT_NOTE),
    ("communitySpotPrice", "interruptible", "community cloud" + SPOT_NOTE),
]


def parse(gpu_types: list[dict], gpu_count: int) -> list[Offer]:
    out = []
    for t in gpu_types:
        # ponytail: stock checked per GPU type, not per secure/community cloud
        if not (t.get("lowestPrice") or {}).get("stockStatus"):
            continue
        for field, tier, note in PRICES:
            price = t.get(field)
            if not price or price <= 0:
                continue
            out.append(Offer(
                provider="runpod",
                gpu=canonical_gpu(t["displayName"]),
                gpu_count=gpu_count,
                vram_gb=t.get("memoryInGb") or 0,
                tier=tier,
                price_per_gpu_hr=price,
                region="-",
                fine_print=note,
                offer_id=f"runpod:{t['id']}",
            ))
    return out


def fetch(api_key: str, gpu_count: int, timeout: float) -> list[Offer]:
    reply = post_json(URL, {"query": QUERY % gpu_count}, {"Authorization": f"Bearer {api_key}"}, timeout)
    if reply.get("errors"):
        raise RuntimeError(reply["errors"][0].get("message", "GraphQL error"))
    return parse(reply["data"]["gpuTypes"], gpu_count)
