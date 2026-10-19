"""Create whitespace-free relative paths for the xsim vector testbench."""
from pathlib import Path
import json
import os

root = Path(__file__).resolve().parents[1]
work = root / "output" / "rtl_validation" / "xsim"
work.mkdir(parents=True, exist_ok=True)
vector_root = root / "output" / "test_vectors"
manifest = json.loads((vector_root / "manifest.json").read_text(encoding="utf-8"))
rows = []
for case in manifest["cases"]:
    for mode in range(4):
        input_path = os.path.relpath(vector_root / case["input_mem"], work).replace("\\", "/")
        output_path = os.path.relpath(vector_root / case["outputs"][str(mode)], work).replace("\\", "/")
        rows.append(f'{case["name"]} {case["width"]} {case["height"]} {mode} {case["threshold"]} {input_path} {output_path}')
(work / "cases.txt").write_text("\n".join(rows) + "\n", encoding="ascii")
print(f"Prepared {len(rows)} case/mode comparisons in {work}")
