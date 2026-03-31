import argparse
import json
import os
from pathlib import Path
import c2pa
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend

def load_manifest_template(template_path: Path) -> dict:
    """Load a manifest JSON template from disk."""
    with template_path.open("r", encoding="utf-8") as f:
        return json.load(f)

def create_es256_signer(cert_path: Path, key_path: Path, key_password: str | None, tsa_url: str | None) -> c2pa.Signer:
    """
    Create a c2pa.Signer using an ECDSA P-256 private key and a PEM certificate chain.

    - cert_path: PEM file containing the certificate (and optional chain), e.g. ec_cert.pem
    - key_path: PEM file containing the EC private key, e.g. ec_key.pem
    - key_password: optional password for an encrypted private key
    - tsa_url: optional RFC 3161 timestamp authority URL
    """
    # Load certificate chain as UTF-8 string
    with cert_path.open("rb") as cert_file:
        certs_pem_str = cert_file.read().decode("utf-8")

    # Load EC private key
    with key_path.open("rb") as key_file:
        key_data = key_file.read()

    private_key = serialization.load_pem_private_key(
        key_data,
        password=key_password.encode("utf-8") if key_password else None,
        backend=default_backend(),
    )

    # Callback that c2pa will call with bytes to be signed
    def callback_signer_es256(data: bytes) -> bytes:
        return private_key.sign(data, ec.ECDSA(hashes.SHA256()))

    # Build the c2pa signer
    signer = c2pa.Signer.from_callback(
        callback=callback_signer_es256,
        alg=c2pa.C2paSigningAlg.ES256,
        certs=certs_pem_str,
        tsa_url=tsa_url,
    )
    return signer

def iter_png_files(input_dir: Path):
    """Yield all .png / .PNG files in the input directory (non-recursive)."""
    for ext in ("*.png", "*.PNG"):
        for path in sorted(input_dir.glob(ext)):
            if path.is_file():
                yield path

def sign_directory(
    input_dir: Path,
    output_dir: Path,
    manifest_template_path: Path,
    cert_path: Path,
    key_path: Path,
    key_password: str | None = None,
    tsa_url: str | None = None,
) -> None:
    """
    For each PNG in input_dir:
      - sign it using a C2PA manifest from manifest_template_path
      - write the signed image into output_dir
    """
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    manifest_template_path = manifest_template_path.resolve()
    cert_path = cert_path.resolve()
    key_path = key_path.resolve()

    if not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist or is not a directory: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Load the JSON manifest template once
    base_manifest = load_manifest_template(manifest_template_path)

    # Derive a suffix from the manifest template filename, e.g.
    # manifests/manifest_ai.json -> "manifest_ai"
    manifest_suffix = manifest_template_path.stem

    # Create signer once and reuse it for all images
    with create_es256_signer(cert_path, key_path, key_password, tsa_url) as signer:
        for src_path in iter_png_files(input_dir):
            # Preserve original base name but append a suffix indicating which
            # manifest was used, e.g. "image.png" -> "image_signed_manifest_ai.png"
            dest_name = f"{src_path.stem}_signed_{manifest_suffix}{src_path.suffix}"
            dest_path = output_dir / dest_name

            # Make a per-image manifest definition (shallow copy is fine if you don't mutate nested structures)
            manifest_def = dict(base_manifest)
            manifest_def["format"] = "image/png"
            manifest_def.setdefault("title", src_path.name)

            print(f"\nSigning {src_path} -> {dest_path}")

            # Build and sign
            with c2pa.Builder(manifest_def) as builder:
                manifest_bytes = builder.sign_file(
                    source_path=str(src_path),
                    dest_path=str(dest_path),
                    signer=signer,
                )

            print(f"  Signed. Manifest size: {len(manifest_bytes)} bytes")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sign PNG images in a directory using a C2PA manifest template.")
    parser.add_argument("input_dir", help="Directory containing input PNG images")
    parser.add_argument("output_dir", help="Directory to write signed PNG images")
    parser.add_argument("--manifest-template", required=True, help="Path to JSON manifest template (e.g. AI or human-edit template)")
    parser.add_argument("--cert", required=True, help="Path to ECDSA P-256 certificate PEM file (e.g. ec_cert.pem)")
    parser.add_argument("--key", required=True, help="Path to ECDSA P-256 private key PEM file (e.g. ec_key.pem)")
    parser.add_argument("--key-password", default=None, help="Optional password for encrypted private key (if applicable)")
    parser.add_argument("--tsa-url", default=None, help="Optional RFC 3161 timestamp authority URL (e.g. http://timestamp.digicert.com). If omitted, no TSA is used.")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    sign_directory(
        input_dir=Path(args.input_dir),
        output_dir=Path(args.output_dir),
        manifest_template_path=Path(args.manifest_template),
        cert_path=Path(args.cert),
        key_path=Path(args.key),
        key_password=args.key_password,
        tsa_url=args.tsa_url,
    )

if __name__ == "__main__":
    main()