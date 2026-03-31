import argparse
import os
import random
from typing import List, Tuple

import torch
from datasets import load_dataset
from diffusers import DiffusionPipeline

def set_seed(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

def get_parti_split():
    ds = load_dataset("nateraw/parti-prompts")
    # Handle either a split dict (e.g. {"train": ...}) or a single split
    if isinstance(ds, dict):
        return ds.get("train") or next(iter(ds.values()))
    return ds

def stratified_sample_indices(
    num_samples: int,
    categories: List[str],
    challenges: List[str],
    category_column: str,
    challenge_column: str,
    seed: int = 42,
) -> List[int]:
    """
    Stratify over categories and approximately balance challenge types.

    Strategy:
    - Allocate an approximately equal quota per category.
    - Within each category, cycle over challenges and pick prompts
      for each (category, challenge) combination in a round-robin fashion
      until the category quota is filled or we exhaust available prompts.
    """
    from collections import defaultdict

    set_seed(seed)

    # Build mapping from (category, challenge) -> list of dataset indices
    split = get_parti_split()
    by_cat_chal = defaultdict(list)

    for idx, row in enumerate(split):
        cat = row.get(category_column)
        chal = row.get(challenge_column)
        if cat in categories and chal in challenges:
            by_cat_chal[(cat, chal)].append(idx)

    # Shuffle indices within each (category, challenge) bucket
    for key in by_cat_chal:
        random.shuffle(by_cat_chal[key])

    num_categories = len(categories)
    base_per_cat = num_samples // num_categories
    remainder = num_samples % num_categories

    # Distribute the remainder categories in a deterministic way
    categories_sorted = sorted(categories)
    per_cat_target = {}
    for i, cat in enumerate(categories_sorted):
        extra = 1 if i < remainder else 0
        per_cat_target[cat] = base_per_cat + extra

    selected_indices: List[int] = []

    for cat in categories_sorted:
        target_for_cat = per_cat_target[cat]
        if target_for_cat == 0:
            continue

        # Build a list of challenge buckets for this category that have at least one index
        buckets: List[Tuple[str, List[int]]] = []
        for chal in challenges:
            key = (cat, chal)
            if by_cat_chal.get(key):
                buckets.append((chal, by_cat_chal[key]))

        if not buckets:
            continue

        # Round-robin over challenges to approximately balance them
        cat_selected = 0
        bucket_idx = 0
        while cat_selected < target_for_cat and buckets:
            chal, idx_list = buckets[bucket_idx]
            if idx_list:
                selected_indices.append(idx_list.pop())
                cat_selected += 1
            else:
                # Remove empty bucket
                buckets.pop(bucket_idx)
                if not buckets:
                    break
                bucket_idx %= len(buckets)
                continue

            bucket_idx = (bucket_idx + 1) % len(buckets)

    # If we didn't manage to hit exactly num_samples (due to dataset limitations),
    # top up from any remaining prompts in a balanced, but simpler, fashion.
    if len(selected_indices) < num_samples:
        remaining_needed = num_samples - len(selected_indices)
        all_remaining: List[int] = []
        for key, idx_list in by_cat_chal.items():
            all_remaining.extend(idx_list)
        random.shuffle(all_remaining)
        for idx in all_remaining:
            if len(selected_indices) >= num_samples:
                break
            if idx not in selected_indices:
                selected_indices.append(idx)

    # In case we over-selected for some reason, trim deterministically
    selected_indices = selected_indices[:num_samples]

    return selected_indices

def sanitize_filename(text: str, max_len: int = 50) -> str:
    keep = []
    for ch in text:
        if ch.isalnum() or ch in (" ", "-", "_"):
            keep.append(ch)
    cleaned = "".join(keep).strip().replace(" ", "_")
    if not cleaned:
        cleaned = "prompt"
    return cleaned[:max_len]


def get_device(requested: str | None) -> str:
    """Resolve device: use requested, or auto-detect (cuda if available else cpu)."""
    if requested and requested.lower() != "auto":
        return requested
    return "cuda" if torch.cuda.is_available() else "cpu"


def build_pipeline(device: str) -> DiffusionPipeline:
    use_fp16 = device.startswith("cuda")
    pipe = DiffusionPipeline.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0",
        torch_dtype=torch.float16 if use_fp16 else torch.float32,
        use_safetensors=True,
        variant="fp16" if use_fp16 else None,
        add_watermarker=False
    )
    pipe.to(device)
    return pipe

def main():
    parser = argparse.ArgumentParser(description="Generate images with SDXL from stratified samples of the nateraw/parti-prompts dataset.")
    parser.add_argument("--num-images", type=int, default=100, help="Number of prompts/images to sample and generate (default: 100).")
    parser.add_argument("--output-dir", type=str, default="outputs/original", help="Directory to save generated images.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible sampling and generation.")
    parser.add_argument("--device", type=str, default="auto", help="Device for inference: 'cuda', 'cpu', or 'auto' (default: auto = cuda if available else cpu).")
    args = parser.parse_args()

    args.device = get_device(args.device)
    print(f"Using device: {args.device}")

    os.makedirs(args.output_dir, exist_ok=True)
    set_seed(args.seed)

    # Load dataset split
    split = get_parti_split()

    # Known schema fields from the description
    category_column = "Category"
    challenge_column = "Challenge"
    prompt_column = "Prompt"

    # Enumerate distinct categories and challenges from the dataset itself
    categories = sorted({row[category_column] for row in split})
    challenges = sorted({row[challenge_column] for row in split})

    indices = stratified_sample_indices(
        num_samples=args.num_images,
        categories=categories,
        challenges=challenges,
        category_column=category_column,
        challenge_column=challenge_column,
        seed=args.seed,
    )

    # Build the list of prompts and associated metadata
    prompts_with_meta = []
    for idx in indices:
        row = split[int(idx)]
        prompts_with_meta.append(
            (row[prompt_column], row[category_column], row[challenge_column])
        )

    # Save the sampled metadata for your paper / reproducibility
    meta_path = os.path.join(args.output_dir, "sampled_prompts.tsv")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write("index\tprompt\tcategory\tchallenge\n")
        for i, (prompt, cat, chal) in enumerate(prompts_with_meta):
            safe_prompt = prompt.replace("\t", " ").replace("\n", " ")
            f.write(f"{i}\t{safe_prompt}\t{cat}\t{chal}\n")

    pipe = build_pipeline(device=args.device)

    for i, (prompt, cat, chal) in enumerate(prompts_with_meta):
        img_seed = args.seed + i
        generator = torch.Generator(device=args.device).manual_seed(img_seed)

        image = pipe(prompt=prompt, generator=generator).images[0]

        base_name = sanitize_filename(prompt)
        filename = f"{i:04d}_{base_name}_cat-{cat}_chal-{chal}.png"
        out_path = os.path.join(args.output_dir, filename)
        image.save(out_path)


if __name__ == "__main__":
    main()
