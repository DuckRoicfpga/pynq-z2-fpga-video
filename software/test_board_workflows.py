"""Board workflow lifecycle regression tests with mocks, not board evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from board_workflows import run_dma_verification
from reference_filters import apply_filter


class WorkflowTests(unittest.TestCase):
    def exercise(self, fail_cleanup):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_path = root / "results" / "board_dma_results.json"
            events = []
            vectors = root / "test_vectors"
            vectors.mkdir()
            (vectors / "manifest.json").write_text("{}")
            image = np.arange(6 * 8 * 3, dtype=np.uint8).reshape(6, 8, 3)
            arrays = {"input": image, **{"mode%d" % mode: apply_filter(image, mode) for mode in range(4)}}
            cases = [{"name": "640x480_random" if index == 0 else "mock%d" % index,
                      "width": 8, "height": 6, "threshold": 128} for index in range(23)]

            class Driver:
                physical_backend = True  # This is a test double; all outputs stay in tmp.
                fingerprint = {"mock": True}
                last_metrics = {"mock": True}
                def __enter__(self):
                    events.append("enter")
                    return self
                def process(self, image, mode, threshold=128):
                    if "pixels" not in events: events.append("pixels")
                    return apply_filter(image, mode, threshold)
                def reset(self): pass
                def __exit__(self, *args):
                    events.append("cleanup")
                    pending = json.loads(result_path.read_text())
                    # Even though pixel checks passed, physical PASS is withheld.
                    self.assert_pending = pending["status"] == "PIXELS_PASS_PENDING_CLEANUP" and not pending["physical_board_verified"]
                    if fail_cleanup:
                        raise RuntimeError("DMA stop timeout")

            driver = Driver()
            with patch("board_workflows.VideoAccelerator.load", return_value=driver), \
                    patch("board_workflows.collect_inventory", return_value={"mock": True}), \
                    patch("board_workflows.load_vector_manifest", return_value={"cases": cases}), \
                    patch("board_workflows.load_case", return_value=arrays), \
                    patch("board_workflows.benchmark", side_effect=lambda driver, image, mode: {"mode": mode}):
                if fail_cleanup:
                    with self.assertRaisesRegex(RuntimeError, "stop timeout"):
                        run_dma_verification(root, progress=lambda text: None)
                else:
                    report = run_dma_verification(root, progress=lambda text: None)
                    self.assertEqual(report["status"], "PASS")
            stored = json.loads(result_path.read_text())
            self.assertTrue(driver.assert_pending)
            self.assertEqual(events, ["enter", "pixels", "cleanup"])
            self.assertEqual(stored["status"], "FAIL" if fail_cleanup else "PASS")
            self.assertEqual(stored["physical_board_verified"], not fail_cleanup)
            self.assertEqual(stored["dma_cleanup_verified"], not fail_cleanup)
            self.assertEqual(len(stored["pixel_tests"]), 92)
            if fail_cleanup:
                self.assertIn("DMA stop timeout", stored["cleanup_error"])

    def test_pixel_success_commits_physical_pass_only_after_cleanup(self):
        self.exercise(False)

    def test_cleanup_failure_keeps_pixel_evidence_but_reports_fail(self):
        self.exercise(True)


if __name__ == "__main__": unittest.main()
