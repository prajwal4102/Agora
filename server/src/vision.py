"""Shelf camera: find the shop's products in a webcam frame and count them.

An open-vocabulary detector (YOLO-World) is given the catalogue's product
names as text, so adding a product is adding a name, with no training. It runs
locally on the laptop.

Shape alone cannot name a flat wrapped product: a chocolate bar in its wrapper
is, to the detector, a "banner". So recognition is two steps for those. The
detector finds flat printed objects, then a second model (CLIP) looks at each
one and says which wrapped product it is, or that it is none of them.

A raw per-frame count flickers when a hand passes or a product is half hidden,
so a count only becomes the shelf's count after it has held steady for a few
frames. A product is only tracked once the camera has actually seen it, so
catalogue items that are not on the table are never reported as empty.
"""
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import shop_db

logger = logging.getLogger("uvicorn.error")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.getenv(
    "VISION_MODEL", os.path.join(_BASE_DIR, "data", "models", "yolov8x-worldv2.pt")
)
FRAMES_DIR = os.path.join(_BASE_DIR, "data", "frames")
# How sure the detector must be, unless a product description sets its own floor.
CONFIDENCE = float(os.getenv("VISION_CONFIDENCE", "0.3"))
# What a wrapped product looks like to the shape detector.
FLAT_SHAPES = ("banner", "wrapper", "packet")
# The detector is asked for every object it can find down to this faint level.
# Anything it is not sure about goes to the second step instead of being
# dropped: with several wrapped bars side by side it finds all of them but
# half-heartedly calls some "bottle", and those used to be thrown away.
FIND_FLOOR = float(os.getenv("VISION_FIND_CONFIDENCE", "0.03"))
# How sure the second step must be before an object counts as a wrapped product.
READ_FLOOR = float(os.getenv("VISION_READ_CONFIDENCE", "0.8"))
# How sure the reader must be to overrule a shape the detector accepted.
READ_OVERRULES = float(os.getenv("VISION_READ_OVERRULES", "0.85"))
# A box mostly covered by other boxes of the same product is a second look at
# the same items (often one box swallowing two neighbours), not another item.
COVERED = float(os.getenv("VISION_COVERED", "0.6"))
# Flat things on a table that are not products. Giving the reader these to
# choose from stops it forcing every flat object into a product.
NOT_PRODUCTS = (
    "a cardboard box", "a sheet of paper", "a phone or a screen", "a wall",
    "a person", "a tube of toothpaste", "a book", "a table",
)
# Boxes overlapping more than this are treated as one object.
OVERLAP = float(os.getenv("VISION_OVERLAP", "0.7"))
STABLE_FRAMES = int(os.getenv("VISION_STABLE_FRAMES", "3"))
IMAGE_SIZE = int(os.getenv("VISION_IMAGE_SIZE", "640"))


def parse_detect_label(label: str) -> List[Tuple[str, float]]:
    """Read the camera descriptions of a product: alternatives separated by "|",
    each optionally followed by "@" and its own confidence floor.

    "bottle|water bottle" gives both at the default floor.
    "pen@0.1" gives pens a lower floor: thin objects score low even when found.

    A description starting with "~" is not a shape for the detector but a
    sentence for the second step, for flat wrapped products; see
    `wrapped_description`.
    """
    found: List[Tuple[str, float]] = []
    for part in str(label or "").split("|"):
        text, _, floor = part.partition("@")
        if text.strip().startswith("~"):
            continue
        if text.strip():
            try:
                found.append((text.strip(), float(floor) if floor.strip() else CONFIDENCE))
            except ValueError:
                found.append((text.strip(), CONFIDENCE))
    return found


