"""
Arena: compare layout detection approaches on gold data.

Uses leave-one-out evaluation — for each gold page, predict using
the approach and score against the human labels.
"""

import json
import time
from pathlib import Path

import numpy as np
from PIL import Image


def load_gold(classifications_path, pages_dir):
    """Load gold-labeled pages with regions."""
    gold = json.loads(Path(classifications_path).read_text())
    pages = []
    for pk, pv in sorted(gold.get("pages", {}).items(), key=lambda x: int(x[0])):
        regions = pv.get("regions", [])
        if not regions or pv.get("predicted"):
            continue
        img_path = Path(pages_dir) / f"page-{int(pk):03d}.png"
        if not img_path.exists():
            continue
        pages.append({"key": pk, "img": img_path, "regions": regions})
    return pages


def iou(a, b):
    """Intersection over union of two boxes."""
    x0 = max(a["x0"], b["x0"])
    y0 = max(a["y0"], b["y0"])
    x1 = min(a["x1"], b["x1"])
    y1 = min(a["y1"], b["y1"])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    area_a = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"])
    area_b = (b["x1"] - b["x0"]) * (b["y1"] - b["y0"])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0


def score_predictions(pred_regions, gold_regions, iou_threshold=0.3):
    """Score predicted regions against gold.

    Returns: precision, recall, type_accuracy, mean_iou
    """
    if not gold_regions:
        return {"precision": 1.0 if not pred_regions else 0.0,
                "recall": 1.0, "type_acc": 1.0, "mean_iou": 1.0, "matched": 0}

    # Match each gold region to best predicted region by IoU
    matched_pred = set()
    matched_gold = set()
    ious = []
    type_correct = 0

    for gi, g in enumerate(gold_regions):
        best_iou = 0
        best_pi = -1
        for pi, p in enumerate(pred_regions):
            if pi in matched_pred:
                continue
            v = iou(g, p)
            if v > best_iou:
                best_iou = v
                best_pi = pi
        if best_iou >= iou_threshold and best_pi >= 0:
            matched_pred.add(best_pi)
            matched_gold.add(gi)
            ious.append(best_iou)
            if pred_regions[best_pi].get("region_type") == g.get("region_type"):
                type_correct += 1

    n_matched = len(matched_gold)
    precision = n_matched / len(pred_regions) if pred_regions else 0
    recall = n_matched / len(gold_regions)
    type_acc = type_correct / n_matched if n_matched > 0 else 0
    mean_iou = np.mean(ious) if ious else 0

    return {
        "precision": precision,
        "recall": recall,
        "type_acc": type_acc,
        "mean_iou": float(mean_iou),
        "matched": n_matched,
        "predicted": len(pred_regions),
        "gold": len(gold_regions),
    }


# ─── APPROACH 1: SURYA (zero-shot, no training) ───

def run_surya(pages):
    """Run Surya layout detection on gold pages."""
    from surya.detection import DetectionPredictor

    predictor = DetectionPredictor()

    results = []
    total_time = 0
    for pg in pages:
        img = Image.open(pg["img"])
        t0 = time.time()
        preds = predictor([img])
        elapsed = time.time() - t0
        total_time += elapsed

        pred_regions = []
        for p in preds[0].bboxes:
            # Surya returns xyxy coordinates
            # Map Surya labels to our types
            label = p.label.lower() if hasattr(p, 'label') else 'text_block'
            type_map = {
                'text': 'text_block', 'title': 'heading', 'list': 'text_block',
                'table': 'table', 'figure': 'figure', 'caption': 'text_block',
                'header': 'heading', 'footer': 'text_block',
                'section-header': 'heading', 'page-header': 'heading',
                'page-footer': 'text_block', 'picture': 'figure',
                'formula': 'equation',
            }
            region_type = type_map.get(label, 'text_block')

            bbox = p.bbox if hasattr(p, 'bbox') else [p.polygon[0][0], p.polygon[0][1], p.polygon[2][0], p.polygon[2][1]]
            pred_regions.append({
                "x0": int(bbox[0]), "y0": int(bbox[1]),
                "x1": int(bbox[2]), "y1": int(bbox[3]),
                "region_type": region_type,
            })

        scores = score_predictions(pred_regions, pg["regions"])
        scores["page"] = pg["key"]
        scores["time"] = elapsed
        results.append(scores)

    return results, total_time


# ─── APPROACH 2: YOLO (fine-tuned on N-1 pages) ───

