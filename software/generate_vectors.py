"""Create reproducible independent input patterns and all four golden outputs."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image
from reference_filters import apply_filter, pack_pixels, rgb_for_display


SEED = 20261015


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def patterns(width, height):
    x = np.arange(width)[None, :]
    y = np.arange(height)[:, None]
    palette = np.array([[0, 0, 0], [255, 255, 255], [0, 0, 255], [0, 255, 0],
                        [255, 0, 0], [255, 255, 0], [255, 0, 255], [0, 255, 255]], dtype=np.uint8)
    colors = np.broadcast_to(palette[(x * 8 // width).clip(0, 7)], (height, width, 3)).copy()
    ramp = np.broadcast_to((x * 255 // max(1, width - 1)).astype(np.uint8), (height, width))
    step = np.broadcast_to(np.where(x < width // 2, 0, 255).astype(np.uint8), (height, width))
    impulse = np.zeros((height, width), np.uint8)
    impulse[height // 2, width // 2] = 255
    checker = np.where((x + y) % 2, 255, 0).astype(np.uint8)
    boundary = np.zeros((height, width), np.uint8)
    boundary[0, :] = 255
    boundary[-1, :] = 129
    boundary[:, 0] = 127
    boundary[:, -1] = 128
    diagonal = np.where((x * height // width) == y, 255, 0).astype(np.uint8)
    random = np.random.RandomState(SEED + width * 1000 + height).randint(0, 256, (height, width, 3)).astype(np.uint8)
    mono = lambda v: np.repeat(v[..., None], 3, axis=2)
    return {"colors": colors, "ramp": mono(ramp), "step": mono(step), "impulse": mono(impulse),
            "checkerboard": mono(checker), "boundary": mono(boundary), "diagonal": mono(diagonal),
            "black": np.zeros((height, width, 3), np.uint8),
            "white": np.full((height, width, 3), 255, np.uint8), "random": random}


def generate(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    files = {}
    for width, height in ((8, 6), (16, 12), (640, 480)):
        selected = patterns(width, height)
        # Full VGA is a throughput/frame-state regression, smaller patterns cover directed arithmetic.
        if width == 640:
            selected = {name: selected[name] for name in ("colors", "step", "random")}
        for name, image in selected.items():
            case_name = "%dx%d_%s" % (width, height, name)
            folder = output / case_name
            folder.mkdir(parents=True, exist_ok=True)
            threshold = 128
            arrays = {"input": image}
            def write_mem(filename, array):
                path = folder / filename
                path.write_text("".join("%08x\n" % int(v) for v in pack_pixels(array)), encoding="ascii")
                relative = path.relative_to(output).as_posix()
                files[relative] = {"sha256": sha256(path), "bytes": path.stat().st_size}
                return relative
            input_mem = write_mem("input.mem", image)
            outputs = {}
            for mode in range(4):
                arrays["mode%d" % mode] = apply_filter(image, mode, threshold)
                outputs[str(mode)] = write_mem("output_mode%d.mem" % mode, arrays["mode%d" % mode])
            npz = folder / "vectors.npz"
            # Uncompressed .npy entries carry fixed headers; zip timestamp does not affect value verification.
            np.savez_compressed(str(npz), **arrays)
            files[npz.relative_to(output).as_posix()] = {"sha256": sha256(npz), "bytes": npz.stat().st_size}
            png = folder / "input.png"
            Image.fromarray(rgb_for_display(image)).save(str(png))
            files[png.relative_to(output).as_posix()] = {"sha256": sha256(png), "bytes": png.stat().st_size}
            cases.append({"name": case_name, "width": width, "height": height, "threshold": threshold,
                          "input_mem": input_mem, "outputs": outputs,
                          "npz": npz.relative_to(output).as_posix(), "input_png": png.relative_to(output).as_posix()})
    manifest = {"schema_version": 1, "seed": SEED, "packing": "UINT32:0x00RRGGBB; row major",
                "contract": {"gray": "(77R+150G+29B+128)>>8", "threshold": "Y>=T",
                             "sobel": "min(255,abs(Gx)+abs(Gy)); black 1-pixel border"},
                "cases": cases, "files": files, "physical_board_verified": False}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "output" / "test_vectors")
    args = parser.parse_args()
    result = generate(args.output)
    print("Created %d cases, %d mode comparisons in %s" % (len(result["cases"]), 4 * len(result["cases"]), args.output))
