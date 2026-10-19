"""Independent numerical checks, including scalar neighborhood convolution oracle."""
import unittest
import numpy as np
from reference_filters import apply_filter, grayscale, pack_pixels, unpack_pixels


def scalar_oracle(image, mode, threshold):
    h, w, _ = image.shape
    luminance = [[int(round((float(int(image[y, x, 2])) * 77 + int(image[y, x, 1]) * 150
                            + int(image[y, x, 0]) * 29 + 0.01) / 256.0))
                  for x in range(w)] for y in range(h)]
    # Above floating form rounds half up (0.01 perturbation below any 1/256 step).
    kernel_x = ((-1, 0, 1), (-2, 0, 2), (-1, 0, 1))
    kernel_y = ((-1, -2, -1), (0, 0, 0), (1, 2, 1))
    out = np.zeros_like(image)
    for y in range(h):
        for x in range(w):
            if mode == 0:
                out[y, x] = image[y, x]
                continue
            value = luminance[y][x]
            if mode == 2:
                value = 255 if value >= threshold else 0
            elif mode == 3:
                if y in (0, h - 1) or x in (0, w - 1):
                    value = 0
                else:
                    sx = sy = 0
                    for dy in range(-1, 2):
                        for dx in range(-1, 2):
                            sample = luminance[y + dy][x + dx]
                            sx += kernel_x[dy + 1][dx + 1] * sample
                            sy += kernel_y[dy + 1][dx + 1] * sample
                    value = min(255, abs(sx) + abs(sy))
            out[y, x] = value
    return out


class ReferenceTests(unittest.TestCase):
    def test_known_primary_colors(self):
        img = np.array([[[0,0,255],[0,255,0],[255,0,0],[255,255,255],[0,0,0]]], np.uint8)
        np.testing.assert_array_equal(grayscale(img), [[77,149,29,255,0]])

    def test_threshold_equality_and_extremes(self):
        img = np.repeat(np.array([[0,127,128,255]], np.uint8)[...,None], 3, 2)
        np.testing.assert_array_equal(apply_filter(img,2,128)[...,0], [[0,0,255,255]])
        self.assertTrue(np.all(apply_filter(img,2,0) == 255))
        np.testing.assert_array_equal(apply_filter(img,2,255)[...,0], [[0,0,0,255]])

    def test_sobel_slope_known_magnitude(self):
        img = np.repeat(np.tile(np.arange(5,dtype=np.uint8),(5,1))[...,None],3,2)
        out = apply_filter(img,3)[...,0]
        np.testing.assert_array_equal(out[1:-1,1:-1], np.full((3,3),8,np.uint8))
        self.assertTrue(np.all(out[[0,-1]]==0) and np.all(out[:,[0,-1]]==0))

    def test_sobel_clips_without_signed_overflow(self):
        img = np.zeros((5,5,3),np.uint8)
        img[:,3:] = 255
        out = apply_filter(img,3)
        self.assertTrue(np.all(out[1:-1,2:4]==255))

    def test_small_images_and_constant_sobel(self):
        for h,w in ((1,1),(1,6),(6,1),(2,2),(6,8)):
            self.assertFalse(np.any(apply_filter(np.full((h,w,3),200,np.uint8),3)))

    def test_scalar_neighborhood_oracle_random(self):
        rng = np.random.RandomState(31)
        for h,w in ((3,3),(6,8),(12,16)):
            image = rng.randint(0,256,(h,w,3)).astype(np.uint8)
            for mode in range(4):
                np.testing.assert_array_equal(apply_filter(image,mode,73), scalar_oracle(image,mode,73))

    def test_transport_endianness_and_reserved_byte(self):
        image = np.array([[[0x12,0x34,0x56],[255,128,1]]],np.uint8)
        words = pack_pixels(image)
        self.assertEqual(int(words[0]),0x00563412)
        np.testing.assert_array_equal(unpack_pixels(words,2,1),image)
        with self.assertRaises(ValueError):
            unpack_pixels(np.array([0xff000000],np.uint32),1,1)

    def test_reject_ambiguous_input(self):
        with self.assertRaises(ValueError): apply_filter(np.zeros((3,3,3)),1)
        with self.assertRaises(ValueError): apply_filter(np.zeros((3,3,3),np.uint8),4)
        with self.assertRaises(ValueError): apply_filter(np.zeros((3,3,3),np.uint8),2,256)


if __name__ == "__main__": unittest.main()
