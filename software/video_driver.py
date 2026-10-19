"""Bounded PYNQ AXI DMA driver. Never substitutes a CPU image for FPGA output."""
import hashlib
import json
import math
from pathlib import Path
import threading
import time
import numpy as np
from reference_filters import Mode, pack_pixels, unpack_pixels, validate_image


ID = 0x56494431
VERSION = 0x00010000
CLOCK_HZ = 100000000
REG = {"ID":0x00,"VERSION":0x04,"CONTROL":0x08,"STATUS":0x0c,"WIDTH":0x10,
       "HEIGHT":0x14,"MODE":0x18,"THRESHOLD":0x1c,"CYCLES":0x20,"PIX_IN":0x24,
       "PIX_OUT":0x28,"ERROR":0x2c,"CLOCK":0x30}
ERROR_FLAGS = {0:"invalid dimensions",1:"invalid mode",2:"early input TLAST",
               3:"missing input TLAST",4:"invalid TKEEP",5:"nonzero reserved byte",
               6:"START during BUSY"}
DMA_ERRORS = 0x770  # DMA internal/slave/decode and SG internal/slave/decode bits.


class VideoHardwareError(RuntimeError):
    pass


class _BoundedDMAChannel:
    """Simple-mode registers from AXI DMA PG021; every polling path has a deadline."""
    def __init__(self,owner,offset):
        self.owner=owner
        self.offset=offset
        self.buffer=None
        self.transferred=0
        self._first=True

    def _poll(self,predicate,action):
        deadline=time.perf_counter()+self.owner.timeout
        while not predicate():
            if time.perf_counter()>=deadline:
                raise VideoHardwareError("DMA %s timeout." % action)
            time.sleep(.00005)

    def start(self):
        self.owner.mmio.write(self.offset,1)
        self._poll(lambda:not self.owner.mmio.read(self.offset+4)&1,"start")
        self._first=True
        self.buffer=None

    def stop(self):
        self.owner.mmio.write(self.offset,0)
        self._poll(lambda:bool(self.owner.mmio.read(self.offset+4)&1),"stop")

    def transfer(self,buffer):
        status=int(self.owner.mmio.read(self.offset+4))
        if status&1 or status&DMA_ERRORS: raise VideoHardwareError("DMA channel not running or in error.")
        if not status&2 and not self._first: raise VideoHardwareError("DMA channel is busy.")
        address=int(buffer.physical_address)
        nbytes=int(buffer.nbytes)
        if not 0<nbytes<=self.owner.max_bytes: raise ValueError("DMA transfer exceeds configured byte limit.")
        if address%self.owner.alignment: raise ValueError("DMA buffer address is not aligned.")
        if self.offset==0: buffer.flush()
        self.owner.mmio.write(self.offset+0x18,address&0xffffffff)
        self.owner.mmio.write(self.offset+0x1c,(address>>32)&0xffffffff)
        self.owner.mmio.write(self.offset+0x28,nbytes)
        self.buffer=buffer
        self.transferred=0
        self._first=False

    def wait(self):
        def completed():
            status=int(self.owner.mmio.read(self.offset+4))
            if status&DMA_ERRORS or status&1:
                raise VideoHardwareError("DMA channel error while waiting: 0x%08x." % status)
            return bool(status&2)
        self._poll(completed,"completion")
        self.transferred=int(self.owner.mmio.read(self.offset+0x28))
        if self.buffer is not None and self.transferred!=self.buffer.nbytes:
            raise VideoHardwareError("DMA completed an unexpected byte count.")
        if self.offset==0x30 and self.buffer is not None: self.buffer.invalidate()
        self.buffer=None


