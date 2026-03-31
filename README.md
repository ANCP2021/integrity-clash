# Authenticated Contradictions from Desynchronized Provenance and Watermarking

Pipeline for generating images, embedding and detecting PixelSeal watermarks, and signing/verifying them with C2PA Content Credentials. Use the same metadata for watermark detection on both unsigned watermarked images and signed images.

## Pipeline Overview

1. **Generate** images with SDXL (stratified Parti Prompts).
2. **Embed** PixelSeal watermarks; save watermarked images and metadata.
3. **Detect** watermarks and compute bit accuracy (optional: on originals and/or watermarked/signed images).
4. **Create CA and signing cert** (one-time; see below).
5. **Sign** images with a C2PA manifest (AI or human-edited template).
6. **Verify** signed images and inspect manifest/validation.

---

## 1. Generate images

Generate a set of images (e.g. to `outputs/original`):

```bash
python3 image_gen.py --num-images 50 --output-dir outputs/original --device auto
```

---

## 2. Embed watermarks

Embed PixelSeal watermarks and write metadata (embedded message bits) for later detection:

```bash
python3 watermark_embed.py \
  --input-dir outputs/original \
  --output-dir outputs/watermarked \
  --device auto
```

- Watermarked images are saved as `<name>_watermarked.<ext>` in `outputs/watermarked`.
- Metadata is written to `outputs/watermarked/watermark_metadata.json` (or `--metadata-path`).

---

## 2.5 Apply robustness attacks (optional)

Apply common "in-the-wild" edits to *watermarked* PNGs. Each attack reads `outputs/watermarked` and writes attacked PNGs to a new directory **while keeping filenames unchanged**, so you can reuse the same `watermark_metadata.json` for detection and keep the signing/verification naming conventions unchanged.

### A1. JPEG compression (Q80)

```bash
python3 watermark_attack.py \
  --input-dir outputs/watermarked \
  --output-dir outputs/watermarked_attack_jpeg_q80 \
  --attack jpeg_q80
```

### A2. Crop + resize back to 1024X1024 (10% from each edge)

```bash
python3 watermark_attack.py \
  --input-dir outputs/watermarked \
  --output-dir outputs/watermarked_attack_crop10 \
  --attack crop10
```

### A3. Screenshot / social-media simulation (0.75× -> JPEG Q70 -> back to 1.0X)

```bash
python3 watermark_attack.py \
  --input-dir outputs/watermarked \
  --output-dir outputs/watermarked_attack_social \
  --attack social
```

After generating an attacked directory, you can sign those images (e.g. with the human-edited manifest), verify them, and then run watermark detection on the signed outputs (same metadata file).

---

## 3. Detect watermarks

Run detection and get bit-accuracy metrics. You can point at **watermarked** images or at **signed** images (same metadata file).

**On watermarked images (same dir as embed output):**

```bash
python3 watermark_detect.py \
  --watermarked-dir outputs/watermarked \
  --metadata-path outputs/watermarked/watermark_metadata.json \
  --output-json watermark_detection_results.json \
  --device auto
```

**Optional:** also compute accuracy on originals:

```bash
python3 watermark_detect.py \
  --original-dir outputs/original \
  --watermarked-dir outputs/watermarked \
  --metadata-path outputs/watermarked/watermark_metadata.json \
  --output-json watermark_detection_results.json
```

**On signed images:** use the same metadata and point `--watermarked-dir` at the directory containing signed PNGs. The detector will match files named `<watermarked_stem>_signed_manifest_<manifest_name>.png` automatically.

```bash
python3 watermark_detect.py \
  --watermarked-dir outputs/signed_ai \
  --metadata-path outputs/watermarked/watermark_metadata.json \
  --output-json watermark_detection_signed_results.json
```

---

## 4. Create CA and signing certificate (before signing)

Run once to create a demo CA and an ECDSA P-256 signing cert. **Do not use these in production.**

```bash
# Create a CA key and v3 CA cert
openssl ecparam -name prime256v1 -genkey -noout -out ca_key.pem
openssl req -new -x509 -key ca_key.pem -out ca_cert.pem -days 365 \
  -subj "/CN=IntegrityClashDemoCA/O=Research" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"

# Create a signing key and CSR
openssl ecparam -name prime256v1 -genkey -noout -out ec_key.pem
openssl req -new -key ec_key.pem -out ec_csr.pem \
  -subj "/CN=IntegrityClashDemo/O=Research"

# Create an extensions config file for the signing cert
cat > signing_ext.cnf << 'EOF'
[v3_signing]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = emailProtection
EOF

# Sign the CSR with your CA, applying v3 extensions
openssl x509 -req -in ec_csr.pem -CA ca_cert.pem -CAkey ca_key.pem \
  -CAcreateserial -out ec_cert.pem -days 365 \
  -extfile signing_ext.cnf -extensions v3_signing

# Combine into chain (signing cert first, then CA)
cat ec_cert.pem ca_cert.pem > ec_chain.pem
```

Use **`ec_chain.pem`** as the certificate and **`ec_key.pem`** as the private key when signing (below).

