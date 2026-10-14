"""Generate windows/marbletrack.ico (PNG-compressed icon, 16-256 px) from code, so no binary editor is needed."""
import struct
from pathlib import Path

import cv2
import numpy as np


def frame(size: int) -> bytes:
    k = 8  # supersample for smooth edges
    S = size * k
    img = np.zeros((S, S, 4), np.uint8)
    r = int(S * 0.22)
    cv2.rectangle(img, (0, 0), (S - 1, S - 1), (26, 19, 14, 255), -1)  # #0e131a, BGRA
    mask = np.zeros((S, S), np.uint8)
    cv2.rectangle(mask, (r, 0), (S - r, S), 255, -1)
    cv2.rectangle(mask, (0, r), (S, S - r), 255, -1)
    for cx, cy in ((r, r), (S - r, r), (r, S - r), (S - r, S - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1)
    img[..., 3] = mask
    cv2.circle(img, (S // 2, S // 2), int(S * 0.33), (59, 169, 242, 255), -1, cv2.LINE_AA)  # amber marble
    cv2.circle(img, (int(S * 0.39), int(S * 0.39)), int(S * 0.1), (245, 245, 245, 255), -1, cv2.LINE_AA)  # glint
    small = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".png", small)[1].tobytes()


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = [frame(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    off = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for s, png in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), off + len(data))
        data += png
    out = Path(__file__).with_name("marbletrack.ico")
    out.write_bytes(head + entries + data)
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
