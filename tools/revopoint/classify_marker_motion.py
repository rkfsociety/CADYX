#!/usr/bin/env python3
"""Classify image candidates by whether they move with a rotated turntable."""

import argparse
import csv
import math
import pathlib
import sys


def read_candidates(path, min_y, max_y):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"id", "cx_px", "cy_px"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path} must contain columns: id,cx_px,cy_px")
        candidates = []
        for line, row in enumerate(reader, start=2):
            try:
                marker_id = int(row["id"])
                x = float(row["cx_px"])
                y = float(row["cy_px"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path}:{line} has an invalid id or image center") from error
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError(f"{path}:{line} has a non-finite image center")
            if min_y is None or y >= min_y:
                if max_y is None or y <= max_y:
                    candidates.append((marker_id, x, y))
    if not candidates:
        raise ValueError(f"{path} contains no candidates")
    return candidates


def classify(before, after, stable_px, moving_px):
    pairs = []
    for before_index, (_, x0, y0) in enumerate(before):
        for after_index, (_, x1, y1) in enumerate(after):
            distance = math.hypot(x1 - x0, y1 - y0)
            if distance <= stable_px:
                pairs.append((distance, before_index, after_index))

    # Match near-identical centers one-to-one; stability alone does not prove background.
    pairs.sort()
    stable = {}
    used_after = set()
    for distance, before_index, after_index in pairs:
        if before_index not in stable and after_index not in used_after:
            stable[before_index] = (after_index, distance)
            used_after.add(after_index)

    result = []
    for before_index, (marker_id, x0, y0) in enumerate(before):
        if before_index in stable:
            after_index, distance = stable[before_index]
            status = "stable_in_image_candidate"
            after_id, x1, y1 = after[after_index]
        else:
            after_id, x1, y1 = min(
                after,
                key=lambda candidate: math.hypot(candidate[1] - x0, candidate[2] - y0),
            )
            distance = math.hypot(x1 - x0, y1 - y0)
            status = "moved_or_not_redetected" if distance >= moving_px else "ambiguous"
        result.append({
            "before_id": marker_id,
            "before_x_px": x0,
            "before_y_px": y0,
            "nearest_after_id": after_id,
            "nearest_after_x_px": x1,
            "nearest_after_y_px": y1,
            "nearest_distance_px": distance,
            "classification": status,
        })
    return result, len(used_after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=pathlib.Path,
                        help="candidate CSV captured before table rotation")
    parser.add_argument("--after", required=True, type=pathlib.Path,
                        help="candidate CSV captured after table rotation")
    parser.add_argument("--before-angle", required=True, type=float,
                        help="turntable angle in degrees at the before capture")
    parser.add_argument("--after-angle", required=True, type=float,
                        help="turntable angle in degrees at the after capture")
    parser.add_argument("--stable-px", type=float, default=5.0,
                        help="maximum displacement for a center that stays at the same image location")
    parser.add_argument("--moving-px", type=float, default=15.0,
                        help="minimum nearest-candidate displacement to classify as moving")
    parser.add_argument("--min-y-px", type=float,
                        help="optional inclusive top-image crop for both candidate sets")
    parser.add_argument("--max-y-px", type=float,
                        help="optional inclusive bottom-image crop for both candidate sets")
    parser.add_argument("--out-csv", required=True, type=pathlib.Path)
    args = parser.parse_args()

    if not math.isfinite(args.before_angle) or not math.isfinite(args.after_angle):
        parser.error("angles must be finite")
    if abs(args.after_angle - args.before_angle) < 2.0:
        parser.error("before and after poses must differ by at least 2 degrees")
    if (not math.isfinite(args.stable_px) or not math.isfinite(args.moving_px) or
            args.stable_px < 0 or args.moving_px <= args.stable_px):
        parser.error("pixel thresholds must be finite, nonnegative, and ordered")
    if ((args.min_y_px is not None and not math.isfinite(args.min_y_px)) or
            (args.max_y_px is not None and not math.isfinite(args.max_y_px)) or
            (args.min_y_px is not None and args.max_y_px is not None and args.min_y_px > args.max_y_px)):
        parser.error("image y-crop bounds must be finite and ordered")
    if args.out_csv.exists():
        parser.error(f"refusing to overwrite existing output: {args.out_csv}")

    before = read_candidates(args.before, args.min_y_px, args.max_y_px)
    after = read_candidates(args.after, args.min_y_px, args.max_y_px)
    rows, stable_after_count = classify(before, after, args.stable_px, args.moving_px)
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=(
            "before_id", "before_x_px", "before_y_px", "nearest_after_id",
            "nearest_after_x_px", "nearest_after_y_px", "nearest_distance_px", "classification",
        ))
        writer.writeheader()
        for row in rows:
            row["before_x_px"] = f"{row['before_x_px']:.3f}"
            row["before_y_px"] = f"{row['before_y_px']:.3f}"
            row["nearest_after_x_px"] = f"{row['nearest_after_x_px']:.3f}"
            row["nearest_after_y_px"] = f"{row['nearest_after_y_px']:.3f}"
            row["nearest_distance_px"] = f"{row['nearest_distance_px']:.3f}"
            writer.writerow(row)

    counts = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    print(f"Pose change: {args.before_angle:g} -> {args.after_angle:g} degrees")
    print(f"Candidates: before={len(before)}, after={len(after)}, stable one-to-one={stable_after_count}")
    if args.min_y_px is not None or args.max_y_px is not None:
        low = args.min_y_px if args.min_y_px is not None else "-inf"
        high = args.max_y_px if args.max_y_px is not None else "inf"
        print(f"Image crop: y={low}..{high} px")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    print(f"Wrote {args.out_csv}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"Marker motion classification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
