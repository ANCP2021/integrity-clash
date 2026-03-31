import argparse
import json
from pathlib import Path
import c2pa

def iter_png_files(input_dir: Path):
    """Yield all .png / .PNG files in the input directory (non-recursive)."""
    for ext in ("*.png", "*.PNG"):
        for path in sorted(input_dir.glob(ext)):
            if path.is_file():
                yield path

def verify_directory(input_dir: Path, output_json: Path | None = None) -> None:
    """
    For each PNG in input_dir (assumed C2PA-signed):
      - open with c2pa.Reader
      - report validation state and manifest detailed JSON
    If output_json is set, write a summary and per-file results to that path.
    """
    input_dir = input_dir.resolve()

    if not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist or is not a directory: {input_dir}")

    results = []
    for src_path in iter_png_files(input_dir):
        print(f"\nVerifying {src_path.name}")
        try:
            with c2pa.Reader(src_path) as reader:
                validation_state = reader.get_validation_state()
                detailed_json_str = reader.detailed_json()

            print(f"  Validation state: {validation_state}")

            try:
                detailed = json.loads(detailed_json_str)
                pretty = json.dumps(detailed, indent=2, sort_keys=True)
            except Exception:
                pretty = detailed_json_str
            print("  Manifest store (detailed JSON):")
            print(pretty)

            results.append({
                "file": src_path.name,
                "path": str(src_path),
                "validation_state": validation_state,
                "manifest_json": json.loads(detailed_json_str) if detailed_json_str else None,
            })
        except Exception as e:
            print(f"  Error: {e}")
            results.append({
                "file": src_path.name,
                "path": str(src_path),
                "validation_state": None,
                "error": str(e),
                "manifest_json": None,
            })

    if output_json:
        output_path = Path(output_json).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # For JSON we store validation_state and optionally a shortened manifest (or full);
        # storing full detailed_json in each entry can be large, so we keep it unless you want it.
        out = {
            "input_dir": str(input_dir),
            "num_files": len(results),
            "results": [
                {
                    "file": r["file"],
                    "path": r["path"],
                    "validation_state": r["validation_state"],
                    "error": r.get("error"),
                }
                for r in results
            ],
        }
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote verification summary to {output_path}")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify C2PA-signed PNG images and report validation state and manifest contents.")
    parser.add_argument("input_dir", help="Directory containing signed PNG images to verify")
    parser.add_argument("--output-json", default=None, help="Optional path to write a verification summary JSON (paths and validation states).")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    verify_directory(
        input_dir=Path(args.input_dir),
        output_json=args.output_json,
    )

if __name__ == "__main__":
    main()
