import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# Canonical display order and internal key -> display label (only these five conditions)
DISPLAY_ORDER = [
    "Baseline",
    "Watermarked",
    "JPEG Compression (Q80)",
    "Crop 10% + Resize",
    "Screenshot Simulation",
]
INTERNAL_TO_DISPLAY = {
    "Original": "Baseline",
    "Watermarked": "Watermarked",
    "Crop_signed_human": "Crop 10% + Resize",
    "Jpeg_signed_human": "JPEG Compression (Q80)",
    "Social_signed_human": "Screenshot Simulation",
}
DISPLAY_TO_INTERNAL = {v: k for k, v in INTERNAL_TO_DISPLAY.items()}

def extract_attack_label(path: str) -> str:
    """Derive internal key from an attack result filename (Crop, Jpeg, Social)."""
    basename = os.path.basename(path)
    match = re.search(r"attack[_-]?(\w+)", basename, re.IGNORECASE)
    if match:
        name = match.group(1)
        return name.capitalize()
    return os.path.splitext(basename)[0]


def collect_condition_arrays(
    not_signed_path: str,
    attack_paths: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, List[float]]:
    """
    Return a dict mapping condition name -> list of bit accuracy values.
    - "Original": bit_accuracy_original from not_signed
    - "Watermarked": bit_accuracy_watermarked from not_signed
    - For each (path, label) in attack_paths: label -> bit_accuracy_watermarked from that JSON
    """
    out: Dict[str, List[float]] = {}
    attack_paths = attack_paths or []

    not_signed = load_json(not_signed_path)
    orig_vals: List[float] = []
    wm_vals: List[float] = []
    for entry in not_signed.get("images", []):
        o = entry.get("bit_accuracy_original")
        w = entry.get("bit_accuracy_watermarked")
        if o is not None:
            orig_vals.append(float(o))
        if w is not None:
            wm_vals.append(float(w))
    if orig_vals:
        out["Original"] = orig_vals
    if wm_vals:
        out["Watermarked"] = wm_vals

    for path, label in attack_paths:
        data = load_json(path)
        vals: List[float] = []
        for entry in data.get("images", []):
            w = entry.get("bit_accuracy_watermarked")
            if w is not None:
                vals.append(float(w))
        if vals:
            out[label] = vals

    return out

def plot_distributions(
    condition_arrays: Dict[str, List[float]],
    threshold: float,
    output_path: str,
) -> None:
    """Draw bit-accuracy distributions and threshold line; save to output_path."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib
        matplotlib.use("Agg")
    except ImportError:
        raise ImportError("Plotting requires matplotlib. Install with: pip install matplotlib") from None

    fig, ax = plt.subplots(figsize=(8, 5))
    fig.set_facecolor("white")

    # Order and display labels: Baseline, Watermarked, Crop 10% + Resize, JPEG Compression (Q80), Screenshot Simulation
    order = [d for d in DISPLAY_ORDER if DISPLAY_TO_INTERNAL.get(d) in condition_arrays]
    for k in sorted(condition_arrays):
        if k not in INTERNAL_TO_DISPLAY:
            order.append(k)
    display_labels = list(order)  # order is already display labels (or key for custom)
    internal_order = [DISPLAY_TO_INTERNAL.get(d, d) for d in order]
    data = [condition_arrays[k] for k in internal_order]
    positions = list(range(len(order)))

    parts = ax.violinplot(
        data,
        positions=positions,
        showmeans=False,
        showmedians=False,
        widths=0.75,
    )
    # Style violins with tab10 colors; hide central bars and min/max lines
    colors = list(plt.cm.tab10.colors)
    for i, pc in enumerate(parts["bodies"]):
        pc.set_facecolor(colors[i % len(colors)])
        pc.set_alpha(0.7)
    for partname in ("cbars", "cmins", "cmaxes", "cmeans", "cmedians"):
        if partname in parts:
            parts[partname].set_visible(False)

    ax.axhline(y=threshold, color="black", linestyle="--", linewidth=1.5, label=f"Threshold ({threshold})")
    ax.set_xticks(positions)
    ax.set_xticklabels(display_labels, rotation=15, ha="right", fontsize=12)
    ax.set_xlabel("Perturbation", fontsize=14)
    ax.set_ylabel("Bit Accuracy", fontsize=14)
    ax.tick_params(axis="both", labelsize=12)
    ax.legend(loc="lower right", fontsize=12)
    ax.set_ylim(0, 1.05)
    ax.set_xlim(-0.5, len(positions) - 0.5)

    plt.tight_layout()
    plt.savefig(output_path, bbox_inches="tight")
    plt.close(fig)

def main() -> None:
    parser = argparse.ArgumentParser(description="Plot bit-accuracy distributions for original, watermarked, and attack conditions with threshold line.")
    parser.add_argument("--not-signed", required=True, metavar="JSON", help="Path to watermark_detection_results_not_signed.json (provides Original and Watermarked).")
    parser.add_argument("--attack-json", action="append", dest="attack_jsons", metavar="JSON", default=[], help="Path to an attack detection JSON (e.g. ..._attack_jpeg_signed_human.json). Can be repeated.")
    parser.add_argument("--attack-label", action="append", dest="attack_labels", metavar="LABEL", default=[], help="Label for the most recent --attack-json (default: derived from filename). Can be repeated.")
    parser.add_argument("--threshold", type=float, default=0.75, help="Detection threshold to draw as vertical line (default: 0.75).")
    parser.add_argument("--output", "-o", required=True, metavar="PATH", help="Output path for the figure (e.g. bit_accuracy_distributions.png).")
    args = parser.parse_args()

    if not os.path.isfile(args.not_signed):
        raise FileNotFoundError(f"Not-signed JSON not found: {args.not_signed}")

    # Pair each attack JSON with a label
    attack_pairs: List[Tuple[str, str]] = []
    for j, path in enumerate(args.attack_jsons):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Attack JSON not found: {path}")
        label = args.attack_labels[j] if j < len(args.attack_labels) else extract_attack_label(path)
        attack_pairs.append((path, label))

    condition_arrays = collect_condition_arrays(args.not_signed, attack_paths=attack_pairs)
    if not condition_arrays:
        raise ValueError("No bit-accuracy data found in the given JSONs.")

    plot_distributions(
        condition_arrays=condition_arrays,
        threshold=args.threshold,
        output_path=args.output,
    )
    print(f"Saved figure to: {args.output}")

if __name__ == "__main__":
    main()
