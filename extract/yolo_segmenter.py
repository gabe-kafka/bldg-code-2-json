"""
YOLOv8-based page layout segmenter.

Trains on human-labeled gold data from classifications.json,
then predicts regions on unlabeled pages.
"""

import json
import shutil
from pathlib import Path

# Class mapping — must match across export, train, and predict
CLASSES = ["heading", "text_block", "definition", "table", "equation", "figure"]
CLASS_TO_IDX = {c: i for i, c in enumerate(CLASSES)}
IDX_TO_CLASS = {i: c for i, c in enumerate(CLASSES)}

# Default classification per type
DEFAULT_CLS = {
    "heading": "structured",
    "text_block": "structured",
    "definition": "structured",
    "table": "structured",
    "equation": "structured",
    "figure": "linked",
}


def export_yolo_dataset(classifications_path, pages_dir, output_dir):
    """Convert gold classifications to YOLO format dataset.

    Creates:
      output_dir/
        images/train/  — symlinks to page PNGs
        labels/train/  — YOLO format txt files
        data.yaml      — dataset config
    """
    classifications_path = Path(classifications_path)
    pages_dir = Path(pages_dir)
    output_dir = Path(output_dir)

    gold = json.loads(classifications_path.read_text())
    pages = gold.get("pages", {})

    img_dir = output_dir / "images" / "train"
    lbl_dir = output_dir / "labels" / "train"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    # Get image dimensions from first page
    from PIL import Image
    sample = next(pages_dir.iterdir())
    with Image.open(sample) as im:
        img_w, img_h = im.size

    exported = 0
    for pg_key, pg_data in pages.items():
        regions = pg_data.get("regions", [])
        if not regions:
            continue
        if pg_data.get("predicted"):
            continue  # skip predictions, only use human-labeled

        img_name = f"page-{int(pg_key):03d}.png"
        img_path = pages_dir / img_name
        if not img_path.exists():
            continue

        # Symlink image
        dst_img = img_dir / img_name
        if dst_img.exists():
            dst_img.unlink()
        dst_img.symlink_to(img_path.resolve())

        # Write YOLO label file
        lbl_path = lbl_dir / f"page-{int(pg_key):03d}.txt"
        lines = []
        for r in regions:
            rt = r.get("region_type", "text_block")
            if rt not in CLASS_TO_IDX:
                continue

            cls_idx = CLASS_TO_IDX[rt]
            # YOLO format: class x_center y_center width height (all normalized 0-1)
            x0, y0, x1, y1 = r["x0"], r["y0"], r["x1"], r["y1"]
            cx = ((x0 + x1) / 2) / img_w
            cy = ((y0 + y1) / 2) / img_h
            bw = (x1 - x0) / img_w
            bh = (y1 - y0) / img_h
            # Clamp to [0, 1]
            cx = max(0, min(1, cx))
            cy = max(0, min(1, cy))
            bw = max(0, min(1, bw))
            bh = max(0, min(1, bh))
            lines.append(f"{cls_idx} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

        lbl_path.write_text("\n".join(lines) + "\n" if lines else "")
        exported += 1

    # Write data.yaml
    yaml_path = output_dir / "data.yaml"
    yaml_path.write_text(
        f"path: {output_dir.resolve()}\n"
        f"train: images/train\n"
        f"val: images/train\n"  # same for now with small dataset
        f"nc: {len(CLASSES)}\n"
        f"names: {CLASSES}\n"
    )

    return exported, yaml_path


def train(data_yaml, epochs=100, imgsz=1024, model_name="yolov8n.pt",
          project="output/yolo", name="segmenter"):
    """Fine-tune YOLOv8 on the exported dataset."""
    from ultralytics import YOLO

    model = YOLO(model_name)
    results = model.train(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=imgsz,
        project=project,
        name=name,
        exist_ok=True,
        verbose=True,
        # Small dataset augmentation
        hsv_h=0.0,  # no color augmentation for document images
        hsv_s=0.0,
        hsv_v=0.1,
        flipud=0.0,  # don't flip documents
        fliplr=0.0,
        mosaic=0.0,  # don't mosaic documents
        scale=0.2,
        translate=0.1,
    )
    best = Path(project) / name / "weights" / "best.pt"
    return best, results


def predict(model_path, image_path, conf=0.25, imgsz=1024):
    """Run inference on a single page image.

    Returns list of region dicts matching the classify tool format.
    """
    from ultralytics import YOLO

    model = YOLO(str(model_path))
    results = model.predict(
        str(image_path),
        conf=conf,
        imgsz=imgsz,
        verbose=False,
    )

    from PIL import Image
    with Image.open(image_path) as im:
        img_w, img_h = im.size

    regions = []
    for result in results:
        for box in result.boxes:
            cls_idx = int(box.cls[0])
            conf_val = float(box.conf[0])
            x0, y0, x1, y1 = box.xyxy[0].tolist()

            region_type = IDX_TO_CLASS.get(cls_idx, "text_block")
            regions.append({
                "x0": int(x0),
                "y0": int(y0),
                "x1": int(x1),
                "y1": int(y1),
                "region_type": region_type,
                "classification": DEFAULT_CLS.get(region_type, "structured"),
                "title_index": "",
                "title_name": "",
                "label": f"{region_type} ({conf_val:.2f})",
            })

    regions.sort(key=lambda r: (r["y0"], r["x0"]))
    return regions


def predict_batch(model_path, pages_dir, classifications_path, conf=0.25):
    """Predict all unlabeled pages and save to classifications."""
    pages_dir = Path(pages_dir)
    classifications_path = Path(classifications_path)

    gold = json.loads(classifications_path.read_text())
    labeled = {k for k, v in gold.get("pages", {}).items()
               if v.get("regions") and not v.get("predicted")}

    import re
    predicted = 0
    for f in sorted(pages_dir.iterdir()):
        m = re.match(r"page-(\d+)\.png", f.name)
        if not m:
            continue
        pk = str(int(m.group(1)))
        if pk in labeled:
            continue

        regions = predict(model_path, f, conf=conf)
        gold.setdefault("pages", {})[pk] = {
            "regions": regions,
            "predicted": True,
        }
        predicted += 1
        print(f"  page {pk}: {len(regions)} regions")

    classifications_path.write_text(json.dumps(gold, indent=2))
    return predicted
