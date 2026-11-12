#!/usr/bin/env python3
"""Single-stream, unpaced HEVC hardware-decode throughput demo."""
import argparse, json, pathlib, statistics, subprocess, sys, tempfile, threading, time
import bench


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',default='source-4k60.mp4')
    p.add_argument('--frames',type=int,default=3600)
    p.add_argument('--repeats',type=int,default=1)
    p.add_argument('--decoder',choices=['auto','mppvideodec','v4l2slh265dec'],default='auto')
    p.add_argument('--max-source-mb',type=int,default=128)
    p.add_argument('--json-out')
    a=p.parse_args()
    if a.frames <= 0 or a.repeats <= 0: p.error('frames/repeats must be positive')
    if a.repeats > 1:
        trials=[]
        with tempfile.TemporaryDirectory(prefix='hevc-throughput-') as td:
            for i in range(a.repeats):
                result=pathlib.Path(td)/f'trial-{i+1}.json'
                cmd=[sys.executable,str(pathlib.Path(__file__).resolve()),'--source',a.source,
                     '--frames',str(a.frames),'--repeats','1','--decoder',a.decoder,
                     '--max-source-mb',str(a.max_source_mb),'--json-out',str(result)]
                rc=subprocess.run(cmd).returncode
                if rc or not result.exists(): return rc or 1
                trials.append(json.loads(result.read_text()))
        rates=[x['throughput_fps'] for x in trials]
        summary={'repeats':a.repeats,'frames_per_trial':a.frames,'pass':all(x['pass'] for x in trials),
                 'throughput_fps_min':min(rates),'throughput_fps_median':statistics.median(rates),
                 'throughput_fps_max':max(rates),'equivalent_4k60_median':statistics.median(rates)/60,
                 'trials':trials}
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        if a.json_out: pathlib.Path(a.json_out).write_text(json.dumps(summary,indent=2))
        return 0 if summary['pass'] else 1
    Gst=bench.gst_init()
    if a.decoder=='auto':
        a.decoder=next((x for x in ('mppvideodec','v4l2slh265dec') if Gst.ElementFactory.find(x)),None)
    if not a.decoder: raise SystemExit('No supported hardware decoder found')
    chunks,media=bench.validate_source(a,3840,2160,60,Gst)
    pipe=Gst.parse_launch(
        f'appsrc name=src is-live=false format=time block=true max-bytes=8388608 '
        f'! queue max-size-buffers=16 max-size-bytes=0 max-size-time=0 '
        f'! h265parse ! {a.decoder} '
        f'! queue max-size-buffers=16 max-size-bytes=0 max-size-time=0 '
        f'! appsink name=sink sync=false async=false max-buffers=16 drop=false wait-on-eos=true')
    src=pipe.get_by_name('src'); sink=pipe.get_by_name('sink')
    src.set_property('caps',Gst.Caps.from_string('video/x-h265,stream-format=byte-stream,alignment=au,framerate=60/1'))
    state={'push_error':None,'push_done':None}
    start=time.monotonic()
    def feed():
        try:
            for k in range(a.frames):
                b=Gst.Buffer.new_wrapped(chunks[k%len(chunks)])
                b.pts=b.dts=round(k*Gst.SECOND/60)
                b.duration=round((k+1)*Gst.SECOND/60)-b.pts
                if src.emit('push-buffer',b)!=Gst.FlowReturn.OK:
                    raise RuntimeError('push-buffer failed')
            src.emit('end-of-stream')
            state['push_done']=time.monotonic()
        except Exception as e:
            state['push_error']=str(e)
    if pipe.set_state(Gst.State.PLAYING)==Gst.StateChangeReturn.FAILURE:
        raise RuntimeError('Pipeline PLAYING failed')
    t=threading.Thread(target=feed,daemon=True); t.start()
    delivered=0
    try:
        while True:
            sample=sink.emit('try-pull-sample',100*Gst.MSECOND)
            if sample: delivered+=1
            msg=pipe.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if msg: raise RuntimeError(str(msg.parse_error()))
            if sink.is_eos(): break
            if state['push_error']: raise RuntimeError(state['push_error'])
    finally:
        end=time.monotonic(); pipe.set_state(Gst.State.NULL); t.join(2)
    elapsed=end-start
    result={'decoder':a.decoder,'source_sha256':media['sha256'],'requested_frames':a.frames,
            'delivered_frames':delivered,'elapsed_seconds':elapsed,'throughput_fps':delivered/elapsed,
            'equivalent_4k60_streams':delivered/elapsed/60,'pass':delivered==a.frames}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if a.json_out: pathlib.Path(a.json_out).write_text(json.dumps(result,indent=2))
    return 0 if result['pass'] else 1


if __name__=='__main__': raise SystemExit(main())
