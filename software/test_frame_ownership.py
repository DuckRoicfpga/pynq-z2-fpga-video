import unittest
from types import SimpleNamespace
from frame_ownership import release_capture_frame


class CachedFrame:
    def __init__(self, capacity=5):
        self.pointer = object()
        self.return_to = SimpleNamespace(_cache=[], capacity=capacity)
        self.freed = False
        self.unmaps = 0
        self.releases = 0

    def freebuffer(self):
        self.releases += 1
        if self.pointer:
            if len(self.return_to._cache) < self.return_to.capacity:
                self.return_to._cache.append(self.pointer)
            self.pointer = 0
        elif not self.freed:
            self.freed = True
            self.unmaps += 1


class OwnershipTests(unittest.TestCase):
    def test_destructor_cannot_unmap_a_pointer_returned_to_cache(self):
        frame = CachedFrame()
        pointer = frame.pointer
        release_capture_frame(frame)
        self.assertIs(frame.return_to._cache[0], pointer)
        # Model the installed PynqBuffer.__del__ after another frame reuses it.
        frame.return_to._cache.pop()
        frame.freebuffer()
        self.assertEqual(frame.unmaps, 0)

    def test_releasing_same_frame_twice_does_not_return_pointer_twice(self):
        frame = CachedFrame()
        release_capture_frame(frame)
        release_capture_frame(frame)
        self.assertEqual(frame.releases, 1)
        self.assertEqual(len(frame.return_to._cache), 1)

    def test_full_cache_keeps_final_disposal_instead_of_leaking_mapping(self):
        frame = CachedFrame(capacity=0)
        release_capture_frame(frame)
        self.assertFalse(frame.freed)
        frame.freebuffer()
        self.assertEqual(frame.unmaps, 1)

    def test_regular_buffer_is_freed_once(self):
        calls=[]
        frame=SimpleNamespace(freebuffer=lambda: calls.append('free'))
        release_capture_frame(frame)
        release_capture_frame(frame)
        self.assertEqual(calls,['free'])


if __name__ == '__main__':
    unittest.main()
