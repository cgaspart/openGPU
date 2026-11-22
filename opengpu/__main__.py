import argparse
import sys
from datetime import datetime, timezone

from opengpu import vast
from opengpu.core import canonical_gpu
from opengpu.scan import PROVIDERS, ScanResult, rank, scan

COLUMNS = ["$/GPU-hr", "provider", "region", "fine print", "offer"]


def _footer(result: ScanResult) -> str:
    parts = []
    if result.skipped:
        parts.append("skipped: " + ", ".join(f"{n} (set {PROVIDERS[n][0]})" for n in result.skipped))
    if result.failed:
        parts.append("failed: " + ", ".join(f"{n} ({why})" for n, why in result.failed.items()))
    return " · ".join(parts)


def render(groups, gpu_count: int, result: ScanResult, scanned_at: datetime) -> str:
    lines = []
    for i, ((gpu, vram, tier), offers) in enumerate(groups.items()):
        if i:
            lines.append("")
        header = f"{gpu} {vram}GB × {gpu_count} · {tier}"
        lines.append(f"{header:<52} scanned {scanned_at:%H:%M} UTC" if i == 0 else header)
        rows = [[f"{o.price_per_gpu_hr:.3f}", o.provider, o.region, o.fine_print, o.offer_id] for o in offers]
        widths = [max(len(r[c]) for r in rows + [COLUMNS]) for c in range(len(COLUMNS))]
        for r in [COLUMNS] + rows:
            cells = [r[0].rjust(widths[0])] + [r[c].ljust(widths[c]) for c in range(1, len(r))]
            lines.append("  " + "  ".join(cells).rstrip())
    footer = _footer(result)
    if footer:
        lines += ["", footer]
    return "\n".join(lines)


def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n


def main(argv: list[str] | None = None, scan_fn=scan) -> int:
    p = argparse.ArgumentParser(prog="opengpu", description="Cheapest live GPU rental prices.")
    p.add_argument("gpu", nargs="+", help="GPU model, e.g. H100, 'RTX 4090', a100 sxm")
    p.add_argument("--count", type=_positive, default=1, help="GPUs per machine (default 1)")
    p.add_argument("--tier", choices=["interruptible", "on-demand"])
    p.add_argument("--limit", type=_positive, default=10, help="offers per group (default 10)")
    args = p.parse_args(argv)
    gpu = " ".join(args.gpu)

    result = scan_fn(args.count, query=gpu)
    groups = rank(result.offers, gpu, args.tier, args.limit)
    if not groups:
        print(f"No offers for {gpu} × {args.count}.")
        known = {o.gpu for o in result.offers} | {canonical_gpu(n) for n in vast.NAMES}
        print("Known GPUs: " + ", ".join(sorted(known)))
        if _footer(result):
            print(_footer(result))
        return 1

    print(render(groups, args.count, result, datetime.now(timezone.utc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
