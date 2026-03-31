import argparse
import json
import os
from typing import Any, Dict, List
import torch
import torchvision.transforms as T
from PIL import Image
import videoseal

def get_device(requested: str) -> str:
    """Resolve device: use requested, or auto-detect (cuda if available else cpu) when 'auto'."""
    if requested and requested.lower() != "auto":
        return requested
    return "cuda" if torch.cuda.is_available() else "cpu"

def bits_preview(bits: torch.Tensor, max_bits: int = 32) -> str:
    """Return a short string preview (e.g., first 32 bits as '0101...')."""
    bits = (bits > 0).to(torch.int32)
    bits = bits[:max_bits].cpu().numpy().tolist()
    return "".join(str(b) for b in bits)

def load_metadata(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def main() -> None:
    parser = argparse.ArgumentParser(description="Detect PixelSeal watermarks using precomputed metadata. Takes watermarked images and a metadata JSON file from watermark_embed.py, runs detection on both original and watermarked images (if available), and writes per-image and aggregate accuracy metrics to an output JSON file.")
    parser.add_argument("--original-dir", type=str, default=None, help="Directory containing original (unwatermarked) images. If omitted, accuracy on original images is skipped.")
    parser.add_argument("--watermarked-dir", type=str, required=True, help="Directory containing watermarked images (e.g., from watermark_embed.py).")
    parser.add_argument("--metadata-path", type=str, required=True, help="Path to watermark metadata JSON produced by watermark_embed.py.")
    parser.add_argument("--output-json", type=str, default="watermark_detection_results.json", help="Path to write detection results JSON.")
    parser.add_argument("--device", type=str, default="auto", help="Device for inference: 'cuda', 'cpu', or 'auto' (default: auto = cuda if available else cpu).")
    args = parser.parse_args()

    if not os.path.isfile(args.metadata_path):
        raise ValueError(f"Metadata JSON does not exist: {args.metadata_path}")
    if not os.path.isdir(args.watermarked_dir):
        raise ValueError(f"Watermarked directory does not exist: {args.watermarked_dir}")
    if args.original_dir is not None and not os.path.isdir(args.original_dir):
        raise ValueError(f"Original directory does not exist: {args.original_dir}")

    metadata = load_metadata(args.metadata_path)
    image_entries: List[Dict[str, Any]] = metadata.get("images", [])
    if not image_entries:
        print(f"No image entries found in metadata: {args.metadata_path}")
        return

    device = get_device(args.device)
    print(f"Using device: {device}")

    # Load PixelSeal model
    model = videoseal.load("pixelseal")
    model.to(device)
    model.eval()

    to_tensor = T.ToTensor()

    results_per_image: List[Dict[str, Any]] = []
    acc_original_list: List[float] = []
    acc_watermarked_list: List[float] = []

    for entry in image_entries:
        idx = entry.get("index")
        input_name = entry.get("input_image")
        wm_name = entry.get("watermarked_image")
        msg_bits = entry.get("message_bits")

        if msg_bits is None:
            print(f"[{idx:04d}] {input_name} - missing 'message_bits' in metadata, skipping.")
            continue

        msg_true = torch.tensor(msg_bits, dtype=torch.float32, device=device)

        # Resolve watermarked image path. First try the exact name from metadata
        # (e.g., produced directly by watermark_embed.py). If that doesn't exist,
        # fall back to a signed variant whose name starts with the watermarked
        # stem and has a suffix like "_signed_manifest_<something>".
        wm_path = os.path.join(args.watermarked_dir, wm_name)
        if not os.path.isfile(wm_path):
            name_root, ext = os.path.splitext(wm_name)
            candidate_path = None
            for entry_name in os.listdir(args.watermarked_dir):
                if not entry_name.lower().endswith(ext.lower()):
                    continue
                if entry_name.startswith(f"{name_root}_signed_manifest_"):
                    candidate_path = os.path.join(args.watermarked_dir, entry_name)
                    break
            if candidate_path is None:
                print(
                    f"[{idx:04d}] {wm_name} - no matching watermarked or signed image "
                    f"found in {args.watermarked_dir}, skipping."
                )
                continue
            wm_path = candidate_path

        orig_path = None
        if args.original_dir is not None and input_name is not None:
            orig_path = os.path.join(args.original_dir, input_name)
            if not os.path.isfile(orig_path):
                print(f"[{idx:04d}] {input_name} - original image not found at {orig_path}, skipping original accuracy.")
                orig_path = None

        print(f"[{idx:04d}] input={input_name} watermarked={wm_name}")

        bit_acc_before: float | None = None
        bit_acc_after: float | None = None

        with torch.no_grad():
            # Detect on original image (if available)
            if orig_path is not None:
                img_orig = Image.open(orig_path).convert("RGB")
                img_orig_tensor = to_tensor(img_orig).unsqueeze(0).to(device)

                detected_before = model.detect(img_orig_tensor, is_video=False)
                preds_before = detected_before["preds"][0, 1:]
                preds_before_bin = (preds_before > 0.0)
                if preds_before_bin.dim() == 3:
                    preds_before_mean = preds_before_bin.float().mean(dim=(1, 2))
                else:
                    preds_before_mean = preds_before_bin.float()
                msg_decoded_before = (preds_before_mean > 0.5).float()

                bit_acc_before = (
                    (msg_decoded_before.cpu() == msg_true.cpu()).float().mean().item()
                )
                acc_original_list.append(bit_acc_before)

            # Detect on watermarked image
            img_wm = Image.open(wm_path).convert("RGB")
            img_wm_tensor = to_tensor(img_wm).unsqueeze(0).to(device)

            detected_after = model.detect(img_wm_tensor, is_video=False)
            preds_after = detected_after["preds"][0, 1:]
            preds_bin = (preds_after > 0.0)
            if preds_bin.dim() == 3:
                preds_mean = preds_bin.float().mean(dim=(1, 2))
            else:
                preds_mean = preds_bin.float()
            msg_decoded_after = (preds_mean > 0.5).float()

            bit_acc_after = (
                (msg_decoded_after.cpu() == msg_true.cpu()).float().mean().item()
            )
            acc_watermarked_list.append(bit_acc_after)

        result_entry: Dict[str, Any] = {
            "index": idx,
            "input_image": input_name,
            "watermarked_image": wm_name,
            "bit_accuracy_original": bit_acc_before,
            "bit_accuracy_watermarked": bit_acc_after,
            "embedded_bits_preview": bits_preview(msg_true),
            "decoded_before_preview": (
                bits_preview(msg_decoded_before) if bit_acc_before is not None else None
            ),
            "decoded_after_preview": bits_preview(msg_decoded_after),
        }
        results_per_image.append(result_entry)

    summary: Dict[str, Any] = {
        "num_images": len(results_per_image),
        "average_bit_accuracy_original": (
            float(sum(acc_original_list) / len(acc_original_list))
            if acc_original_list
            else None
        ),
        "average_bit_accuracy_watermarked": (
            float(sum(acc_watermarked_list) / len(acc_watermarked_list))
            if acc_watermarked_list
            else None
        ),
    }

    output: Dict[str, Any] = {
        "metadata_path": os.path.abspath(args.metadata_path),
        "original_dir": os.path.abspath(args.original_dir) if args.original_dir else None,
        "watermarked_dir": os.path.abspath(args.watermarked_dir),
        "summary": summary,
        "images": results_per_image,
    }

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(
        f"Wrote detection results for {summary['num_images']} images to: {args.output_json}"
    )
    if summary["average_bit_accuracy_watermarked"] is not None:
        print(
            f"Average bit accuracy on watermarked images: "
            f"{summary['average_bit_accuracy_watermarked']:.3f}"
        )
    if summary["average_bit_accuracy_original"] is not None:
        print(
            f"Average bit accuracy on original images: "
            f"{summary['average_bit_accuracy_original']:.3f}"
        )

if __name__ == "__main__":
    main()
