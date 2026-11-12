#!/usr/bin/env python3
"""Finite, paced HEVC AU replay. Linux only; see README.zh.md."""
import argparse
import collections
import csv
import hashlib
import json
import math
import os
import pathlib
import platform
import re
import signal
import subprocess
import sys
import threading
import time

CASES = {'1x4K60': (1,3840,2160,60), '2x4K60': (2,3840,2160,60), '4x4K30': (4,3840,2160,30),
         '4x4K60': (4,3840,2160,60), '8x1080p30': (8,1920,1080,30)}
BAD = re.compile(r'EXT4-fs (?:error|warning)|I/O error|Buffer I/O|mmc.*(?:error|timeout)|journal.*abort|Remounting filesystem read-only', re.I)

def command(args, timeout=30):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout)

def atomic(path, obj):
    p = pathlib.Path(str(path)+'.tmp')
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    p.replace(path)

def kernel():
    # Fail closed if the operator cannot inspect the kernel log.
    return command(['dmesg','--color=never'])

def health():
    k = kernel()
    if BAD.search(k):
        raise RuntimeError('Kernel storage error history detected. Stop, repair offline and reboot; see README.')
    return k

def gst_init():
    import gi
    gi.require_version('Gst','1.0')
    gi.require_version('GstApp','1.0')
    from gi.repository import Gst, GstApp
    Gst.init(None)
    return Gst

def validate_source(a, w, h, fps, Gst):
    p = pathlib.Path(a.source).resolve()
    if not p.is_file() or p.stat().st_size > a.max_source_mb*1024**2:
        raise RuntimeError('Source absent or exceeds compressed cache limit')
    probe = json.loads(command(['ffprobe','-v','error','-select_streams','v:0',
        '-show_streams','-show_frames','-show_packets','-of','json',str(p)],120))
    s = probe['streams'][0]
    if (s['codec_name'],s['width'],s['height']) != ('hevc',w,h):
        raise RuntimeError('Codec/resolution mismatch')
    # show_frames + show_packets uses packets_and_frames in ffprobe JSON.
    mixed = probe.get('packets_and_frames',[])
    frames = probe.get('frames',[x for x in mixed if x.get('type')=='frame'])
    packets = probe.get('packets',[x for x in mixed if x.get('type')=='packet'])
    if not frames or len(frames)!=len(packets):
        raise RuntimeError('Need exactly one display frame per packet')
    if frames[0].get('key_frame') != 1 or any(x.get('pict_type')=='B' for x in frames):
        raise RuntimeError('Need closed GOP starting at IDR, no B frames; use prepare.sh')
    for i, (f, pkt) in enumerate(zip(frames,packets)):
        t = float(f['best_effort_timestamp_time'])
        if abs(t-i/fps)>0.0001 or abs(float(pkt['pts_time'])-float(pkt['dts_time']))>0.0001:
            raise RuntimeError('Need zero-origin CFR timestamps and PTS=DTS; use prepare.sh')
    # Demux/parser counts are checked independently against ffprobe.
    pipe = Gst.parse_launch('filesrc name=file ! qtdemux ! h265parse ! video/x-h265,stream-format=byte-stream,alignment=au ! appsink name=collect sync=false max-buffers=8 drop=false')
    pipe.get_by_name('file').set_property('location',str(p))
    sink = pipe.get_by_name('collect')
    chunks=[]
    started=time.monotonic()
    pipe.set_state(Gst.State.PLAYING)
    try:
        while time.monotonic()-started<120:
            sample=sink.emit('try-pull-sample',100*Gst.MSECOND)
            if sample:
                b=sample.get_buffer()
                chunks.append(b.extract_dup(0,b.get_size()))
                if sum(map(len,chunks))>a.max_source_mb*1024**2:
                    raise RuntimeError('Parsed compressed cache too large')
            msg=pipe.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if msg: raise RuntimeError(str(msg.parse_error()))
            if sink.is_eos(): break
        else: raise RuntimeError('Source parser timeout')
    finally: pipe.set_state(Gst.State.NULL)
    if len(chunks)!=len(frames): raise RuntimeError('Parser AU count != ffprobe frame count')
    nals=re.split(b'\x00\x00\x00?\x01',chunks[0])
    if not any(x and ((x[0]>>1)&63) in (19,20) for x in nals):
        raise RuntimeError('Loop must start with an HEVC IDR access unit')
    return chunks, {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                    'frames':len(frames),'stream':s,'loop':'cached compressed AU replay; no seeks or decoder reset'}

