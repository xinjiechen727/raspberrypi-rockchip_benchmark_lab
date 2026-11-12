#!/usr/bin/env python3
"""Concurrent paced 4K60 streams; duration also serves stability runs."""
import argparse, json, pathlib, subprocess, time


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',default='source-4k60.mp4')
    p.add_argument('--seconds',type=int,default=60)
    p.add_argument('--repeats',type=int,default=1)
    p.add_argument('--streams',type=int,choices=[2,4],default=2)
    p.add_argument('--decoder',choices=['auto','mppvideodec','v4l2slh265dec'],default='auto')
    p.add_argument('--out')
    a=p.parse_args()
    if a.seconds<=0 or a.repeats<=0: p.error('seconds/repeats must be positive')
    if a.decoder=='auto':
        checks=[('mppvideodec','gst-inspect-1.0'),('v4l2slh265dec','gst-inspect-1.0')]
        a.decoder=next((d for d,cmd in checks if subprocess.run([cmd,d],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0),None)
    if not a.decoder: raise SystemExit('No supported hardware decoder found')
    out=pathlib.Path(a.out or f'/dev/shm/hevc-4x4k60-{int(time.time_ns())}')
    case=f'{a.streams}x4K60'
    cmd=['bash','run.sh','--decoder',a.decoder,'--case',case,'--source',a.source,
         '--seconds',str(a.seconds),'--repeats',str(a.repeats),'--out',str(out)]
    rc=subprocess.run(cmd).returncode
    summary=out/'summary.json'
    data=json.loads(summary.read_text()) if summary.exists() else []
    print(json.dumps({'decoder':a.decoder,'case':case,'seconds':a.seconds,'repeats':a.repeats,
                      'output':str(out),'exit_code':rc,'trials':data},ensure_ascii=False,indent=2))
    return rc


if __name__=='__main__': raise SystemExit(main())
