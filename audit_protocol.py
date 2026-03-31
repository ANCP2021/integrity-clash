import argparse
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

STATE_CONSISTENT_SYNTHETIC = "CONSISTENT_SYNTHETIC"  # Q1
STATE_INTEGRITY_CLASH = "INTEGRITY_CLASH"  # Q2
STATE_FRAGILE_PROVENANCE = "FRAGILE_PROVENANCE"  # Q3
STATE_SILENT = "SILENT"  # Q4

@dataclass
class Case:
    pipeline: str
    index: int
    filename: str
    bit_accuracy: float
    has_valid_c2pa: bool
    claims_ai: Optional[bool]  # True: AI manifest, False: non-AI manifest, None: no manifest
    state: str

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def classify_case(
    has_valid_c2pa: bool,
    claims_ai: Optional[bool],
    bit_accuracy: Optional[float],
    threshold: float,
) -> str:
    """
    Map a single test case into one of the four quadrants:

    - Q1 (CONSISTENT_SYNTHETIC): C2PA valid + claims AI + watermark detected
    - Q2 (INTEGRITY_CLASH):     C2PA valid + does NOT claim AI + watermark detected
    - Q3 (FRAGILE_PROVENANCE):  No valid C2PA + watermark detected
    - Q4 (SILENT):              No valid C2PA + no watermark detected

    For completeness, if C2PA is valid but the watermark is not detected, we
    still bucket it into SILENT so that every case maps to one of Q1-Q4.
    """
    detected = bit_accuracy is not None and bit_accuracy > threshold

    if has_valid_c2pa and detected:
        if claims_ai is False:
            return STATE_INTEGRITY_CLASH
        # If the manifest explicitly claims AI, or claim is unknown, we treat
        # this as consistent synthetic provenance.
        return STATE_CONSISTENT_SYNTHETIC

    if not has_valid_c2pa and detected:
        return STATE_FRAGILE_PROVENANCE

    # No watermark detected (<= threshold) – treated as SILENT regardless of C2PA.
    return STATE_SILENT

def build_cases(
    wm_not_signed: dict,
    wm_signed_ai: dict,
    wm_signed_human: dict,
    verif_ai: dict,
    verif_human: dict,
    threshold: float,
) -> List[Case]:
    cases: List[Case] = []

    # P0: Original unwatermarked images - no C2PA, no watermark (bit_accuracy_original).
    for img in wm_not_signed["images"]:
        bit_acc = img.get("bit_accuracy_original")
        state = classify_case(
            has_valid_c2pa=False,
            claims_ai=None,
            bit_accuracy=bit_acc,
            threshold=threshold,
        )
        cases.append(
            Case(
                pipeline="P0",
                index=img["index"],
                filename=img["input_image"],
                bit_accuracy=bit_acc,
                has_valid_c2pa=False,
                claims_ai=None,
                state=state,
            )
        )

    # P1: Watermarked only - no C2PA, watermark present (bit_accuracy_watermarked).
    for img in wm_not_signed["images"]:
        bit_acc = img.get("bit_accuracy_watermarked")
        state = classify_case(
            has_valid_c2pa=False,
            claims_ai=None,
            bit_accuracy=bit_acc,
            threshold=threshold,
        )
        cases.append(
            Case(
                pipeline="P1",
                index=img["index"],
                filename=img["watermarked_image"],
                bit_accuracy=bit_acc,
                has_valid_c2pa=False,
                claims_ai=None,
                state=state,
            )
        )

    # Helper: map filenames to validation_state for signed pipelines.
    def build_validation_map(verif: dict) -> Dict[str, str]:
        return {entry["file"]: entry["validation_state"] for entry in verif["results"]}

    val_map_ai = build_validation_map(verif_ai)
    val_map_human = build_validation_map(verif_human)

    # P1b: Watermarked + signed with AI manifest - claims AI.
    for img in wm_signed_ai["images"]:
        signed_filename = img["watermarked_image"].replace(
            ".png", "_signed_manifest_ai.png"
        )
        validation_state = val_map_ai.get(signed_filename, "Unknown")
        has_valid_c2pa = validation_state == "Valid"
        bit_acc = img.get("bit_accuracy_watermarked")
        state = classify_case(
            has_valid_c2pa=has_valid_c2pa,
            claims_ai=True,
            bit_accuracy=bit_acc,
            threshold=threshold,
        )
        cases.append(
            Case(
                pipeline="P1b",
                index=img["index"],
                filename=signed_filename,
                bit_accuracy=bit_acc,
                has_valid_c2pa=has_valid_c2pa,
                claims_ai=True,
                state=state,
            )
        )

    # P2: Watermarked + signed with human-edited manifest - does NOT claim AI.
    for img in wm_signed_human["images"]:
        signed_filename = img["watermarked_image"].replace(
            ".png", "_signed_manifest_human_edited.png"
        )
        validation_state = val_map_human.get(signed_filename, "Unknown")
        has_valid_c2pa = validation_state == "Valid"
        bit_acc = img.get("bit_accuracy_watermarked")
        state = classify_case(
            has_valid_c2pa=has_valid_c2pa,
            claims_ai=False,
            bit_accuracy=bit_acc,
            threshold=threshold,
        )
        cases.append(
            Case(
                pipeline="P2",
                index=img["index"],
                filename=signed_filename,
                bit_accuracy=bit_acc,
                has_valid_c2pa=has_valid_c2pa,
                claims_ai=False,
                state=state,
            )
        )

    return cases

