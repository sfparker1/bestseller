"""GrabCut-based product crop (ported from the Photo_Build prototype; bytes in / bytes out)."""
import cv2
import numpy as np
from scipy import ndimage as ndi

MAX_SIDE = 1400   # downscale huge originals before GrabCut (speed + Render memory)
OUT_SIDE = 520    # cropped image is stored at most this many px on the long side


def crop_product(data: bytes, pad: float = 0.06):
    """Return JPEG bytes of the cropped candle(s), or None if no foreground found."""
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    s = MAX_SIDE / max(h, w)
    if s < 1:
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    x0, y0, x1, y1 = int(.08 * w), int(.14 * h), int(.93 * w), int(.84 * h)
    mask[:] = cv2.GC_BGD
    mask[y0:y1, x0:x1] = cv2.GC_PR_BGD
    mask[int(.28 * h):int(.80 * h), int(.2 * w):int(.85 * w)] = cv2.GC_PR_FGD
    bgd = np.zeros((1, 65))
    fgd = np.zeros((1, 65))
    cv2.grabCut(img, mask, None, bgd, fgd, 6, cv2.GC_INIT_WITH_MASK)
    fg = ((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    lab, n = ndi.label(fg)
    if n == 0:
        return None
    sizes = ndi.sum(fg, lab, range(1, n + 1))
    keep = np.zeros_like(fg)
    for i, sz in enumerate(sizes, 1):
        if sz > .15 * sizes.max():
            keep |= (lab == i).astype(np.uint8)
    ys, xs = np.where(keep)
    bx0, bx1, by0, by1 = xs.min(), xs.max(), ys.min(), ys.max()
    px, py = int((bx1 - bx0) * pad), int((by1 - by0) * pad)
    bx0, bx1 = max(0, bx0 - px), min(w, bx1 + px)
    by0, by1 = max(0, by0 - py), min(h, by1 + py)
    out = img[by0:by1, bx0:bx1]
    oh, ow = out.shape[:2]
    s = OUT_SIDE / max(oh, ow)
    if s < 1:
        out = cv2.resize(out, (int(ow * s), int(oh * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode('.jpg', out, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes() if ok else None
