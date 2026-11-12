#!/usr/bin/env python3
"""Real-time 4K60 HEVC playback to Wayland or DRM/KMS HDMI output."""
import argparse, glob, os, pathlib
import bench


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',default='source-4k60.mp4')
    p.add_argument('--decoder',choices=['auto','mppvideodec','v4l2slh265dec'],default='auto')
    p.add_argument('--sink',choices=['auto','wayland','kms','fake'],default='auto')
    p.add_argument('--loop',action='store_true')
    a=p.parse_args()
    source=str(pathlib.Path(a.source).resolve())
    if not pathlib.Path(source).is_file(): p.error('source not found')
    Gst=bench.gst_init()
    if a.decoder=='auto':
        a.decoder=next((x for x in ('mppvideodec','v4l2slh265dec') if Gst.ElementFactory.find(x)),None)
    if not a.decoder: raise SystemExit('No supported hardware decoder found')
    # SSH does not normally inherit the desktop Wayland environment.
    runtime = pathlib.Path(
        os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
    )
    if not os.environ.get('WAYLAND_DISPLAY'):
        sockets = sorted(
            x for x in runtime.glob('wayland-*') if x.is_socket()
        )
        if sockets:
            os.environ['XDG_RUNTIME_DIR'] = str(runtime)
            os.environ['WAYLAND_DISPLAY'] = sockets[0].name

    if a.sink == 'auto':
        a.sink = 'wayland' if os.environ.get('WAYLAND_DISPLAY') else 'kms'

    if a.decoder == 'v4l2slh265dec' and a.sink == 'kms':
        raise SystemExit(
            'Pi 5 HEVC DMABuf is not compatible with this direct '
            'kmssink pipeline. Use --sink wayland or --sink fake.'
        )

    if a.sink == 'wayland' and not os.environ.get('WAYLAND_DISPLAY'):
        raise SystemExit(
            'Wayland socket not found. Use --sink fake for '
            'headless decoding validation.'
        )
    if a.sink=='kms':
        connected=[]
        for status in glob.glob('/sys/class/drm/card*-*/status'):
            try:
                if pathlib.Path(status).read_text().strip()=='connected': connected.append(status)
            except OSError:
                pass
        if not connected:
            raise SystemExit('No connected DRM display found; connect HDMI before starting KMS playback')
    sink = {
        'wayland': 'waylandsink fullscreen=true sync=true',
        'kms': 'kmssink sync=true',
        'fake': 'fakesink sync=false',
    }[a.sink]
    launch=f'filesrc name=file ! qtdemux ! h265parse ! {a.decoder} ! queue max-size-buffers=8 ! {sink}'
    pipe=Gst.parse_launch(launch); pipe.get_by_name('file').set_property('location',source)
    bus=pipe.get_bus()
    try:
        while True:
            if pipe.set_state(Gst.State.PLAYING)==Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('Pipeline PLAYING failed')
            msg=bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE,Gst.MessageType.ERROR|Gst.MessageType.EOS)
            if msg.type==Gst.MessageType.ERROR:
                err,dbg=msg.parse_error(); raise RuntimeError(f'{err}: {dbg}')
            if not a.loop: break
            pipe.set_state(Gst.State.NULL)
    except KeyboardInterrupt:
        pass
    finally:
        pipe.set_state(Gst.State.NULL)
    return 0


if __name__=='__main__': raise SystemExit(main())
