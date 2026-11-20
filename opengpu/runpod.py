from opengpu.core import Offer, canonical_gpu, get_json

# RunPod REST v2 (GraphQL is legacy and rejects new scoped keys). RunPod no longer sells spot
# pods (every spot price in GraphQL is null as of 2026-11-20), so all offers are on-demand.
URL = "https://api.runpod.io/v2/catalog/gpus?include=AVAILABILITY&product=POD&count={count}&cloud={cloud}"
CLOUDS = ("SECURE", "COMMUNITY")


def parse(gpus: list[dict], gpu_count: int, cloud: str) -> list[Offer]:
    out = []
    for g in gpus:
        stock = g.get("availability")
        price = (g.get("price") or {}).get(cloud.lower())  # per GPU per hour
        if stock in (None, "NONE") or not price or price <= 0:
            continue
        out.append(Offer(
            provider="runpod",
            gpu=canonical_gpu(g["name"]),
            gpu_count=gpu_count,
            vram_gb=g.get("memory") or 0,
            tier="on-demand",
            price_per_gpu_hr=price,
            region="-",
            fine_print=f"{cloud.lower()} cloud · stock {stock.lower()}",
            offer_id=f"runpod:{g['id']}",
        ))
    return out


def fetch(api_key: str, gpu_count: int, timeout: float, query: str) -> list[Offer]:
    # One call per cloud returns every GPU type with stock for this count; rank() filters by query.
    auth = {"Authorization": f"Bearer {api_key}"}
    return [o for cloud in CLOUDS
            for o in parse(get_json(URL.format(count=gpu_count, cloud=cloud), auth, timeout)["gpus"], gpu_count, cloud)]
