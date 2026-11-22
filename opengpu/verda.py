from opengpu.core import Offer, canonical_gpu, get_json

# Public list, no key. Stock (/v1/instance-availability) needs an OAuth login, so it isn't checked.
URL = "https://api.datacrunch.io/v1/instance-types"
NOTES = {"interruptible": "can be removed any time · stock not checked", "on-demand": "stock not checked"}


def parse(types: list[dict], gpu_count: int) -> list[Offer]:
    out = []
    for t in types:
        n = (t.get("gpu") or {}).get("number_of_gpus") or 0
        if not n or n != gpu_count:
            continue
        locations = t.get("location_prices") or [{"location_code": "-", **t}]
        for loc in locations:
            for tier, field in (("interruptible", "spot_price"), ("on-demand", "price_per_hour")):
                price = float(loc.get(field) or 0)
                if price <= 0:
                    continue
                out.append(Offer(
                    provider="verda",
                    gpu=canonical_gpu(t["name"]),
                    gpu_count=n,
                    vram_gb=round(((t.get("gpu_memory") or {}).get("size_in_gigabytes") or 0) / n),  # total -> per GPU
                    tier=tier,
                    price_per_gpu_hr=price / n,
                    region=loc["location_code"],
                    fine_print=NOTES[tier],
                    offer_id=f"verda:{t['instance_type']}",
                ))
    return out


def fetch(api_key: str, gpu_count: int, timeout: float, query: str) -> list[Offer]:
    return parse(get_json(URL, {}, timeout), gpu_count)