---

## 5. Sign images

Sign PNGs with a C2PA manifest template. Use the **chain** and the **signing key** from the step above:

```bash
python3 sign_images.py outputs/watermarked outputs/signed_ai \
  --manifest-template manifests/manifest_ai.json \
  --cert ec_chain.pem \
  --key ec_key.pem
```

Human-edited manifest example:

```bash
python3 sign_images.py outputs/watermarked outputs/signed_human \
  --manifest-template manifests/manifest_human_edited.json \
  --cert ec_chain.pem \
  --key ec_key.pem
```

Optional: `--key-password` if the key is encrypted; `--tsa-url` for an RFC 3161 timestamp authority.

---

## 6. Verify signed images

Verify C2PA signatures and print validation state and manifest JSON (no keys or templates needed):

```bash
python3 verify_images.py outputs/signed_ai
```

Optional: write a summary JSON:

```bash
python3 verify_images.py outputs/signed_ai --output-json verification_results.json
```

---

## Audit protocol

The script `audit_protocol.py` classifies each test case into one of four cross-layer consistency states:

- Q1 `CONSISTENT_SYNTHETIC`: C2PA valid + claims AI + watermark detected
- Q2 `INTEGRITY_CLASH`: C2PA valid + does NOT claim AI + watermark detected
- Q3 `FRAGILE_PROVENANCE`: No valid C2PA + watermark detected
- Q4 `SILENT`: No valid C2PA + no watermark detected

Example run (baseline pipelines):

```bash
python3 audit_protocol.py \
  --wm-not-signed watermark_detection_results_not_signed.json \
  --wm-signed-ai watermark_detection_results_signed_ai.json \
  --wm-signed-human watermark_detection_results_signed_human.json \
  --verif-ai verification_results_signed_ai.json \
  --verif-human verification_results_signed_human.json \
  --threshold 0.75
```

### Robustness experiment pattern (attacked -> sign human -> verify -> detect -> audit)

Below is an example using the JPEG attack directory `outputs/watermarked_attack_jpeg_q80`:

```bash
# Sign attacked images with the human-edited manifest
python3 sign_images.py outputs/watermarked_attack_jpeg_q80 outputs/signed_human_attack_jpeg_q80 \
  --manifest-template manifests/manifest_human_edited.json \
  --cert ec_chain.pem \
  --key ec_key.pem

# Verify attacked+signed images
python3 verify_images.py outputs/signed_human_attack_jpeg_q80 --output-json verification_results_signed_human_attack_jpeg_q80.json

# Detect watermarks on attacked+signed images (same metadata file)
python3 watermark_detect.py \
  --watermarked-dir outputs/signed_human_attack_jpeg_q80 \
  --metadata-path outputs/watermarked/watermark_metadata.json \
  --output-json watermark_detection_results_signed_human_attack_jpeg_q80.json

# Run the audit protocol, swapping in the attacked-human verification + detection JSONs
python3 audit_protocol.py \
  --wm-not-signed watermark_detection_results_not_signed.json \
  --wm-signed-ai watermark_detection_results_signed_ai.json \
  --wm-signed-human watermark_detection_results_signed_human_attack_jpeg_q80.json \
  --verif-ai verification_results_signed_ai.json \
  --verif-human verification_results_signed_human_attack_jpeg_q80.json \
  --threshold 0.75
```

### Bit-accuracy distribution plot

Plot bit-accuracy distributions across all conditions (original, watermarked, and each attack) with the detection threshold (e.g. 0.75) as a vertical line.

```bash
python3 plot_bit_accuracy_distributions.py \
  --not-signed watermark_detection_results_not_signed.json \
  --attack-json watermark_detection_results_attack_jpeg_signed_human.json \
  --attack-json watermark_detection_results_attack_crop_signed_human.json \
  --attack-json watermark_detection_results_attack_social_signed_human.json \
  --threshold 0.75 \
  --output bit_accuracy_distributions.png
```

Labels for attack JSONs are derived from filenames (e.g. "jpeg", "crop", "social") or set explicitly with `--attack-label`.

## Script summary

| Script                           | Purpose                                                                 |
|----------------------------------|-------------------------------------------------------------------------|
| `image_gen.py`                    | Generate SDXL images from Parti Prompts.                               |
| `watermark_embed.py`              | Embed PixelSeal watermarks; output images + metadata JSON.              |
| `watermark_detect.py`             | Detect watermarks and report bit accuracy (watermarked or signed dir). |
| `watermark_attack.py`             | Apply post-processing attacks to watermarked PNGs.                     |
| `sign_images.py`                  | Sign PNGs with C2PA manifest (use `ec_chain.pem` + `ec_key.pem`).      |
| `verify_images.py`                | Verify signed PNGs and print manifest/validation.                      |
| `audit_protocol.py`               | Classify cases into Q1–Q4 and compute audit metrics/tables.            |
| `plot_bit_accuracy_distributions.py` | Plot bit-accuracy distributions by condition with threshold line.   |

## License

See [LICENSE](LICENSE).