class Lane:
    def __init__(self, index, a, chunks, fps, n, Gst):
        self.i,self.a,self.chunks,self.fps,self.n,self.Gst=index,a,chunks,fps,n,Gst
        self.lock=threading.RLock()
        self.input=set(); self.decoded=set(); self.delivered=set()
        self.errors=collections.Counter(); self.late=0; self.release_late=0
        self.buffers=collections.Counter(); self.stage_last={}
        self.max_latency_ms=0.; self.max_release_ms=0.; self.peak_backlog=0
        self.max_wake_lag_ms=0.; self.max_pre_push_lag_ms=0.
        self.max_prepare_ms=0.; self.max_push_block_ms=0.
        self.push_block_total_ms=0.
        self.push_block_over_1ms=0
        self.push_block_over_5ms=0
        self.push_block_over_10ms=0
        self.feed_trace=[]
        self.decoder_sink_ns=[-1]*self.n
        self.decoder_src_ns=[-1]*self.n
        self.last=-1; self.status='STARTING'; self.eos=False; self.trace=[]
        self.pipe=Gst.parse_launch(f'appsrc name=src is-live=true format=time block=true max-bytes=4194304 ! queue name=inq max-size-buffers=8 max-size-bytes=0 max-size-time=0 ! h265parse ! {a.decoder} name=dec ! queue name=outq max-size-buffers=8 max-size-bytes=0 max-size-time=0 ! appsink name=sink sync=false async=false max-buffers=8 drop=false wait-on-eos=true')
        self.src=self.pipe.get_by_name('src')
        self.src.set_property('caps',Gst.Caps.from_string(f'video/x-h265,stream-format=byte-stream,alignment=au,framerate={fps}/1'))
        self.pipe.get_by_name('dec').get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER,self.on_input)
        self.pipe.get_by_name('dec').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER,self.on_output)
        self.sink=self.pipe.get_by_name('sink')

    def identity(self,b):
        Gst=self.Gst
        if b.pts==Gst.CLOCK_TIME_NONE:
            self.errors['missing_pts']+=1; return None
        k=round(b.pts*self.fps/Gst.SECOND)
        if not 0<=k<self.n or abs(b.pts-round(k*Gst.SECOND/self.fps))>1000:
            self.errors['invalid_pts']+=1; return None
        return k

    def record(self,b,stage,ts_ns=None):
        with self.lock:
            name='input' if stage is self.input else ('decoded' if stage is self.decoded else 'delivered')
            self.buffers[name]+=1
            k=self.identity(b)
            if k is None: return None
            if k in stage: self.errors[name+'_duplicate']+=1
            if k<=self.stage_last.get(name,-1): self.errors[name+'_pts_nonmonotonic']+=1
            self.stage_last[name]=k
            stage.add(k)
            if ts_ns is not None:
                if stage is self.input:
                    self.decoder_sink_ns[k]=ts_ns
                elif stage is self.decoded:
                    self.decoder_src_ns[k]=ts_ns
            return k

    def on_input(self,pad,info):
        self.record(info.get_buffer(),self.input,ts_ns=time.monotonic_ns())
        return self.Gst.PadProbeReturn.OK

    def on_output(self,pad,info):
        self.record(info.get_buffer(),self.decoded,ts_ns=time.monotonic_ns())
        return self.Gst.PadProbeReturn.OK

    def feed(self,start,stop):
        Gst=self.Gst
        if self.a.feed_cpus:
            cpus=set()
            for part in self.a.feed_cpus.split(','):
                lo, sep, hi=part.partition('-')
                cpus.update(range(int(lo),int(hi)+1) if sep else [int(lo)])
            os.sched_setaffinity(0,cpus)
        self.status='RUNNING'
        try:
            for k in range(self.n):
                due=start+k/self.fps

                if stop.wait(max(0,due-time.monotonic())):
                    return

                wake_at=time.monotonic()
                wake_lag=max(0,wake_at-due)*1000

                b=Gst.Buffer.new_wrapped(self.chunks[k%len(self.chunks)])
                b.pts=b.dts=round(k*Gst.SECOND/self.fps)
                b.duration=round((k+1)*Gst.SECOND/self.fps)-b.pts

                pre_push_at=time.monotonic()
                prepare_ms=(pre_push_at-wake_at)*1000
                pre_push_lag=max(0,pre_push_at-due)*1000

                push_start=time.monotonic()
                flow=self.src.emit('push-buffer',b)
                push_end=time.monotonic()

                push_ms=(push_end-push_start)*1000
                post_push_lag=max(0,push_end-due)*1000

                with self.lock:
                    self.max_release_ms=max(self.max_release_ms,wake_lag)
                    self.max_wake_lag_ms=max(self.max_wake_lag_ms,wake_lag)
                    self.max_pre_push_lag_ms=max(
                        self.max_pre_push_lag_ms,pre_push_lag
                    )
                    self.max_prepare_ms=max(
                        self.max_prepare_ms,prepare_ms
                    )
                    self.max_push_block_ms=max(
                        self.max_push_block_ms,push_ms
                    )

                    self.push_block_total_ms+=push_ms

                    if wake_lag>self.a.release_ms:
                        self.release_late+=1

                    if push_ms>1:
                        self.push_block_over_1ms+=1
                    if push_ms>5:
                        self.push_block_over_5ms+=1
                    if push_ms>10:
                        self.push_block_over_10ms+=1

                    self.feed_trace.append((
                        k,
                        round(due*1e9),
                        round(wake_lag,6),
                        round(prepare_ms,6),
                        round(pre_push_lag,6),
                        round(push_ms,6),
                        round(post_push_lag,6),
                        int(flow==Gst.FlowReturn.OK)
                    ))

                if flow!=Gst.FlowReturn.OK:
                    raise RuntimeError('appsrc push rejected')

            self.src.emit('end-of-stream')

        except Exception as e:
            self.status='FEED_ERROR'
            self.errors[str(e)]+=1
            stop.set()

    def consume(self,start,stop,tracefile):
        Gst=self.Gst
        with open(tracefile,'w',newline='') as f:
            writer=csv.writer(f); writer.writerow(['frame_id','pts_ns','received_monotonic_ns','latency_ms','late'])
            while not stop.is_set():
                sample=self.sink.emit('try-pull-sample',100*Gst.MSECOND)
                if sample:
                    now=time.monotonic(); b=sample.get_buffer()
                    with self.lock:
                        k=self.record(b,self.delivered)
                        if k is not None:
                            if k<=self.last: self.errors['nonmonotonic_output']+=1
                            self.last=k
                            latency=(now-start-k/self.fps)*1000
                            late=latency>self.a.deadline_ms
                            self.late+=int(late); self.max_latency_ms=max(self.max_latency_ms,latency)
                            if latency < -1: self.errors['early_output']+=1
                            writer.writerow([k,b.pts,round(now*1e9),round(latency,6),int(late)])
                    # Reference released before pulling another frame; no pixel copies.
                    sample=None
                if self.sink.is_eos(): self.eos=True; return

    def snapshot(self,elapsed):
        with self.lock:
            expected=min(self.n,max(0,math.ceil(elapsed*self.fps)))
            backlog=len(self.input-self.delivered)
            self.peak_backlog=max(self.peak_backlog,backlog)
            return dict(lane=self.i,status=self.status,eos=self.eos,planned=self.n,due=expected,
                input=len(self.input),decoded=len(self.decoded),delivered=len(self.delivered),
                late=self.late,on_time=len(self.delivered)-self.late,
                input_missing=self.n-len(self.input),
                decode_missing=len(self.input-self.decoded),delivery_missing=len(self.decoded-self.delivered),
                backlog=backlog,peak_backlog_sampled=self.peak_backlog,
                schedule_debt=max(0,expected-len(self.input)),
                release_late=self.release_late,
                wake_late=self.release_late,
                max_release_ms=self.max_release_ms,
                max_wake_lag_ms=self.max_wake_lag_ms,
                max_pre_push_lag_ms=self.max_pre_push_lag_ms,
                max_prepare_ms=self.max_prepare_ms,
                max_push_block_ms=self.max_push_block_ms,
                avg_push_block_ms=self.push_block_total_ms/max(1,len(self.feed_trace)),
                push_block_over_1ms=self.push_block_over_1ms,
                push_block_over_5ms=self.push_block_over_5ms,
                push_block_over_10ms=self.push_block_over_10ms,
                max_latency_ms=self.max_latency_ms,
                errors=dict(self.errors),inq=self.pipe.get_by_name('inq').get_property('current-level-buffers'),
                raw_input_buffers=self.buffers['input'],raw_decoded_buffers=self.buffers['decoded'],
                raw_delivered_buffers=self.buffers['delivered'],
                outq=self.pipe.get_by_name('outq').get_property('current-level-buffers'))