def wrapped_description(label: str) -> Optional[str]:
    """The sentence the second step uses to recognise a flat wrapped product,
    e.g. "~a chocolate bar in its wrapper" -> "a chocolate bar in its wrapper".
    None for products recognised by shape alone."""
    for part in str(label or "").split("|"):
        if part.strip().startswith("~"):
            return part.strip()[1:].strip() or None
    return None


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
            recent = self._recent.setdefault(product, deque(maxlen=self.frames))
            recent.append(count)
            steady = len(recent) == self.frames and len(set(recent)) == 1
            if steady and count > 0:
                # Seen steadily, not for one blurred frame: now it is tracked.
                self._tracked.add(product)
            if product not in self._tracked:
                continue  # never properly seen on this shelf: do not report it as empty
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


FLAT = "(flat object)"  # stand-in for "some wrapped product, not yet named"

Found = Tuple[str, float, Tuple[int, int, int, int]]  # product, confidence, box corners


def _area(box: Tuple[int, int, int, int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _touches_edge(box: Tuple[int, int, int, int], width: int, height: int) -> bool:
    """Is this box cut off by the edge of the picture?"""
    margin = max(2, int(0.01 * min(width, height)))
    return (
        box[0] <= margin or box[1] <= margin
        or box[2] >= width - margin or box[3] >= height - margin
    )


def _without_repeats(found: List[Found]) -> List[Found]:
    """Drop boxes that are a second look at items already counted.

    Smallest boxes are kept first, since a small box is one item. A larger box
    is then kept only if most of it shows something new; one that mostly covers
    boxes already kept (typically a box around two neighbouring items) is not
    another item."""
    kept: List[Found] = []
    for item in sorted(found, key=lambda entry: _area(entry[2])):
        product, _, box = item
        covered = 0
        for other_product, _, other in kept:
            if other_product == product:
                covered += _area((
                    max(box[0], other[0]), max(box[1], other[1]),
                    min(box[2], other[2]), min(box[3], other[3]),
                ))
        if covered < COVERED * _area(box):
            kept.append(item)
    return kept


class ShelfVision:
    def __init__(self) -> None:
        self._model: Any = None
        self._class_product: List[str] = []  # detector class index -> product name
        self._class_floor: List[float] = []  # detector class index -> confidence floor
        # Second step, for flat wrapped products.
        self._reader: Any = None
        self._reader_prepare: Any = None
        self._reader_names: List[Optional[str]] = []  # product name, or None for a non-product
        self._reader_text: Any = None
        self._lock = threading.Lock()
        self.counts = StableCounts()
        self.error: Optional[str] = None
        self.last_frame: Optional[bytes] = None  # most recent frame, for tuning
        self._last_saved = 0.0
        self._saved = -1

    def _load(self) -> None:
        """Load the detector and tell it which product names to look for."""
        from ultralytics import YOLOWorld

        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        prompts: List[str] = []
        class_product: List[str] = []
        class_floor: List[float] = []
        wrapped: Dict[str, str] = {}
        for product in shop_db.catalogue():
            for prompt, floor in parse_detect_label(product["detect_label"]):
                prompts.append(prompt)
                class_product.append(product["name"])
                class_floor.append(floor)
            sentence = wrapped_description(product["detect_label"])
            if sentence:
                wrapped[product["name"]] = sentence
        if wrapped:
            for shape in FLAT_SHAPES:
                prompts.append(shape)
                class_product.append(FLAT)
                class_floor.append(FIND_FLOOR)
            self._load_reader(wrapped)
        model = YOLOWorld(MODEL_PATH)
        model.set_classes(prompts)
        self._model, self._class_product, self._class_floor = model, class_product, class_floor
        logger.info("Shelf vision ready: looking for %s", prompts)

    def _load_reader(self, wrapped: Dict[str, str]) -> None:
        """Prepare the second step: the sentences it chooses between."""
        import clip
        import torch

        self._reader, self._reader_prepare = clip.load("ViT-B/32", device="cpu")
        self._reader_names = [*wrapped.keys(), *([None] * len(NOT_PRODUCTS))]
        sentences = [*wrapped.values(), *NOT_PRODUCTS]
        with torch.no_grad():
            text = self._reader.encode_text(clip.tokenize(sentences))
            self._reader_text = text / text.norm(dim=-1, keepdim=True)

    def _read(self, frame: Any, boxes: List[Tuple[int, int, int, int]]) -> List[Tuple[Optional[str], float]]:
        """Second step: for each object, which wrapped product is it, if any?
        All objects are read in one pass, which is much quicker than one by one."""
        import cv2
        import torch
        from PIL import Image

        if not boxes or self._reader is None:
            return [(None, 0.0)] * len(boxes)
        height, width = frame.shape[:2]
        images, usable = [], []
        for x1, y1, x2, y2 in boxes:
            crop = frame[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]
            usable.append(crop.size > 0)
            if crop.size > 0:
                images.append(self._reader_prepare(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))))
        if not images:
            return [(None, 0.0)] * len(boxes)
        with torch.no_grad():
            seen = self._reader.encode_image(torch.stack(images))
            seen = seen / seen.norm(dim=-1, keepdim=True)
            scores = (100 * seen @ self._reader_text.T).softmax(-1)
        answers = iter(
            (self._reader_names[int(row.argmax())], float(row.max())) for row in scores
        )
        return [next(answers) if ok else (None, 0.0) for ok in usable]

    def _keep_for_tuning(self, image_bytes: bytes) -> None:
        """Save about one frame a second, the newest 90, so detection problems
        can be looked at afterwards without anyone having to re-stage them."""
        now = time.time()
        if now - self._last_saved < 1.0:
            return
        self._last_saved = now
        try:
            os.makedirs(FRAMES_DIR, exist_ok=True)
            self._saved = (self._saved + 1) % 90
            with open(os.path.join(FRAMES_DIR, f"frame_{self._saved:02d}.jpg"), "wb") as handle:
                handle.write(image_bytes)
        except OSError:
            pass

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
        self.last_frame = image_bytes
        self._keep_for_tuning(image_bytes)
        with self._lock:
            if self._model is None:
                self._load()
            result = self._model.predict(
                frame, conf=min([FIND_FLOOR, *self._class_floor]), iou=OVERLAP, imgsz=IMAGE_SIZE,
                agnostic_nms=True, verbose=False,
            )[0]
            height, width = frame.shape[:2]
            frame_counts = {
                name: 0 for name in dict.fromkeys([*self._class_product, *self._reader_names])
                if name and name != FLAT
            }
            # Step one: keep what the detector is sure of by shape, and set
            # everything else aside for the reader.
            # Every object the detector found, faint or not, with what the
            # reader makes of it.
            candidates = [
                (self._class_product[int(box.cls[0])], float(box.conf[0]),
                 self._class_floor[int(box.cls[0])], tuple(int(v) for v in box.xyxy[0]))
                for box in result.boxes
            ]
            readings = self._read(frame, [corners for _, _, _, corners in candidates])
            found: List[Found] = []
            for (shape, confidence, floor, corners), (read_as, read_confidence) in zip(candidates, readings):
                is_shape = shape != FLAT and confidence >= floor
                if read_as is not None and read_confidence >= READ_FLOOR and (
                    not is_shape or read_confidence >= READ_OVERRULES
                ):
                    # The reader recognises a wrapped product. That settles an
                    # object with no confident shape, and overrules a shape
                    # label when the reader is very sure: a blurred chocolate
                    # bar is a confident "pen" to the detector.
                    found.append((read_as, read_confidence, corners))
                elif is_shape and confidence >= CONFIDENCE:
                    found.append((shape, confidence, corners))
                elif is_shape and not _touches_edge(corners, width, height):
                    # A faint shape, allowed by a low floor such as pens have.
                    # At the edge of the picture these are cables and shadows.
                    found.append((shape, confidence, corners))
            boxes: List[Dict[str, Any]] = []
            for product, confidence, (x1, y1, x2, y2) in _without_repeats(found):
                boxes.append({
                    "product": product,
                    "confidence": round(confidence, 2),
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