def summarise_conflict_matrix(cases: List[Case]) -> Dict[str, int]:
    counts = {
        STATE_CONSISTENT_SYNTHETIC: 0,
        STATE_INTEGRITY_CLASH: 0,
        STATE_FRAGILE_PROVENANCE: 0,
        STATE_SILENT: 0,
    }
    for c in cases:
        counts[c.state] = counts.get(c.state, 0) + 1
    return counts

def per_pipeline_summary(cases: List[Case]) -> Dict[str, dict]:
    by_pipeline: Dict[str, List[Case]] = {}
    for c in cases:
        by_pipeline.setdefault(c.pipeline, []).append(c)

    expected_quadrant = {
        "P0": STATE_SILENT,  # no C2PA, no watermark expected
        "P1": STATE_FRAGILE_PROVENANCE,  # no C2PA, watermark present
        "P1b": STATE_CONSISTENT_SYNTHETIC,  # AI manifest, watermark present
        "P2": STATE_INTEGRITY_CLASH,  # human manifest, watermark present
    }

    summary: Dict[str, dict] = {}
    for pipeline, lst in by_pipeline.items():
        n = len(lst)
        if n == 0:
            continue
        mean_bit_acc = sum(c.bit_accuracy for c in lst) / n
        min_bit_acc = min(c.bit_accuracy for c in lst)
        c2pa_valid_rate = (
            sum(1 for c in lst if c.has_valid_c2pa) / n if any(c.has_valid_c2pa for c in lst) else 0.0
        )
        expected_state = expected_quadrant.get(pipeline)
        pct_expected = (
            sum(1 for c in lst if c.state == expected_state) / n if expected_state else 0.0
        )

        summary[pipeline] = {
            "num_images": n,
            "mean_bit_accuracy": mean_bit_acc,
            "min_bit_accuracy": min_bit_acc,
            "c2pa_validation_rate": c2pa_valid_rate,
            "expected_quadrant": expected_state,
            "pct_in_expected_quadrant": pct_expected,
        }

    return summary

def audit_metrics(cases: List[Case]) -> Dict[str, float]:
    """
    Evaluate the audit protocol treating INTEGRITY_CLASH (Q2) as the positive class.

    - Ground-truth positives: all P2 cases (by design these are integrity clashes).
    - Predicted positives: cases classified as INTEGRITY_CLASH.
    """
    positives = [c for c in cases if c.pipeline == "P2"]
    negatives = [c for c in cases if c.pipeline != "P2"]

    tp = sum(1 for c in positives if c.state == STATE_INTEGRITY_CLASH)
    fn = sum(1 for c in positives if c.state != STATE_INTEGRITY_CLASH)
    fp = sum(1 for c in negatives if c.state == STATE_INTEGRITY_CLASH)
    tn = sum(1 for c in negatives if c.state != STATE_INTEGRITY_CLASH)

    tpr = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    fnr = fn / (tp + fn) if (tp + fn) > 0 else 0.0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    accuracy = (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) > 0 else 0.0

    return {
        "TPR": tpr,
        "FNR": fnr,
        "FPR": fpr,
        "accuracy": accuracy,
        "TP": tp,
        "FN": fn,
        "FP": fp,
        "TN": tn,
    }

def format_conflict_matrix_csv(counts: Dict[str, int]) -> str:
    lines = ["quadrant,count"]
    lines.append(f"{STATE_CONSISTENT_SYNTHETIC},{counts.get(STATE_CONSISTENT_SYNTHETIC, 0)}")
    lines.append(f"{STATE_INTEGRITY_CLASH},{counts.get(STATE_INTEGRITY_CLASH, 0)}")
    lines.append(f"{STATE_FRAGILE_PROVENANCE},{counts.get(STATE_FRAGILE_PROVENANCE, 0)}")
    lines.append(f"{STATE_SILENT},{counts.get(STATE_SILENT, 0)}")
    return "\n".join(lines)