def worker(a):
    Gst=gst_init(); nlanes,w,h,fps=CASES[a.case]
    baseline=health()
    chunks,media=validate_source(a,w,h,fps,Gst)
    # Keep status/reporting work from holding the GIL across a 60 fps release.
    sys.setswitchinterval(0.001)
    if not Gst.ElementFactory.find(a.decoder): raise RuntimeError('Hardware decoder plugin absent')
    out=pathlib.Path(a.out); out.mkdir(parents=True,exist_ok=True)
    for path in [out,pathlib.Path(a.source).resolve(),pathlib.Path('/')]:
        if os.statvfs(path).f_flag & os.ST_RDONLY:
            raise RuntimeError(f'Read-only filesystem: {path}')
    fs=os.statvfs(out)
    if fs.f_bavail*fs.f_frsize < 512*1024**2:
        raise RuntimeError('Need at least 512 MiB free report space')
    env={'uname':platform.uname()._asdict(),'gst':Gst.version_string(),
         'decoder_inspect':command(['gst-inspect-1.0',a.decoder]),
         'mounts':command(['findmnt','-J']), 'media':media,'arguments':vars(a)}
    atomic(out/'environment.json',env)
    lanes=[Lane(i+1,a,chunks,fps,round(a.seconds*fps),Gst) for i in range(nlanes)]
    stop=threading.Event(); reason=''; start=time.monotonic()+2; threads=[]
    def interrupted(*_): stop.set()
    signal.signal(signal.SIGINT,interrupted); signal.signal(signal.SIGTERM,interrupted)
    try:
        for lane in lanes:
            if lane.pipe.set_state(Gst.State.PLAYING)==Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('Pipeline PLAYING failed')
        for lane in lanes:
            for target,args in [(lane.consume,(start,stop,out/f'lane-{lane.i}-frames.csv')),(lane.feed,(start,stop))]:
                t=threading.Thread(target=target,args=args,daemon=True); t.start(); threads.append(t)
        with open(out/'timeline.csv','w',newline='') as f:
            writer=None
            while not stop.is_set():
                elapsed=time.monotonic()-start
                rows=[x.snapshot(elapsed) for x in lanes]
                for row in rows:
                    flat={'elapsed':round(elapsed,3),**row}; flat['errors']=json.dumps(flat['errors'])
                    if writer is None: writer=csv.DictWriter(f,fieldnames=flat.keys()); writer.writeheader()
                    writer.writerow(flat)
                f.flush()
                atomic(out/'live.json',{'elapsed':elapsed,'lanes':rows})
                print('\033[H\033[2J'+f'{a.decoder} | {a.case} | {max(0,elapsed):.1f}/{a.seconds}s | deadline {a.deadline_ms}ms')
                print('CH   INPUT   DECODE  DELIVER  LATE  BACKLOG  DEBT  STATUS')
                for r in rows: print(f"{r['lane']:2} {r['input']:7} {r['decoded']:7} {r['delivered']:7} {r['late']:5} {r['backlog']:7} {r['schedule_debt']:5} {r['status']}")
                sys.stdout.flush()
                for lane in lanes:
                    msg=lane.pipe.get_bus().pop_filtered(Gst.MessageType.ERROR)
                    if msg: raise RuntimeError(str(msg.parse_error()))
                if all(x.eos for x in lanes):
                    # Scanning a large dmesg ring holds the Python GIL long enough
                    # to perturb the 60 fps feeder on slower userspace cores. Check
                    # the complete ring at both boundaries, outside measurement.
                    current=kernel()
                    if BAD.search(current) or (baseline and baseline.splitlines()[-1] not in current):
                        reason='STORAGE_ERROR_OR_KERNEL_LOG_GAP'
                    else:
                        reason='EOS'
                    break
                if elapsed>a.seconds+a.drain_seconds: reason='DRAIN_TIMEOUT'; break
                stop.wait(1)
            if not reason: reason='INTERRUPTED_OR_FEED_ERROR'
    finally:
        stop.set()
        rows=[x.snapshot(time.monotonic()-start) for x in lanes]
        for r in rows:
            lane=lanes[r['lane']-1]
            r['identity_sets_match']=lane.input==lane.decoded==lane.delivered
            r['missing_delivery_ids']=sorted(set(range(lane.n))-lane.delivered)
            r['missing_decode_ids']=sorted(lane.input-lane.decoded)
            r['pass']=bool(reason=='EOS' and r['eos'] and r['identity_sets_match'] and r['input']==r['planned']==r['decoded']==r['delivered']
                and not r['late'] and not r['release_late'] and not r['errors'])
            r['status']='PASS' if r['pass'] else 'FAIL'
            # Missing is only a finalized endpoint deficit, never inferred from transient FPS.
            r['missing_planned_delivery']=r['planned']-r['delivered']
        passed=all(r['pass'] for r in rows)
        atomic(out/'result.json',{'reason':reason or 'EXCEPTION','pass':passed,
            'qualified_30min':passed and a.seconds>=1800,'measurement':'appsink receipt; no display or inference',
            'lanes':rows})
        for lane in lanes: lane.pipe.set_state(Gst.State.NULL)
        for t in threads: t.join(2)
        for lane in lanes:
            with open(out/f'lane-{lane.i}-stages.csv','w',newline='') as f:
                writer=csv.writer(f)
                writer.writerow(['frame_id','decoder_sink_ns','decoder_src_ns'])
                writer.writerows(
                    (k,lane.decoder_sink_ns[k],lane.decoder_src_ns[k])
                    for k in range(lane.n)
                )
        for lane in lanes:
            with open(out/f'lane-{lane.i}-feed.csv','w',newline='') as f:
                writer=csv.writer(f)
                writer.writerow([
                    'frame_id','due_monotonic_ns','wake_lag_ms',
                    'prepare_ms','pre_push_lag_ms','push_block_ms',
                    'post_push_lag_ms','push_ok'
                ])
                writer.writerows(lane.feed_trace)
    return 0 if all(r['pass'] for r in rows) else 1

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--decoder',required=True,choices=['mppvideodec','v4l2slh265dec'])
    p.add_argument('--case',required=True,choices=CASES)
    p.add_argument('--source',required=True)
    p.add_argument('--seconds',type=int,default=1800)
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--deadline-ms',type=float,default=100)
    p.add_argument('--release-ms',type=float,default=10)
    p.add_argument('--drain-seconds',type=int,default=15)
    p.add_argument('--max-source-mb',type=int,default=128)
    p.add_argument('--feed-cpus',default='',help='CPU list/ranges for input pacing thread, e.g. 6-7')
    p.add_argument('--out',required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args()
    if min(a.seconds,a.repeats,a.deadline_ms,a.release_ms,a.drain_seconds,a.max_source_mb)<=0: p.error('positive values required')
    if a.worker: return worker(a)
    # Whole run is locked by run.sh, including preflight and all repetitions.
    out=pathlib.Path(a.out).resolve()
    out.mkdir(parents=True,exist_ok=False)
    summary=[]; child=None
    def stop(*_):
        if child and child.poll() is None: os.killpg(child.pid,signal.SIGTERM)
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
    try:
        for trial in range(1,a.repeats+1):
            dest=out/f'trial-{trial}'
            args=[sys.executable,str(pathlib.Path(__file__).resolve()),*sys.argv[1:],'--worker','--out',str(dest)]
            # Last --out wins in argparse.
            child=subprocess.Popen(args,start_new_session=True)
            try: rc=child.wait(timeout=a.seconds+a.drain_seconds+240)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL); child.wait(); rc=124
            result=dest/'result.json'
            data=json.loads(result.read_text()) if result.exists() else {'pass':False,'reason':'NO_FINAL_RESULT'}
            data.update(trial=trial,exit_code=rc)
            if rc: data['pass']=False
            summary.append(data)
            atomic(out/'summary.json',summary)
            with open(out/'summary.csv','w',newline='') as f:
                wr=csv.writer(f); wr.writerow(['trial','exit_code','pass','reason','lane','planned','input','decoded','delivered','late','missing','release_late'])
                for d in summary:
                    for r in d.get('lanes') or [{}]:
                        wr.writerow([d['trial'],d['exit_code'],d['pass'],d['reason'],*[r.get(k,'') for k in ['lane','planned','input','decoded','delivered','late','missing_planned_delivery','release_late']]])
            if data['reason']!='EOS': break  # infrastructure/storage faults must not be retried blindly
    except KeyboardInterrupt:
        if child and child.poll() is None:
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: os.killpg(child.pid,signal.SIGKILL); child.wait()
        atomic(out/'interrupted.json',{'status':'INTERRUPTED','completed_trials':len(summary)})
        return 130
    return 0 if len(summary)==a.repeats and all(d['pass'] for d in summary) else 1

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as e:
        print(f'ERROR: {e}',file=sys.stderr); sys.exit(2)