def run_yolo_loo(pages):
    """Leave-one-out YOLO evaluation. Train on N-1, test on 1."""
    from extract.yolo_segmenter import CLASSES, CLASS_TO_IDX

    results = []
    total_time = 0

    for hold_out_idx in range(len(pages)):
        train_pages = [p for i, p in enumerate(pages) if i != hold_out_idx]
        test_page = pages[hold_out_idx]

        # Quick export + train
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            img_dir = tmpdir / "images" / "train"
            lbl_dir = tmpdir / "labels" / "train"
            img_dir.mkdir(parents=True)
            lbl_dir.mkdir(parents=True)

            sample_img = Image.open(train_pages[0]["img"])
            img_w, img_h = sample_img.size
            sample_img.close()

            for tp in train_pages:
                name = Path(tp["img"]).name
                (img_dir / name).symlink_to(Path(tp["img"]).resolve())
                lines = []
                for r in tp["regions"]:
                    rt = r.get("region_type", "text_block")
                    if rt not in CLASS_TO_IDX:
                        continue
                    cx = ((r["x0"] + r["x1"]) / 2) / img_w
                    cy = ((r["y0"] + r["y1"]) / 2) / img_h
                    bw = (r["x1"] - r["x0"]) / img_w
                    bh = (r["y1"] - r["y0"]) / img_h
                    lines.append(f"{CLASS_TO_IDX[rt]} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
                (lbl_dir / name.replace(".png", ".txt")).write_text("\n".join(lines) + "\n")

            yaml_path = tmpdir / "data.yaml"
            yaml_path.write_text(
                f"path: {tmpdir}\ntrain: images/train\nval: images/train\n"
                f"nc: {len(CLASSES)}\nnames: {CLASSES}\n"
            )

            from ultralytics import YOLO
            model = YOLO("yolov8n.pt")
            t0 = time.time()
            model.train(data=str(yaml_path), epochs=30, imgsz=1024,
                       project=str(tmpdir / "runs"), name="loo",
                       exist_ok=True, verbose=False,
                       hsv_h=0, hsv_s=0, hsv_v=0.1,
                       flipud=0, fliplr=0, mosaic=0, scale=0.2, translate=0.1)
            train_time = time.time() - t0

            best = tmpdir / "runs" / "loo" / "weights" / "best.pt"
            if not best.exists():
                best = tmpdir / "runs" / "loo" / "weights" / "last.pt"

            from extract.yolo_segmenter import predict, IDX_TO_CLASS
            t0 = time.time()
            pred_regions = predict(best, test_page["img"], conf=0.15)
            pred_time = time.time() - t0
            total_time += train_time + pred_time

        scores = score_predictions(pred_regions, test_page["regions"])
        scores["page"] = test_page["key"]
        scores["time"] = train_time + pred_time
        results.append(scores)
        print(f"    YOLO LOO page {test_page['key']}: "
              f"P={scores['precision']:.2f} R={scores['recall']:.2f} "
              f"IoU={scores['mean_iou']:.2f} type={scores['type_acc']:.2f} "
              f"({scores['matched']}/{scores['gold']} matched)")

    return results, total_time


# ─── APPROACH 3: OpenCV (zero-shot, no training) ───

def run_opencv(pages):
    """Run OpenCV morphological detection."""
    import cv2

    results = []
    total_time = 0

    for pg in pages:
        t0 = time.time()
        img = cv2.imread(str(pg["img"]), cv2.IMREAD_GRAYSCALE)
        h, w = img.shape

        _, binary = cv2.threshold(img, 230, 255, cv2.THRESH_BINARY_INV)
        kh = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        dilated = cv2.dilate(binary, kh, iterations=1)
        kv = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 12))
        dilated = cv2.dilate(dilated, kv, iterations=1)
        kh2 = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 1))
        dilated = cv2.dilate(dilated, kh2, iterations=1)
        kv2 = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 8))
        dilated = cv2.dilate(dilated, kv2, iterations=1)

        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        elapsed = time.time() - t0
        total_time += elapsed

        pred_regions = []
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            if bw * bh < 500 or bh < 10 or bw < 20:
                continue
            if x < 60 and bw < 100:
                continue
            if y > h * 0.97:
                continue
            pred_regions.append({
                "x0": x, "y0": y, "x1": x + bw, "y1": y + bh,
                "region_type": "text_block",  # OpenCV can't classify
            })

        scores = score_predictions(pred_regions, pg["regions"])
        scores["page"] = pg["key"]
        scores["time"] = elapsed
        results.append(scores)

    return results, total_time


# ─── MAIN ───

def run_arena(classifications_path, pages_dir):
    pages = load_gold(classifications_path, pages_dir)
    print(f"Gold data: {len(pages)} pages, "
          f"{sum(len(p['regions']) for p in pages)} regions\n")

    approaches = {}

    # 1. Surya
    print("=== SURYA (zero-shot) ===")
    try:
        surya_results, surya_time = run_surya(pages)
        approaches["surya"] = (surya_results, surya_time)
        for r in surya_results:
            print(f"  page {r['page']}: P={r['precision']:.2f} R={r['recall']:.2f} "
                  f"IoU={r['mean_iou']:.2f} type={r['type_acc']:.2f}")
    except Exception as e:
        print(f"  FAILED: {e}")

    # 2. OpenCV
    print("\n=== OPENCV (zero-shot) ===")
    try:
        cv_results, cv_time = run_opencv(pages)
        approaches["opencv"] = (cv_results, cv_time)
        for r in cv_results:
            print(f"  page {r['page']}: P={r['precision']:.2f} R={r['recall']:.2f} "
                  f"IoU={r['mean_iou']:.2f}")
    except Exception as e:
        print(f"  FAILED: {e}")

    # 3. YOLO (leave-one-out)
    print("\n=== YOLO (leave-one-out, 30 epochs each) ===")
    try:
        yolo_results, yolo_time = run_yolo_loo(pages)
        approaches["yolo"] = (yolo_results, yolo_time)
    except Exception as e:
        print(f"  FAILED: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Approach':<12} {'Precision':>10} {'Recall':>10} {'IoU':>10} {'Type Acc':>10} {'Time':>10}")
    print("-" * 60)
    for name, (results, total_time) in approaches.items():
        avg_p = np.mean([r["precision"] for r in results])
        avg_r = np.mean([r["recall"] for r in results])
        avg_iou = np.mean([r["mean_iou"] for r in results])
        avg_type = np.mean([r["type_acc"] for r in results])
        print(f"{name:<12} {avg_p:>10.2f} {avg_r:>10.2f} {avg_iou:>10.2f} {avg_type:>10.2f} {total_time:>9.1f}s")


if __name__ == "__main__":
    run_arena("output/qc/classifications.json", "output/pages/asce722-ch26")
