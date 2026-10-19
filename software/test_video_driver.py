"""Host driver tests: mocks test transport/error handling, never claim a physical FPGA PASS."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import numpy as np
from video_driver import VideoAccelerator,VideoHardwareError,BoundedSimpleDMA,REG,ID,VERSION,CLOCK_HZ,validate_overlay_files


class Buffer(np.ndarray):
    def __new__(cls,shape,dtype):
        obj = np.zeros(shape,dtype).view(cls)
        obj.events = []
        return obj
    def flush(self): self.events.append("flush")
    def invalidate(self): self.events.append("invalidate")
    def freebuffer(self): self.events.append("free")


class FakeMMIO:
    def __init__(self,callback=None): self.regs={};self.callback=callback
    def read(self,address): return self.regs.get(address,0)
    def write(self,address,value):
        self.regs[address]=value
        if self.callback: self.callback(address,value)


class FakeDMA:
    def __init__(self,core,events):
        self.core=core
        self.events=events
        self.mmio=FakeMMIO(self.write)
        self.mode="success"
        self.recvchannel=FakeChannel(self,"recv")
        self.sendchannel=FakeChannel(self,"send")
    def write(self,address,value):
        if address==0 and value==4:
            self.mmio.regs[0]=0;self.mmio.regs[0x30]=0
            self.mmio.regs[4]=2;self.mmio.regs[0x34]=2
    def finish(self,source):
        if self.mode=="timeout": return
        self.recvchannel.buffer[:] = source
        count=len(source)
        self.core.regs[REG["PIX_IN"]]=count
        self.core.regs[REG["PIX_OUT"]]=count + (1 if self.mode=="count" else 0)
        self.core.regs[REG["CYCLES"]]=count+13
        self.core.regs[REG["STATUS"]]=2
        self.mmio.regs[4]=2;self.mmio.regs[0x34]=2
        if self.mode=="kernel_error":
            self.core.regs[REG["STATUS"]]=4;self.core.regs[REG["ERROR"]]=8
        elif self.mode=="dma_error": self.mmio.regs[0x34]=0x20
        elif self.mode=="reserved": self.recvchannel.buffer[-1]=np.uint32(0xff000000)
        elif self.mode=="halted": self.mmio.regs[4]=1


class FakeChannel:
    def __init__(self,dma,name): self.dma=dma;self.name=name;self.buffer=None
    def start(self):
        self.dma.mmio.regs[4 if self.name=="send" else 0x34]=2
        self.dma.events.append(self.name+".start")
    def stop(self): self.dma.events.append(self.name+".stop")
    def wait(self): self.dma.events.append(self.name+".wait")
    def transfer(self,buffer):
        self.buffer=buffer
        self.dma.events.append(self.name+".transfer")
        self.dma.mmio.regs[4 if self.name=="send" else 0x34]=0
        if self.name=="send": self.dma.finish(buffer)


def make_driver(timeout=.02,clock=100.):
    events=[]
    def core_write(address,value):
        if address==REG["CONTROL"]:
            if value==2: core.regs[REG["STATUS"]]=0;core.regs[REG["ERROR"]]=0
            if value==1: core.regs[REG["STATUS"]]=1;events.append("core.start")
    core=FakeMMIO(core_write)
    core.regs.update({REG["ID"]:ID,REG["VERSION"]:VERSION,REG["CLOCK"]:CLOCK_HZ})
    dma=FakeDMA(core,events)
    overlay=SimpleNamespace(axi_dma_0=dma,video_filter_0=SimpleNamespace(mmio=core))
    return VideoAccelerator(overlay,Buffer,timeout=timeout,clock_mhz=clock),events


class DriverTests(unittest.TestCase):
    def setUp(self): self.image=np.arange(6*8*3,dtype=np.uint8).reshape(6,8,3)
    def test_receiver_first_register_config_cache_counter_and_cleanup(self):
        driver,events=make_driver()
        out=driver.process(self.image,0,73)
        np.testing.assert_array_equal(out,self.image)
        self.assertLess(events.index("recv.transfer"),events.index("core.start"))
        self.assertLess(events.index("core.start"),events.index("send.transfer"))
        self.assertEqual(driver.mmio.read(REG["WIDTH"]),8)
        self.assertEqual(driver.mmio.read(REG["HEIGHT"]),6)
        self.assertEqual(driver.mmio.read(REG["THRESHOLD"]),73)
        self.assertEqual(driver.last_metrics["pixels_out"],48)
        self.assertGreater(driver.last_metrics["core_active_ms"],0)
        source,target=driver._buffers
        self.assertIn("flush",source.events);self.assertIn("invalidate",target.events)
        driver.close();driver.close()
        self.assertEqual(source.events.count("free"),1);self.assertEqual(target.events.count("free"),1)
        with self.assertRaises(VideoHardwareError): driver.process(self.image)

    def test_reuse_and_resize_frees_previous_buffers(self):
        driver,_=make_driver()
        driver.process(self.image)
        buffers=driver._buffers
        driver.process(self.image)
        self.assertIs(driver._buffers,buffers)
        driver.process(np.zeros((12,16,3),np.uint8))
        self.assertIn("free",buffers[0].events)
        driver.close()

    def test_invalid_data_rejected_before_dma(self):
        driver,events=make_driver()
        before=len(events)
        for image,mode,t in ((np.zeros((2,8,3),np.uint8),0,128),(self.image,4,128),(self.image,2,256),
                             (self.image.astype(np.float32),0,128)):
            with self.assertRaises(ValueError): driver.process(image,mode,t)
        self.assertEqual(len(events),before)
        driver.close()

    def test_timeout_recovers_no_cpu_fallback(self):
        driver,_=make_driver()
        driver.dma.mode="timeout"
        with self.assertRaisesRegex(VideoHardwareError,"timeout"): driver.process(self.image)
        self.assertIsNone(driver.last_metrics)
        self.assertEqual(driver.mmio.read(REG["STATUS"]),0)
        driver.dma.mode="success"
        np.testing.assert_array_equal(driver.process(self.image),self.image)
        driver.close()

    def test_transport_errors_fail_and_recover(self):
        for fault in ("kernel_error","dma_error","count","halted","reserved"):
            driver,_=make_driver()
            driver.dma.mode=fault
            with self.assertRaises((VideoHardwareError,ValueError)): driver.process(self.image)
            self.assertIsNone(driver.last_metrics)
            self.assertEqual(driver.mmio.read(REG["STATUS"]),0)
            driver.close()

    def test_identity_and_clock_checked(self):
        with self.assertRaisesRegex(VideoHardwareError,"FCLK0"): make_driver(clock=99.)
        driver,_=make_driver()
        driver.mmio.regs[REG["ID"]]=0
        with self.assertRaisesRegex(VideoHardwareError,"ID/version"):
            VideoAccelerator(driver.overlay,Buffer)
        driver.close()

    def test_overlay_fingerprints_and_matching_stems(self):
        with tempfile.TemporaryDirectory(dir=str(Path(__file__).parent)) as temp:
            root=Path(temp)
            (root/"video_dma.bit").write_bytes(b"test-bit")
            (root/"video_dma.hwh").write_bytes(b"test-hwh")
            manifest={"clock_hz":CLOCK_HZ,"register_map_version":VERSION,
                      "bit_sha256":hashlib.sha256(b"test-bit").hexdigest(),
                      "hwh_sha256":hashlib.sha256(b"test-hwh").hexdigest()}
            (root/"overlay_manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
            self.assertEqual(validate_overlay_files(root)[0],root/"video_dma.bit")
            (root/"video_dma.bit").write_bytes(b"wrong-bit")
            with self.assertRaisesRegex(VideoHardwareError,"fingerprint"): validate_overlay_files(root)
            manifest["bit_file"]="../escape.bit"
            (root/"overlay_manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
            with self.assertRaisesRegex(VideoHardwareError,"extension"): validate_overlay_files(root)

    def test_bounded_channel_registers_and_completion_byte_count(self):
        io=FakeMMIO()
        def written(offset,value):
            if offset in (0,0x30): io.regs[offset+4]=2 if value&1 else 1
            if offset in (0x28,0x58): io.regs[offset-0x24]=2
        io.callback=written
        dma=BoundedSimpleDMA(io,timeout=.01)
        source=Buffer((48,),np.uint32);source.physical_address=0x10000000
        target=Buffer((48,),np.uint32);target.physical_address=0x10010000
        dma.recvchannel.start();dma.sendchannel.start()
        dma.recvchannel.transfer(target);dma.sendchannel.transfer(source)
        self.assertEqual(io.read(0x18),0x10000000)
        self.assertEqual(io.read(0x48),0x10010000)
        self.assertEqual(io.read(0x58),192)
        dma.recvchannel.wait();dma.sendchannel.wait()
        self.assertIn("invalidate",target.events)
        self.assertEqual(dma.recvchannel.transferred,192)
        dma.recvchannel.stop();dma.sendchannel.stop()

    def test_bounded_channel_start_and_stop_timeout(self):
        io=FakeMMIO()
        dma=BoundedSimpleDMA(io,timeout=.005)
        io.regs[4]=1
        with self.assertRaisesRegex(VideoHardwareError,"start timeout"): dma.sendchannel.start()
        io.regs[4]=0
        with self.assertRaisesRegex(VideoHardwareError,"stop timeout"): dma.sendchannel.stop()
        with self.assertRaisesRegex(VideoHardwareError,"completion timeout"): dma.sendchannel.wait()

    def test_bounded_channel_bad_size_address_and_short_receive(self):
        io=FakeMMIO();io.regs[4]=2;io.regs[0x34]=2
        dma=BoundedSimpleDMA(io,length_bits=8)
        source=Buffer((48,),np.uint32);source.physical_address=0x10000002
        with self.assertRaisesRegex(ValueError,"aligned"): dma.sendchannel.transfer(source)
        source=Buffer((100,),np.uint32);source.physical_address=0x10000000
        with self.assertRaisesRegex(ValueError,"byte limit"): dma.sendchannel.transfer(source)
        source=Buffer((48,),np.uint32);source.physical_address=0x10000000
        dma.recvchannel.transfer(source)
        io.regs[0x58]=188
        with self.assertRaisesRegex(VideoHardwareError,"byte count"): dma.recvchannel.wait()

    def test_failed_recovery_preserves_live_buffers_until_explicit_reset(self):
        driver,_=make_driver(timeout=.005)
        driver.process(self.image)
        source,target=driver._buffers
        driver.dma.mode="timeout"
        old_callback=driver.dma.mmio.callback
        def stuck_reset(address,value):
            if address==0 and value==4: driver.dma.mmio.regs[0]=4
            else: old_callback(address,value)
        driver.dma.mmio.callback=stuck_reset
        with self.assertRaisesRegex(VideoHardwareError,"recovery reset also failed"): driver.process(self.image)
        before=source.copy()
        for image in (self.image+1,np.zeros((12,16,3),np.uint8)):
            with self.assertRaisesRegex(VideoHardwareError,"ownership is unsafe"): driver.process(image)
            self.assertNotIn("free",source.events)
            self.assertNotIn("free",target.events)
            np.testing.assert_array_equal(source,before)
        with self.assertRaises(VideoHardwareError): driver.close()
        self.assertNotIn("free",source.events)
        driver.dma.mmio.callback=old_callback
        driver.dma.mode="success"
        driver.reset()
        driver.process(np.zeros((12,16,3),np.uint8))
        self.assertIn("free",source.events)
        driver.close()

    def test_timeout_must_be_finite_positive(self):
        for timeout in (0,-1,float("nan"),float("inf")):
            with self.assertRaises(ValueError): make_driver(timeout=timeout)
            with self.assertRaises(ValueError): BoundedSimpleDMA(FakeMMIO(),timeout=timeout)


if __name__=="__main__": unittest.main()
