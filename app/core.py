#!/usr/bin/env python3

import base64, io, json
from pathlib import Path

def _progress(iterable, total=None, desc=""):
    try:
        from tqdm import tqdm
        return tqdm(iterable, total=total, desc=desc)
    except Exception:
        return iterable

try:
    from PIL import Image, ImageOps
    PIL_OK = True
except Exception:
    PIL_OK = False

def human(n):
    units = ["B","KB","MB","GB"]
    i = 0
    n = float(n)
    while n >= 1024 and i < len(units)-1:
        n /= 1024.0; i += 1
    return f"{n:.2f} {units[i]}"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def jpeg_fit_to_b64_cap(jpeg_bytes: bytes, cap_chars: int, max_px: int = 800, quality_floor: int = 50):
    if not PIL_OK:
        return jpeg_bytes, {"note":"Pillow not installed; no resizing/compression performed."}
    from PIL import Image, ImageOps
    import io

    max_bytes = (cap_chars * 3) // 4

    im = Image.open(io.BytesIO(jpeg_bytes))
    im = ImageOps.exif_transpose(im).convert("RGB")

    w, h = im.size
    if max(w, h) > max_px:
        if w >= h:
            new_w = max_px
            new_h = int(h * (max_px / w))
        else:
            new_h = max_px
            new_w = int(w * (max_px / h))
        im = im.resize((new_w, new_h), Image.LANCZOS)

    def encode(q: int) -> bytes:
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=q, optimize=True, progressive=True, subsampling="4:2:0")
        return buf.getvalue()

    q_hi = 85
    data = encode(q_hi)

    if len(data) > max_bytes:
        q_lo = quality_floor
        while q_lo < q_hi:
            q_mid = (q_lo + q_hi) // 2
            buf = encode(q_mid)
            if len(buf) <= max_bytes:
                data = buf
                q_hi = q_mid
            else:
                q_lo = q_mid + 1

        if len(data) > max_bytes:
            attempts = 0
            while len(data) > max_bytes and max(im.size) > 50 and attempts < 6:
                w, h = im.size
                im = im.resize((max(1, int(w*0.85)), max(1, int(h*0.85))), Image.LANCZOS)
                data = encode(quality_floor)
                attempts += 1

    meta = {
        "final_dims": im.size,
        "jpeg_bytes": len(data),
        "base64_chars": 4 * ((len(data) + 2) // 3)
    }
    return data, meta

def run_batch(
    input_dir: Path,
    output_dir: Path,
    recurse: bool,
    data_uri: bool,
    cap_chars: int | None,
    csv_map: bool,
    max_px: int,
    quality_floor: int,
    log=print
):
    patterns = ["*.jpg", "*.jpeg", "*.JPG", "*.JPEG"]
    files = []
    for pat in patterns:
        files += list(input_dir.rglob(pat) if recurse else input_dir.glob(pat))

    if not files:
        log("No JPG/JPEG files found.")
        return 1

    ensure_dir(output_dir)
    out_map = []

    total_in = 0
    total_out_b64chars = 0
    skipped = 0

    for p in _progress(files, total=len(files), desc="Encoding"):
        try:
            b = p.read_bytes()
            original_len = len(b)

            if cap_chars is not None:
                def shrink(bytes_in):
                    jb, meta = jpeg_fit_to_b64_cap(
                        bytes_in,
                        cap_chars,
                        max_px=max_px,
                        quality_floor=quality_floor
                    )
                    return jb, meta
                b, meta = shrink(b)
            else:
                meta = {"final_dims": None, "jpeg_bytes": len(b)}

            b64 = base64.b64encode(b).decode("ascii")
            b64_full = f"data:image/jpeg;base64,{b64}" if data_uri else b64

            rel = p.relative_to(input_dir)
            out_file = output_dir / rel.with_suffix(".b64.txt")
            ensure_dir(out_file.parent)
            out_file.write_text(b64_full, encoding="utf-8")

            total_in += original_len
            total_out_b64chars += len(b64_full)

            out_map.append({
                "source": str(rel),
                "output": str(out_file.relative_to(output_dir)),
                "orig_bytes": original_len,
                "final_jpeg_bytes": len(b),
                "base64_chars": len(b64_full),
                "data_uri": data_uri,
            })
        except Exception as e:
            skipped += 1
            log(f"[WARN] {p}: {e}")

    (output_dir / "manifest.json").write_text(
        json.dumps(out_map, indent=2),
        encoding="utf-8"
    )

    if csv_map:
        import csv
        with open(output_dir / "manifest.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["source","output","orig_bytes","final_jpeg_bytes","base64_chars","data_uri"])
            for row in out_map:
                w.writerow([
                    row["source"],
                    row["output"],
                    row["orig_bytes"],
                    row["final_jpeg_bytes"],
                    row["base64_chars"],
                    row["data_uri"],
                ])

    log(f"\nDone. Input bytes: {human(total_in)}  -> Total Base64 chars: {total_out_b64chars:,}")
    if skipped:
        log(f"Skipped {skipped} files due to errors.")
    log(f"Outputs are in: {output_dir}")
    return 0
