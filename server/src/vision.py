"""Shelf camera: find the shop's products in a webcam frame and count them.

An open-vocabulary detector (YOLO-World) is given the catalogue's product
names as text, so adding a product is adding a name, with no training. It runs
locally on the laptop.

A raw per-frame count flickers when a hand passes or a product is half hidden,
so a count only becomes the shelf's count after it has held steady for a few
frames. A product is only tracked once the camera has actually seen it, so
catalogue items that are not on the table are never reported as empty.
"""
import logging
import os
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import shop_db

logger = logging.getLogger("uvicorn.error")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.getenv(
    "VISION_MODEL", os.path.join(_BASE_DIR, "data", "models", "yolov8s-worldv2.pt")
)
CONFIDENCE = float(os.getenv("VISION_CONFIDENCE", "0.15"))
STABLE_FRAMES = int(os.getenv("VISION_STABLE_FRAMES", "3"))
IMAGE_SIZE = int(os.getenv("VISION_IMAGE_SIZE", "640"))


class StableCounts:
    """Turns flickering per-frame counts into counts that have held steady."""

    def __init__(self, frames: int = STABLE_FRAMES):
        self.frames = frames
        self._recent: Dict[str, Deque[int]] = {}
        self._stable: Dict[str, int] = {}
        self._tracked: set = set()

    def reset(self) -> None:
        self._recent.clear()
        self._stable.clear()
        self._tracked.clear()

    def update(self, counts: Dict[str, int]) -> Dict[str, int]:
        """Feed one frame's counts. Returns the products whose steady count
        just changed, with their new count."""
        changed: Dict[str, int] = {}
        for product, count in counts.items():
            if count > 0:
                self._tracked.add(product)
            if product not in self._tracked:
                continue  # never seen on this shelf: do not report it as empty
            recent = self._recent.setdefault(product, deque(maxlen=self.frames))
            recent.append(count)
            steady = len(recent) == self.frames and len(set(recent)) == 1
            if steady and self._stable.get(product) != count:
                self._stable[product] = count
                changed[product] = count
        return changed

    @property
    def stable(self) -> Dict[str, int]:
        return dict(self._stable)

    @property
    def tracked(self) -> List[str]:
        return sorted(self._tracked)


class ShelfVision:
    def __init__(self) -> None:
        self._model: Any = None
        self._class_product: List[str] = []  # detector class index -> product name
        self._lock = threading.Lock()
        self.counts = StableCounts()
        self.error: Optional[str] = None

    def _load(self) -> None:
        """Load the detector and tell it which product names to look for."""
        from ultralytics import YOLOWorld

        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        prompts: List[str] = []
        class_product: List[str] = []
        for product in shop_db.catalogue():
            # A product may list several descriptions, separated by "|".
            for prompt in str(product["detect_label"]).split("|"):
                if prompt.strip():
                    prompts.append(prompt.strip())
                    class_product.append(product["name"])
        model = YOLOWorld(MODEL_PATH)
        model.set_classes(prompts)
        self._model, self._class_product = model, class_product
        logger.info("Shelf vision ready: looking for %s", prompts)

    def warm_up(self) -> None:
        """Load the detector ahead of the first frame (it takes a few seconds)."""
        try:
            with self._lock:
                if self._model is None:
                    self._load()
        except Exception as exc:  # the dashboard buttons still work without it
            self.error = f"{type(exc).__name__}: {exc}"
            logger.warning("Shelf vision could not start: %s", self.error)

    def reset(self) -> None:
        with self._lock:
            self.counts.reset()

    def detect(self, image_bytes: bytes) -> Dict[str, Any]:
        """Detect products in one JPEG frame. Returns boxes, this frame's
        counts, the steady counts, and any steady count that just changed."""
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("That is not a readable image")
        with self._lock:
            if self._model is None:
                self._load()
            result = self._model.predict(
                frame, conf=CONFIDENCE, imgsz=IMAGE_SIZE, agnostic_nms=True, verbose=False
            )[0]
            height, width = frame.shape[:2]
            boxes: List[Dict[str, Any]] = []
            frame_counts = {name: 0 for name in dict.fromkeys(self._class_product)}
            for box in result.boxes:
                product = self._class_product[int(box.cls[0])]
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                boxes.append({
                    "product": product,
                    "confidence": round(float(box.conf[0]), 2),
                    # Fractions of the frame, so the page can draw at any size.
                    "box": [x1 / width, y1 / height, x2 / width, y2 / height],
                })
                frame_counts[product] += 1
            changed = self.counts.update(frame_counts)
            return {
                "boxes": boxes,
                "frame_counts": frame_counts,
                "stable_counts": self.counts.stable,
                "tracked": self.counts.tracked,
                "changed": changed,
            }


shelf_vision = ShelfVision()
