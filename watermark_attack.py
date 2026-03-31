import argparse
import io
import os
from PIL import Image

ATTACK_JPEG_Q80 = "jpeg_q80"
ATTACK_CROP10 = "crop10"
ATTACK_SOCIAL = "social"

def _resample_lanczos():
    # Pillow>=10 uses Image.Resampling; older versions expose constants directly.
    try:
        return Image.Resampling.LANCZOS  # type: ignore[attr-defined]
    except AttributeError:
        return Image.LANCZOS

def jpeg_roundtrip(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    buf.seek(0)
    out = Image.open(buf).convert("RGB")
    return out

def attack_jpeg_q80(img: Image.Image) -> Image.Image:
    return jpeg_roundtrip(img, quality=80)

def attack_crop_resize(img: Image.Image, crop_frac_each_side: float) -> Image.Image:
    w, h = img.size
    dx = int(round(w * crop_frac_each_side))
    dy = int(round(h * crop_frac_each_side))
    left = min(max(dx, 0), w - 1)
    top = min(max(dy, 0), h - 1)
    right = max(min(w - dx, w), left + 1)
    bottom = max(min(h - dy, h), top + 1)

    cropped = img.crop((left, top, right, bottom))
    resized = cropped.resize((w, h), resample=_resample_lanczos())
    return resized

def attack_social_sim(img: Image.Image, downscale_factor: float, jpeg_quality: int) -> Image.Image:
    w, h = img.size
    w2 = max(1, int(round(w * downscale_factor)))
    h2 = max(1, int(round(h * downscale_factor)))
    smaller = img.resize((w2, h2), resample=_resample_lanczos())
    roundtripped = jpeg_roundtrip(smaller, quality=jpeg_quality)
    back = roundtripped.resize((w, h), resample=_resample_lanczos())
    return back

def main() -> None:
    parser = argparse.ArgumentParser(description="Apply common post-processing attacks to PixelSeal-watermarked PNGs. This keeps filenames unchanged so existing watermark metadata can be reused.")
    parser.add_argument("--input-dir", required=True, help="Directory containing watermarked PNGs.")
    parser.add_argument("--output-dir", required=True, help="Directory to write attacked PNGs.")
    parser.add_argument("--attack", required=True, choices=[ATTACK_JPEG_Q80, ATTACK_CROP10, ATTACK_SOCIAL], help="Which attack to apply.")
    parser.add_argument("--crop-frac-each-side", type=float, default=0.10, help="For crop attack: fraction to crop from EACH side (default: 0.10). Example: 0.10 crops 10%% from left/right/top/bottom then resizes back.")
    parser.add_argument("--downscale", type=float, default=0.75, help="For social attack: downscale factor before JPEG (default: 0.75).")
    parser.add_argument("--jpeg-quality", type=int, default=70, help="For social attack: JPEG quality during roundtrip (default: 70).")
    args = parser.parse_args()

    if not os.path.isdir(args.input_dir):
        raise ValueError(f"Input directory does not exist: {args.input_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    input_files = sorted(
        f for f in os.listdir(args.input_dir) if f.lower().endswith(".png")
    )
    if not input_files:
        raise ValueError(f"No PNG files found in input dir: {args.input_dir}")

    n_ok = 0
    for name in input_files:
        in_path = os.path.join(args.input_dir, name)
        out_path = os.path.join(args.output_dir, name)

        img = Image.open(in_path).convert("RGB")

        if args.attack == ATTACK_JPEG_Q80:
            out = attack_jpeg_q80(img)
        elif args.attack == ATTACK_CROP10:
            out = attack_crop_resize(img, crop_frac_each_side=args.crop_frac_each_side)
        elif args.attack == ATTACK_SOCIAL:
            out = attack_social_sim(
                img,
                downscale_factor=args.downscale,
                jpeg_quality=args.jpeg_quality,
            )
        else:
            raise RuntimeError(f"Unhandled attack: {args.attack}")

        # Save as PNG (so subsequent C2PA signing remains unchanged).
        out.save(out_path, format="PNG")
        n_ok += 1

    print(f"Wrote {n_ok} attacked PNGs to: {args.output_dir}")

if __name__ == "__main__":
    main()
