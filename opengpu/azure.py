import urllib.parse

from opengpu.core import Offer, canonical_gpu, get_json, matches

# Azure Retail Prices API: official, public, no key.
URL = "https://prices.azure.com/api/retail/prices?"

# ponytail: whole-GPU VM sizes only (fractional NV/RTX PRO v6 sizes skipped). Add rows as Azure ships new ones.
SKUS = {  # VM size: (GPU, GPUs per VM, GB per GPU)
    "Standard_NC40ads_H100_v5": ("H100 NVL", 1, 94),
    "Standard_NC80adis_H100_v5": ("H100 NVL", 2, 94),
    "Standard_ND96isr_H100_v5": ("H100 SXM", 8, 80),
    "Standard_ND96isr_H200_v5": ("H200", 8, 141),
    "Standard_NC24ads_A100_v4": ("A100 PCIE", 1, 80),
    "Standard_NC48ads_A100_v4": ("A100 PCIE", 2, 80),
    "Standard_NC96ads_A100_v4": ("A100 PCIE", 4, 80),
    "Standard_ND96asr_v4": ("A100 SXM", 8, 40),
    "Standard_ND96amsr_A100_v4": ("A100 SXM", 8, 80),
    "Standard_ND96isr_MI300X_v5": ("MI300X", 8, 192),
    "Standard_NC4as_T4_v3": ("T4", 1, 16),
    "Standard_NC64as_T4_v3": ("T4", 4, 16),
    "Standard_NC6s_v3": ("V100", 1, 16),
    "Standard_NC12s_v3": ("V100", 2, 16),
    "Standard_NC24s_v3": ("V100", 4, 16),
    "Standard_NV36ads_A10_v5": ("A10", 1, 24),
    "Standard_NV72ads_A10_v5": ("A10", 2, 24),
}
NOTES = {"interruptible": "evicted with ~30 s notice · needs spot quota", "on-demand": "needs quota"}


def skus_for(query: str, gpu_count: int) -> list[str]:
    return [s for s, (gpu, n, _) in SKUS.items() if n == gpu_count and matches(query, canonical_gpu(gpu))]


def parse(items: list[dict]) -> list[Offer]:
    out = []
    for it in items:
        meter = it.get("meterName", "")
        if it.get("armSkuName") not in SKUS or "Windows" in it.get("productName", "") \
                or "Low Priority" in meter or it.get("unitOfMeasure") != "1 Hour" or not it.get("retailPrice"):
            continue
        gpu, n, vram = SKUS[it["armSkuName"]]
        tier = "interruptible" if meter.endswith(" Spot") else "on-demand"
        out.append(Offer(
            provider="azure",
            gpu=canonical_gpu(gpu),
            gpu_count=n,
            vram_gb=vram,
            tier=tier,
            price_per_gpu_hr=round(it["retailPrice"] / n, 4),
            region=it.get("armRegionName") or "-",
            fine_print=NOTES[tier],
            offer_id=f"azure:{it['armSkuName']}",
        ))
    return out


def fetch(api_key: str, gpu_count: int, timeout: float, query: str) -> list[Offer]:
    skus = skus_for(query, gpu_count)
    if not skus:
        return []
    names = " or ".join(f"armSkuName eq '{s}'" for s in skus)
    url = URL + urllib.parse.urlencode(
        {"$filter": f"serviceName eq 'Virtual Machines' and priceType eq 'Consumption' and ({names})"})
    items = []
    for _ in range(20):  # pages of up to 1000 items
        page = get_json(url, {}, timeout)
        items += page.get("Items") or []
        url = page.get("NextPageLink")
        if not url:
            break
    return parse(items)
