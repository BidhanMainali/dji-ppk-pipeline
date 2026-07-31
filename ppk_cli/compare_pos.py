"""Compare two RTKLIB/Emlid Studio .pos files epoch by epoch.

Usage:
    python compare_pos.py <reference.pos> <test.pos> [--label-a ES] [--label-b CLI]

Reports Q (fix/float) breakdowns, a Q agreement matrix, and per-epoch
position deltas in metres (ENU), split by fix status. Fails loudly if the
files share no common epochs.
"""
import argparse
import math


def parse_pos(path):
    """Return {time_string: (lat, lon, height, q)} from a .pos file."""
    epochs = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("%") or not line.strip():
                continue
            parts = line.split()
            # GPST "YYYY/MM/DD HH:MM:SS.sss" then lat lon h Q ns ...
            t = parts[0] + " " + parts[1]
            lat, lon, h = float(parts[2]), float(parts[3]), float(parts[4])
            q = int(parts[5])
            epochs[t] = (lat, lon, h, q)
    if not epochs:
        raise SystemExit(f"ERROR: no solution epochs parsed from {path}")
    return epochs


QNAME = {1: "FIX", 2: "FLOAT", 3: "SBAS", 4: "DGPS", 5: "SINGLE", 6: "PPP"}


def qbreakdown(epochs, label):
    """Print how many epochs are FIX/FLOAT/etc. for one file; return the counts."""
    counts = {}
    for _, _, _, q in epochs.values():
        counts[q] = counts.get(q, 0) + 1
    total = len(epochs)
    print(f"\n{label}: {total} epochs")
    for q in sorted(counts):
        print(f"  {QNAME.get(q, q):6s} {counts[q]:5d}  ({100*counts[q]/total:.2f}%)")
    return counts


def enu_delta(a, b, lat0):
    """Position delta b-a in metres (E, N, U) for nearby points."""
    dlat = b[0] - a[0]
    dlon = b[1] - a[1]
    dn = dlat * 111132.95
    de = dlon * 111319.49 * math.cos(math.radians(lat0))
    du = b[2] - a[2]
    return de, dn, du


def stats(vals):
    """Return (mean, rms, max) of a list of magnitudes — the summary trio used
    throughout the reports (also imported by compare_tags.py)."""
    n = len(vals)
    mean = sum(vals) / n
    rms = math.sqrt(sum(v * v for v in vals) / n)
    return mean, rms, max(vals)


def report_deltas(pairs, title):
    """Print horizontal and vertical mean/rms/max (in cm) for a set of ENU deltas."""
    if not pairs:
        print(f"\n{title}: no epochs")
        return
    h = [math.hypot(de, dn) for de, dn, du in pairs]
    v = [abs(du) for de, dn, du in pairs]
    hm, hr, hx = stats(h)
    vm, vr, vx = stats(v)
    print(f"\n{title} ({len(pairs)} epochs)")
    print(f"  horizontal  mean {hm*100:7.2f} cm   rms {hr*100:7.2f} cm   max {hx*100:7.2f} cm")
    print(f"  vertical    mean {vm*100:7.2f} cm   rms {vr*100:7.2f} cm   max {vx*100:7.2f} cm")


def main():
    """CLI entry point: compare two .pos files epoch-by-epoch and report deltas."""
    p = argparse.ArgumentParser()
    p.add_argument("pos_a")
    p.add_argument("pos_b")
    p.add_argument("--label-a", default="A")
    p.add_argument("--label-b", default="B")
    ns = p.parse_args()
    label_a, label_b = ns.label_a, ns.label_b

    ea, eb = parse_pos(ns.pos_a), parse_pos(ns.pos_b)
    qbreakdown(ea, f"[{label_a}] {ns.pos_a}")
    qbreakdown(eb, f"[{label_b}] {ns.pos_b}")

    common = sorted(set(ea) & set(eb))
    only_a, only_b = len(ea) - len(common), len(eb) - len(common)
    print(f"\nCommon epochs: {len(common)}   only-{label_a}: {only_a}   only-{label_b}: {only_b}")
    if not common:
        raise SystemExit("ERROR: no common epochs — time formats may differ")

    # Q agreement matrix
    matrix = {}
    for t in common:
        key = (ea[t][3], eb[t][3])
        matrix[key] = matrix.get(key, 0) + 1
    print(f"\nQ agreement ({label_a} -> {label_b}):")
    for (qa, qb), n in sorted(matrix.items()):
        print(f"  {QNAME.get(qa, qa):6s} -> {QNAME.get(qb, qb):6s} {n:5d}")

    lat0 = ea[common[0]][0]
    both_fix, a_fix_b_not, mixed_rest = [], [], []
    for t in common:
        d = enu_delta(ea[t], eb[t], lat0)
        qa, qb = ea[t][3], eb[t][3]
        if qa == 1 and qb == 1:
            both_fix.append(d)
        elif qa == 1:
            a_fix_b_not.append(d)
        else:
            mixed_rest.append(d)

    report_deltas(both_fix, f"Deltas where BOTH FIX  <-- the number that matters")
    report_deltas(a_fix_b_not, f"Deltas where {label_a} FIX but {label_b} not")
    report_deltas(mixed_rest, f"Deltas where {label_a} FLOAT/other")

    # large-delta callout in both-fix epochs
    bad = [(t, enu_delta(ea[t], eb[t], lat0)) for t in common
           if ea[t][3] == 1 and eb[t][3] == 1]
    bad = [(t, d) for t, d in bad if math.hypot(d[0], d[1]) > 0.05 or abs(d[2]) > 0.10]
    if bad:
        print(f"\nWARNING: {len(bad)} both-FIX epochs exceed 5 cm horiz / 10 cm vert:")
        for t, d in bad[:10]:
            print(f"  {t}  dE {d[0]*100:6.1f} cm  dN {d[1]*100:6.1f} cm  dU {d[2]*100:6.1f} cm")
        if len(bad) > 10:
            print(f"  ... and {len(bad)-10} more")
    else:
        print("\nAll both-FIX epochs agree within 5 cm horizontal / 10 cm vertical.")


if __name__ == "__main__":
    main()