def format_per_pipeline_csv(summary: Dict[str, dict]) -> str:
    lines = [
        "pipeline,num_images,c2pa_validation_rate,mean_bit_accuracy,min_bit_accuracy,expected_quadrant,pct_in_expected_quadrant"
    ]
    for pipeline in sorted(summary.keys()):
        s = summary[pipeline]
        lines.append(
            f"{pipeline},{s['num_images']},{s['c2pa_validation_rate']:.4f},{s['mean_bit_accuracy']:.6f},"
            f"{s['min_bit_accuracy']:.6f},{s['expected_quadrant']},{s['pct_in_expected_quadrant']:.4f}"
        )
    return "\n".join(lines)

def format_audit_metrics_csv(metrics: Dict[str, float]) -> str:
    header = "TPR,FPR,FNR,accuracy,TP,FP,FN,TN"
    line = (
        f"{metrics['TPR']:.4f},"
        f"{metrics['FPR']:.4f},"
        f"{metrics['FNR']:.4f},"
        f"{metrics['accuracy']:.4f},"
        f"{int(metrics['TP'])},"
        f"{int(metrics['FP'])},"
        f"{int(metrics['FN'])},"
        f"{int(metrics['TN'])}"
    )
    return "\n".join([header, line])

def main() -> None:
    parser = argparse.ArgumentParser(description="Audit protocol for cross-layer consistency between C2PA provenance and PixelSeal watermarking across four pipelines (P0, P1, P1b, P2).")
    parser.add_argument("--wm-not-signed", required=True, help="Path to watermark_detection_results_not_signed.json")
    parser.add_argument("--wm-signed-ai", required=True, help="Path to watermark_detection_results_signed_ai.json")
    parser.add_argument("--wm-signed-human", required=True, help="Path to watermark_detection_results_signed_human.json")
    parser.add_argument("--verif-ai", required=True, help="Path to verification_results_ai.json")
    parser.add_argument("--verif-human", required=True, help="Path to verification_results_human.json")
    parser.add_argument("--threshold", type=float, default=0.75, help="Bit accuracy threshold for considering the watermark detected (default: 0.75)")
    parser.add_argument("--output-prefix", default=None, help="Optional prefix for writing CSV files (<prefix>_conflict_matrix.csv, <prefix>_per_pipeline.csv, <prefix>_audit_metrics.csv). If omitted, tables are only printed to stdout.")
    args = parser.parse_args()

    wm_not_signed = load_json(args.wm_not_signed)
    wm_signed_ai = load_json(args.wm_signed_ai)
    wm_signed_human = load_json(args.wm_signed_human)
    verif_ai = load_json(args.verif_ai)
    verif_human = load_json(args.verif_human)

    cases = build_cases(
        wm_not_signed=wm_not_signed,
        wm_signed_ai=wm_signed_ai,
        wm_signed_human=wm_signed_human,
        verif_ai=verif_ai,
        verif_human=verif_human,
        threshold=args.threshold,
    )

    # 1) Conflict matrix across all pipelines.
    conflict_counts = summarise_conflict_matrix(cases)

    # 2) Per-pipeline summaries.
    pipeline_summary = per_pipeline_summary(cases)

    # 3) Audit protocol metrics (TPR/FPR/FNR/accuracy).
    metrics = audit_metrics(cases)

    # Print paper-ready CSV tables to stdout.
    print("# Conflict matrix (quadrant counts)")
    print(format_conflict_matrix_csv(conflict_counts))
    print()

    print("# Per-pipeline summary")
    print(format_per_pipeline_csv(pipeline_summary))
    print()

    print("# Audit protocol evaluation metrics")
    print(format_audit_metrics_csv(metrics))

    # Optionally write CSVs.
    if args.output_prefix:
        prefix = args.output_prefix
        with open(f"{prefix}_conflict_matrix.csv", "w", encoding="utf-8") as f:
            f.write(format_conflict_matrix_csv(conflict_counts))
        with open(f"{prefix}_per_pipeline.csv", "w", encoding="utf-8") as f:
            f.write(format_per_pipeline_csv(pipeline_summary))
        with open(f"{prefix}_audit_metrics.csv", "w", encoding="utf-8") as f:
            f.write(format_audit_metrics_csv(metrics))

if __name__ == "__main__":
    main()
