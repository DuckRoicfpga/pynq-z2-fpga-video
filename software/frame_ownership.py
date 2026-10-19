"""Project-local ownership guard for cached PYNQ video frames.

PYNQ's installed PynqBuffer.freebuffer returns a cached pointer and sets it
to zero, but leaves freed=False. Its later __del__ calls freebuffer again,
which can munmap the same mapping now owned by a different cached frame.
Mark only mappings actually retained by the cache as already released by
this wrapper. Ordinary buffers and a full cache keep their normal disposal.
No global PYNQ library monkeypatch is installed.
"""


def release_capture_frame(frame):
    if getattr(frame, "_codex_frame_released", False):
        return
    cache = getattr(frame, "return_to", None)
    pointer = getattr(frame, "pointer", None)
    entries = getattr(cache, "_cache", None)
    if cache is not None and pointer and not isinstance(entries, list):
        raise RuntimeError("Unsupported PYNQ video frame cache; cannot release mapping safely")
    frame.freebuffer()
    if pointer and cache is not None and any(item is pointer for item in entries):
        # The mapping belongs to the cache/new frame, never to this object's
        # destructor. Preserve the cached BO; prevent its second munmap.
        frame.freed = True
    frame._codex_frame_released = True
