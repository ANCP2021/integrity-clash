import argparse
import json
import os
from typing import Any, Dict, List
import torch
import torchvision.transforms as T
from PIL import Image
import videoseal

def list_image_files(input_dir: str) -> List[str]:
    exts = (".png", ".jpg", ".jpeg", ".webp")
    files: List[str] = []
    for entry in os.listdir(input_dir):
        if entry.lower().endswith(exts):
            files.append(os.path.join(input_dir, entry))
    files.sort()
    return files

def get_device(requested: str) -> str:
    """Resolve device: use requested, or auto-detect (cuda if available else cpu) when 'auto'."""
    if requested and requested.lower() != "auto":
        return requested
    return "cuda" if torch.cuda.is_available() else "cpu"

def bits_to_list(bits: torch.Tensor) -> List[int]:
    """Convert a 1D tensor of bits to a Python list of 0/1 ints."""
    bits = (bits > 0).to(torch.int32)
    return bits.cpu().numpy().tolist()

def main() -> None:
    parser = argparse.ArgumentParser(description="Embed PixelSeal watermarks into images. Reads images from an input directory, writes watermarked images to an output directory, and saves per-image embedded message bits to a metadata JSON file.")
    parser.add_argument("--input-dir", type=str, default="outputs/original", help="Directory containing original images (e.g., from image_gen.py).")
    parser.add_argument("--output-dir", type=str, default="outputs/watermarked", help="Directory to save watermarked images.")
    parser.add_argument("--metadata-path", type=str, default=None, help="Path to write JSON metadata with embedded message bits. Defaults to '<output-dir>/watermark_metadata.json'.")
    parser.add_argument("--device", type=str, default="auto", help="Device for inference: 'cuda', 'cpu', or 'auto' (default: auto = cuda if available else cpu).")
    args = parser.parse_args()

    if not os.path.isdir(args.input_dir):
        raise ValueError(f"Input directory does not exist: {args.input_dir}")

    os.makedirs(args.output_dir, exist_ok=True)

    metadata_path = (
        args.metadata_path
        if args.metadata_path is not None
        else os.path.join(args.output_dir, "watermark_metadata.json")
    )

    device = get_device(args.device)
    print(f"Using device: {device}")

    # Load PixelSeal model
    model = videoseal.load("pixelseal")
    model.to(device)
    model.eval()

    image_files = list_image_files(args.input_dir)
    if not image_files:
        print(f"No image files found in input directory: {args.input_dir}")
        return

    print(
        f"Embedding watermarks into {len(image_files)} images from "
        f"'{args.input_dir}' and saving to '{args.output_dir}'."
    )

    to_tensor = T.ToTensor()
    to_pil = T.ToPILImage()

    all_metadata: List[Dict[str, Any]] = []

    for i, in_path in enumerate(image_files):
        filename = os.path.basename(in_path)
        name_root, ext = os.path.splitext(filename)
        out_filename = f"{name_root}_watermarked{ext}"
        out_path = os.path.join(args.output_dir, out_filename)

        img = Image.open(in_path).convert("RGB")
        img_tensor = to_tensor(img).unsqueeze(0).to(device)
        print(
            f"[{i:04d}] {filename} -> {out_filename} "
            f"(tensor shape: {tuple(img_tensor.shape)}, dtype: {img_tensor.dtype})"
        )

        with torch.no_grad():
            outputs = model.embed(img_tensor, is_video=False)
            imgs_w = outputs["imgs_w"]  # (1, C, H, W)

            # Ground-truth embedded message for this image (binarized)
            msg_true = (outputs["msgs"][0] > 0.5).float()  # [K]

            # Save watermarked image
            img_w_pil = to_pil(imgs_w[0].cpu().clamp(0.0, 1.0))
            img_w_pil.save(out_path)

        # Record metadata for this image
        entry: Dict[str, Any] = {
            "index": i,
            "input_image": filename,
            "watermarked_image": out_filename,
            "message_bits": bits_to_list(msg_true),
        }
        all_metadata.append(entry)

    # Write metadata JSON
    metadata = {
        "input_dir": os.path.abspath(args.input_dir),
        "output_dir": os.path.abspath(args.output_dir),
        "num_images": len(all_metadata),
        "images": all_metadata,
    }
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"Wrote watermark metadata for {len(all_metadata)} images to: {metadata_path}")

if __name__ == "__main__":
    main()
