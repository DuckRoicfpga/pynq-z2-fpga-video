"""Integer golden model, BGR input/output. This module never accesses the FPGA."""
from enum import IntEnum
import numpy as np


class Mode(IntEnum):
    ORIGINAL = 0
    GRAYSCALE = 1
    THRESHOLD = 2
    SOBEL = 3


MODE_NAMES = {0: "Ảnh gốc", 1: "Ảnh xám", 2: "Tách ngưỡng", 3: "Sobel"}


def validate_image(image):
    value = np.asarray(image)
    if value.dtype != np.uint8 or value.ndim != 3 or value.shape[2] != 3:
        raise ValueError("Image must be uint8 H x W x 3 BGR; no implicit scaling.")
    if value.shape[0] < 1 or value.shape[1] < 1:
        raise ValueError("Image dimensions must be positive.")
    return value


def grayscale(image):
    """Round (77 R + 150 G + 29 B)/256 to nearest, ties upward."""
    wide = validate_image(image).astype(np.uint32)
    return ((wide[..., 2] * 77 + wide[..., 1] * 150 + wide[..., 0] * 29 + 128) >> 8).astype(np.uint8)


def apply_filter(image, mode, threshold=128):
    image = validate_image(image)
    mode = Mode(mode)
    if not isinstance(threshold, (int, np.integer)) or not 0 <= int(threshold) <= 255:
        raise ValueError("Threshold must be an integer in 0..255.")
    if mode == Mode.ORIGINAL:
        return image.copy()
    gray = grayscale(image)
    if mode == Mode.GRAYSCALE:
        result = gray
    elif mode == Mode.THRESHOLD:
        result = np.where(gray >= int(threshold), 255, 0).astype(np.uint8)
    else:
        # Signed differences first, then unsigned clipping; border remains zero.
        result = np.zeros(gray.shape, dtype=np.uint8)
        g = gray.astype(np.int32)
        if g.shape[0] >= 3 and g.shape[1] >= 3:
            gx = (-g[:-2, :-2] + g[:-2, 2:] - 2 * g[1:-1, :-2]
                  + 2 * g[1:-1, 2:] - g[2:, :-2] + g[2:, 2:])
            gy = (-g[:-2, :-2] - 2 * g[:-2, 1:-1] - g[:-2, 2:]
                  + g[2:, :-2] + 2 * g[2:, 1:-1] + g[2:, 2:])
            result[1:-1, 1:-1] = np.minimum(255, np.abs(gx) + np.abs(gy)).astype(np.uint8)
    return np.repeat(result[..., None], 3, axis=2)


def pack_pixels(image):
    wide = validate_image(image).astype(np.uint32)
    return (wide[..., 0] | (wide[..., 1] << 8) | (wide[..., 2] << 16)).reshape(-1)


def unpack_pixels(words, width, height):
    words = np.asarray(words)
    if words.dtype != np.uint32 or words.size != int(width) * int(height):
        raise ValueError("Expected width*height uint32 words.")
    if np.any(words & np.uint32(0xFF000000)):
        raise ValueError("Reserved high byte must be zero.")
    words = words.reshape(int(height), int(width))
    return np.stack([(words >> shift).astype(np.uint8) for shift in (0, 8, 16)], axis=2)


def rgb_for_display(image):
    """Pillow and Jupyter display RGB, unlike the hardware BGR transport."""
    return validate_image(image)[..., ::-1].copy()
