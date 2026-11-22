import re

from opengpu.core import Offer, canonical_gpu, get_csv
from opengpu.vast import vram_gb

# ponytail: AWS and GCP prices from SkyPilot's public catalog, no key needed. The same prices SkyPilot picks
# clouds by. AWS rows refresh ~2x/day; GCP spot prices change at most monthly. Live upgrade: AWS
# DescribeSpotPriceHistory (needs AWS credentials) / GCP Cloud Billing API (needs an API key).
# SkyPilot bumps the catalog version now and then and the old one stops updating: watch for v9.
URL = "https://raw.githubusercontent.com/skypilot-org/skypilot-catalog/master/catalogs/v8/{cloud}/vms.csv"
TIERS = (("interruptible", "SpotPrice"), ("on-demand", "Price"))

# Catalog names that leave out the form factor. AWS and GCP only sell these as SXM / server cards.
NAMES = {"H100": "H100 SXM", "H100-MEGA": "H100 SXM", "A100": "A100 SXM", "A100-80GB": "A100 SXM",
         "RTXPRO6000": "RTX PRO 6000 S"}

NOTES = {
    "aws": {"interruptible": "2 min notice · needs spot quota · price ≤1 day old", "on-demand": "needs quota"},
    "gcp": {"interruptible": "30 s notice · needs spot quota", "on-demand": "needs quota"},
}

# GCP prices a GPU apart from the VM it's attached to. (catalog GPU name, count) -> VM it ships in.
# Anything else (T4, V100, P4) attaches to a general N1 VM.
GCP_HOSTS = {
    **{("H100", n): f"a3-highgpu-{n}g" for n in (1, 2, 4, 8)},
    ("H100-MEGA", 8): "a3-megagpu-8g", ("H200", 8): "a3-ultragpu-8g", ("B200", 8): "a4-highgpu-8g",
    **{("A100", n): f"a2-highgpu-{n}g" for n in (1, 2, 4, 8)}, ("A100", 16): "a2-megagpu-16g",
    **{("A100-80GB", n): f"a2-ultragpu-{n}g" for n in (1, 2, 4, 8)},
    **{("L4", n): f"g2-standard-{c}" for n, c in ((1, 4), (2, 24), (4, 48), (8, 96))},
    **{("RTXPRO6000", n): f"g4-standard-{c}" for n, c in ((1, 48), (2, 96), (4, 192), (8, 384))},
}
GCP_N1_HOST = "n1-standard-8"


def _offer(cloud: str, row: dict, gpu_count: int, tier: str, machine_price: float, machine: str) -> Offer:
    name = row["AcceleratorName"]
    mib = re.search(r"'SizeInMiB': (\d+)", row["GpuInfo"])  # first one is per GPU
    return Offer(cloud, canonical_gpu(NAMES.get(name, name)), gpu_count, vram_gb(int(mib[1])) if mib else 0,
                 tier, machine_price / gpu_count, row["Region"], NOTES[cloud][tier], f"{cloud}:{machine}")


def _cheapest(offers: list[Offer]) -> list[Offer]:
    """One offer per machine, region and tier: the catalog lists every zone."""
    best: dict = {}
    for o in offers:
        k = (o.offer_id, o.region, o.tier)
        if k not in best or o.price_per_gpu_hr < best[k].price_per_gpu_hr:
            best[k] = o
    return list(best.values())


def parse_aws(rows: list[dict], gpu_count: int) -> list[Offer]:
    out = []
    for r in rows:
        # GpuInfo names the maker; Inferentia, Trainium and FPGAs aren't NVIDIA.
        if "NVIDIA" not in r["GpuInfo"] or float(r["AcceleratorCount"] or 0) != gpu_count:
            continue
        for tier, field in TIERS:
            if float(r[field] or 0) > 0:
                out.append(_offer("aws", r, gpu_count, tier, float(r[field]), r["InstanceType"]))
    return _cheapest(out)


def parse_gcp(rows: list[dict], gpu_count: int) -> list[Offer]:
    vms = {(r["InstanceType"], r["Region"]): r for r in rows if r["InstanceType"]}
    out = []
    for r in rows:
        name = r["AcceleratorName"]
        if r["InstanceType"] or not name or name.startswith("tpu") or float(r["AcceleratorCount"]) != gpu_count:
            continue
        host = GCP_HOSTS.get((name, gpu_count), GCP_N1_HOST)
        vm = vms.get((host, r["Region"]))
        for tier, field in TIERS:
            # A missing VM price would show the GPU alone as the machine price: skip instead.
            if vm and vm[field] != "" and float(r[field] or 0) > 0:
                out.append(_offer("gcp", r, gpu_count, tier, float(r[field]) + float(vm[field]), host))
    return _cheapest(out)


def aws(api_key: str, gpu_count: int, timeout: float, query: str) -> list[Offer]:
    return parse_aws(get_csv(URL.format(cloud="aws"), timeout), gpu_count)


def gcp(api_key: str, gpu_count: int, timeout: float, query: str) -> list[Offer]:
    return parse_gcp(get_csv(URL.format(cloud="gcp"), timeout), gpu_count)