class BoundedSimpleDMA:
    """No interrupt or SG support; the generated overlay uses simple DMA with polling."""
    def __init__(self,mmio,timeout=5.0,length_bits=23,alignment=8):
        self.mmio=mmio
        self.timeout=float(timeout)
        if not math.isfinite(self.timeout) or self.timeout<=0:
            raise ValueError("timeout must be finite and positive")
        self.max_bytes=(1<<int(length_bits))-1
        self.alignment=int(alignment)
        self.sendchannel=_BoundedDMAChannel(self,0)
        self.recvchannel=_BoundedDMAChannel(self,0x30)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_overlay_files(root, manifest_name="overlay_manifest.json"):
    root = Path(root)
    manifest_path = (root / manifest_name).resolve()
    if manifest_path.parent != root.resolve():
        raise VideoHardwareError("Manifest must be in the overlay directory.")
    if not manifest_path.is_file():
        raise VideoHardwareError("Missing overlay_manifest.json; upload the complete verified bundle.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = {}
    for kind in ("bit", "hwh"):
        filename = manifest.get(kind + "_file", "video_dma." + kind)
        path = (root / filename).resolve()
        if path.parent != root.resolve() or path.suffix != "." + kind:
            raise VideoHardwareError("Overlay file must be in overlays/ with expected extension.")
        expected = manifest.get(kind + "_sha256")
        if not path.is_file() or not isinstance(expected,str) or file_sha256(path) != expected:
            raise VideoHardwareError("%s fingerprint mismatch; do not mix different builds." % kind)
        paths[kind] = path
    if manifest.get("clock_hz") != CLOCK_HZ or manifest.get("register_map_version") != VERSION:
        raise VideoHardwareError("Incompatible clock/register contract in overlay manifest.")
    if manifest.get("ip_name", "video_filter_0") != "video_filter_0" or manifest.get("dma_name", "axi_dma_0") != "axi_dma_0":
        raise VideoHardwareError("Unexpected block names in overlay manifest.")
    if paths["bit"].stem != paths["hwh"].stem:
        raise VideoHardwareError("PYNQ requires matching bit/hwh basenames.")
    return paths["bit"], manifest


class VideoAccelerator:
    """Own the overlay and two reusable contiguous buffers; use as a context manager.

    CYCLES is active wall-clock time in PL (includes stalls and drain), not only MAC time.
    process() measures prepare/transfer/reconstruct separately; display is never benchmarked here.
    """
    def __init__(self, overlay, allocate_fn, fingerprint=None, timeout=5.0, clock_mhz=None, dma=None):
        self.overlay = overlay
        self.dma = dma if dma is not None else getattr(overlay, "axi_dma_0")
        self.mmio = getattr(overlay, "video_filter_0").mmio
        self._allocate = allocate_fn
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0: raise ValueError("timeout must be finite and positive")
        self.fingerprint = fingerprint or {}
        self.clock_mhz = clock_mhz
        self._buffers = None
        self._buffer_shape = None
        self._closed = False
        self._unsafe_dma = True
        self._lock = threading.Lock()
        self.last_metrics = None
        self.physical_backend = False  # Only from_overlay/load sets this after real PYNQ MMIO creation.
        if self.mmio.read(REG["ID"]) != ID or self.mmio.read(REG["VERSION"]) != VERSION:
            raise VideoHardwareError("Wrong IP ID/version. The video overlay is not loaded.")
        if self.mmio.read(REG["CLOCK"]) != CLOCK_HZ:
            raise VideoHardwareError("Wrong RTL clock contract.")
        if clock_mhz is not None and abs(float(clock_mhz) - 100.0) > 0.1:
            raise VideoHardwareError("Measured FCLK0 %.6f MHz differs from 100 MHz." % float(clock_mhz))
        self.reset()

    @classmethod
    def load(cls, overlay_dir="overlays", timeout=5.0, manifest_name="overlay_manifest.json"):
        bit_path, manifest = validate_overlay_files(overlay_dir,manifest_name)
        # Import only on the board: host golden tests need neither PYNQ nor root privileges.
        try:
            from pynq import Overlay
        except ImportError as exc:
            raise VideoHardwareError("Run this notebook on PYNQ-Z2 Jupyter, not laptop Python.") from exc
        overlay = Overlay(str(bit_path), download=True)
        return cls.from_overlay(overlay,manifest,timeout)

    @classmethod
    def from_overlay(cls,overlay,fingerprint=None,timeout=5.0):
        """Attach to an already-loaded HDMI+DMA overlay without programming it again."""
        from pynq import MMIO,allocate
        from pynq.ps import Clocks
        missing = {"video_filter_0", "axi_dma_0"} - set(overlay.ip_dict)
        if missing: raise VideoHardwareError("HWH lacks required blocks: %s" % sorted(missing))
        dma_params = overlay.ip_dict["axi_dma_0"].get("parameters", {})
        if str(dma_params.get("c_include_sg", "0")) not in ("0", "False", "false"):
            raise VideoHardwareError("Driver requires AXI DMA simple mode.")
        length_bits = dma_params.get("c_sg_length_width", dma_params.get("C_SG_LENGTH_WIDTH", 23))
        if int(length_bits) < 22:
            raise VideoHardwareError("DMA transfer limit is too small for VGA and 720p frames.")
        dma_info=overlay.ip_dict["axi_dma_0"]
        memory_width=int(dma_params.get("c_m_axi_mm2s_data_width",64))
        memory_width=max(memory_width,int(dma_params.get("c_m_axi_s2mm_data_width",64)))
        dma=BoundedSimpleDMA(MMIO(dma_info["phys_addr"],dma_info["addr_range"]),timeout,
                             int(length_bits),memory_width//8)
        result=cls(overlay,allocate,fingerprint,timeout,Clocks.fclk0_mhz,dma=dma)
        result.physical_backend=True
        return result

    def _wait_reset(self, offset):
        deadline = time.perf_counter() + self.timeout
        while self.dma.mmio.read(offset) & 4:
            if time.perf_counter() >= deadline:
                raise VideoHardwareError("DMA reset did not complete before timeout.")
            time.sleep(0.0001)

    def reset(self):
        """Abort both transfers, reset both DMA directions and the kernel; safe before re-use."""
        if self._closed: raise VideoHardwareError("Accelerator is closed.")
        # Until the entire reset/start sequence succeeds, DMA ownership of buffers is unknown.
        self._unsafe_dma = True
        self.mmio.write(REG["CONTROL"],2)
        self.dma.mmio.write(0x00,4)
        self._wait_reset(0x00)
        self._wait_reset(0x30)
        # Resetting either DMA channel resets the whole DMA. Public start restores driver state.
        self.dma.sendchannel.start()
        self.dma.recvchannel.start()
        status = self.mmio.read(REG["STATUS"])
        if status & 5:
            raise VideoHardwareError("Kernel reset failed: status=0x%08x." % status)
        self._unsafe_dma = False

    def _preflight(self):
        if self._unsafe_dma:
            raise VideoHardwareError("DMA ownership is unsafe after a failed reset. Call reset() successfully or reboot; buffers remain allocated.")
        status=int(self.mmio.read(REG["STATUS"]))
        if status&5:
            raise VideoHardwareError("Kernel is busy/in error; reset before starting a frame.")
        for offset,channel in ((0,self.dma.sendchannel),(0x30,self.dma.recvchannel)):
            dma_status=int(self.dma.mmio.read(offset+4))
            first=bool(getattr(channel,"_first",False))
            if dma_status&DMA_ERRORS or dma_status&1 or (not dma_status&2 and not first):
                raise VideoHardwareError("DMA is not quiescent before buffer preparation: 0x%08x." % dma_status)

    def _release_buffers(self):
        if self._buffers:
            for buffer in self._buffers:
                buffer.freebuffer()
        self._buffers = None
        self._buffer_shape = None

    def _ensure_buffers(self,width,height):
        shape = (height,width)
        if self._buffer_shape == shape: return
        self._release_buffers()
        source = self._allocate(shape=(width*height,),dtype=np.uint32)
        try:
            target = self._allocate(shape=(width*height,),dtype=np.uint32)
        except Exception:
            source.freebuffer()
            raise
        self._buffers = (source,target)
        self._buffer_shape = shape

    def _wait_complete(self):
        deadline = time.perf_counter() + self.timeout
        while True:
            status = int(self.mmio.read(REG["STATUS"]))
            if status & 4:
                flags = int(self.mmio.read(REG["ERROR"]))
                text = ", ".join(name for bit,name in ERROR_FLAGS.items() if flags & (1<<bit))
                raise VideoHardwareError("Kernel error 0x%08x (%s)." % (flags,text))
            tx = int(self.dma.mmio.read(0x04))
            rx = int(self.dma.mmio.read(0x34))
            if (tx | rx) & DMA_ERRORS:
                raise VideoHardwareError("DMA error: MM2S=0x%08x S2MM=0x%08x." % (tx,rx))
            if (tx | rx) & 1:
                raise VideoHardwareError("DMA halted unexpectedly: MM2S=0x%08x S2MM=0x%08x." % (tx,rx))
            if status & 2 and not status & 1 and tx & 2 and rx & 2:
                # PYNQ wait() updates channel state and buffer caches. Both are known idle now.
                self.dma.sendchannel.wait()
                self.dma.recvchannel.wait()
                return
            if time.perf_counter() >= deadline:
                raise VideoHardwareError("Frame timeout: core=0x%08x MM2S=0x%08x S2MM=0x%08x." % (status,tx,rx))
            time.sleep(0.00005)

    def process(self, image, mode=0, threshold=128):
        image = validate_image(image)
        height,width = image.shape[:2]
        mode = int(Mode(mode))
        if width < 3 or width > 1280 or height < 3 or height > 720:
            raise ValueError("Hardware supports width 3..1280, height 3..720.")
        if not isinstance(threshold,(int,np.integer)) or not 0<=int(threshold)<=255:
            raise ValueError("Threshold must be 0..255 integer.")
        with self._lock:
            if self._closed: raise VideoHardwareError("Accelerator is closed.")
            self.last_metrics = None
            # Prove that old buffers are not owned by DMA BEFORE writing, freeing or resizing them.
            self._preflight()
            started = time.perf_counter()
            self._ensure_buffers(width,height)
            source,target = self._buffers
            source[:] = pack_pixels(image)
            source.flush()
            # Output sentinel catches missing writes if DMA/kernel incorrectly signal completion.
            target[:] = np.uint32(0xFFDEADBE)
            target.flush()
            prepared = time.perf_counter()
            try:
                for name,value in (("WIDTH",width),("HEIGHT",height),("MODE",mode),("THRESHOLD",int(threshold))):
                    self.mmio.write(REG[name],value)
                transfer_started = time.perf_counter()
                # Receiver is armed first. START clears prior done/counters; sender is last.
                self.dma.recvchannel.transfer(target)
                self.mmio.write(REG["CONTROL"],1)
                self.dma.sendchannel.transfer(source)
                self._wait_complete()
                transfer_finished = time.perf_counter()
                target.invalidate()
                count_in = int(self.mmio.read(REG["PIX_IN"]))
                count_out = int(self.mmio.read(REG["PIX_OUT"]))
                cycles = int(self.mmio.read(REG["CYCLES"]))
                if count_in != width*height or count_out != width*height or cycles <= 0:
                    raise VideoHardwareError("Invalid counters: input=%d output=%d cycles=%d expected=%d." %
                                             (count_in,count_out,cycles,width*height))
                output = unpack_pixels(np.asarray(target,dtype=np.uint32).copy(),width,height)
                finished = time.perf_counter()
                self.last_metrics = {"backend":"FPGA AXI DMA", "width":width,"height":height,"mode":mode,
                    "threshold":int(threshold),"pixels_in":count_in,"pixels_out":count_out,
                    "core_active_cycles":cycles,"core_active_ms":cycles/CLOCK_HZ*1000.0,
                    "core_cycles_include_stream_stalls":True,"clock_hz_contract":CLOCK_HZ,
                    "fclk0_mhz_measured":self.clock_mhz,"prepare_ms":(prepared-started)*1000,
                    "dma_and_control_ms":(transfer_finished-transfer_started)*1000,
                    "reconstruct_ms":(finished-transfer_finished)*1000,"total_driver_ms":(finished-started)*1000}
                return output
            except Exception as exc:
                # DMA cannot access buffers after recovery reset succeeds. Keep them allocated if it fails.
                try:
                    self.reset()
                except Exception as recovery:
                    raise VideoHardwareError("%s; recovery reset also failed: %s. Reboot board before freeing buffers." % (exc,recovery)) from exc
                raise

    def close(self):
        with self._lock:
            if self._closed: return
            self.reset()
            self._unsafe_dma = True
            self.dma.sendchannel.stop()
            self.dma.recvchannel.stop()
            self._release_buffers()
            self._closed = True

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
