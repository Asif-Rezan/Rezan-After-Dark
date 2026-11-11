"""Private Drive-backed parallel production stages for the visible workflow DAG."""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import wave
from zoneinfo import ZoneInfo

from fluency_lessons import generate_lesson,validate_lesson,SPEAKERS
from fluency_render import thumbnail
from fluency_video import (digest,file_hash,write_json,prepare_audio,make_speech,frame_ranges,
                           render_frames,write_validation,run,record_history)

PARTS=16
FPS=12


def context(root):
    from workflow import Store,required
    run_id=required('SAVED_RUN_ID')
    if not re.fullmatch(r'daily-\d+',run_id):
        raise ValueError('Expected daily-<original GitHub run ID>')
    root.mkdir(parents=True,exist_ok=True)
    return Store(),run_id


def load_lesson(store,run_id):
    return validate_lesson(store.load(f'runs/{run_id}/lesson.json',{}))


def download(store,run_id,root,name,expected=None):
    if name in ('.','..') or not re.fullmatch(r'[A-Za-z0-9_.-]+',name):
        raise ValueError('Invalid production asset name')
    fid=store.find(f'runs/{run_id}/{name}')
    if not fid:
        raise RuntimeError(f'Missing saved asset {name}; no implicit regeneration')
    store.download(fid,root/name)
    if expected is not None and file_hash(root/name)!=expected:
        raise RuntimeError(f'Saved asset hash mismatch: {name}')
    return root/name


def speech_key(turn):
    return digest([turn['text'],turn['speaker'],'kokoro','fluency-voice-v1',.9,0])


def voice_cache(lesson,root,speaker):
    if speaker not in SPEAKERS:
        raise ValueError('Unknown voice worker')
    cache=root/'speech';cache.mkdir(exist_ok=True)
    result={}
    for turn in lesson['turns']:
        if turn['speaker']!=speaker:
            continue
        key=speech_key(turn)
        path=cache/f'{key}.wav'
        if key in result:
            continue
        if not path.exists():
            temporary=cache/f'{key}-pending.wav'
            make_speech(turn['text'],speaker,temporary,'kokoro')
            with wave.open(str(temporary)) as audio:
                if (audio.getnchannels(),audio.getsampwidth(),audio.getframerate())!=(1,2,24000) or audio.getnframes()<2400:
                    raise RuntimeError('Invalid production voice audio')
            temporary.replace(path)
        result[key]=file_hash(path)
    return result


def restore_voice(store,run_id,root,lesson,speaker):
    manifest=store.load(f'runs/{run_id}/voice-{speaker}.json',{})
    expected={speech_key(turn) for turn in lesson['turns'] if turn['speaker']==speaker}
    if (manifest.get('lesson_hash')!=digest(lesson) or manifest.get('speaker')!=speaker
            or set(manifest.get('files',{}))!=expected
            or any(not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{64}',v) for v in manifest.get('files',{}).values())):
        raise RuntimeError('Voice manifest does not match this lesson and speaker')
    cache=root/'speech';cache.mkdir(exist_ok=True)
    for key,sha in manifest['files'].items():
        temp=download(store,run_id,root,f'speech-{key}.wav',sha)
        temp.replace(cache/f'{key}.wav')


def timing_manifest(lesson,events,total,root):
    if not 480<=total<=1200:
        raise ValueError(f'Measured lesson duration {total:.1f}s is outside 8–20 minutes')
    return dict(lesson_hash=digest(lesson),fps=FPS,total_seconds=total,frame_count=math.ceil(total*FPS),parts=PARTS,
        ranges=frame_ranges(math.ceil(total*FPS),PARTS),
        asset_hashes={name:file_hash(root/name) for name in ('narration.wav','captions.srt','timeline.json')})


def load_timing(store,run_id,lesson):
    manifest=store.load(f'runs/{run_id}/timing.json',{})
    count=manifest.get('frame_count',0)
    if (manifest.get('lesson_hash')!=digest(lesson) or manifest.get('fps')!=FPS or manifest.get('parts')!=PARTS
            or not 480<=manifest.get('total_seconds',0)<=1200
            or count!=math.ceil(manifest['total_seconds']*FPS)
            or manifest.get('ranges')!=[list(r) for r in frame_ranges(count,PARTS)]
            or set(manifest.get('asset_hashes',{}))!={'narration.wav','captions.srt','timeline.json'}
            or any(not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{64}',v) for v in manifest.get('asset_hashes',{}).values())):
        raise RuntimeError('Timing manifest does not match this lesson and render partition')
    return manifest


def validate_parts(lesson,timing,manifests):
    if len(manifests)!=PARTS:
        raise RuntimeError('Missing render chunk manifest')
    for index,manifest in enumerate(manifests):
        if (manifest.get('part')!=index or manifest.get('lesson_hash')!=digest(lesson)
                or manifest.get('timing_hash')!=digest(timing) or manifest.get('fps')!=FPS
                or manifest.get('range')!=timing['ranges'][index]
                or not isinstance(manifest.get('sha256'),str) or not re.fullmatch(r'[a-f0-9]{64}',manifest['sha256'])):
            raise RuntimeError('Render chunks overlap or belong to another lesson/timing')


def upload_only(store,run_id,root):
    """Restore completed media only; captions may be reconciled but no regeneration."""
    from workflow import upload_private
    from fluency_upload import ensure_captions
    key=f'runs/{run_id}/state.json'
    state=store.load(key,{})
    if state.get('phase')=='upload_started':
        raise RuntimeError('Video insertion outcome ambiguous; refusing another insertion')
    if state.get('phase') in ('uploaded','complete','verified_private'):
        if state.get('format')!='english-fluency':
            raise RuntimeError('Foreign upload state')
        ensure_captions(store,key,state,root)
        return
    lesson=load_lesson(store,run_id)
    report=store.load(f'runs/{run_id}/validation.json',{})
    if report.get('lesson_hash')!=digest(lesson) or not report.get('animated'):
        raise RuntimeError('No completed render matching the saved lesson')
    for name in ('video.mp4','thumbnail.jpg','captions.srt'):
        sha=report.get('asset_hashes',{}).get(name)
        if not isinstance(sha,str):
            raise RuntimeError('Completed media manifest is incomplete')
        download(store,run_id,root,name,sha)
    record_history(store, run_id, lesson)
    state=dict(phase='prepared',story=lesson,hash=digest(lesson),format='english-fluency')
    store.save(key,state)
    upload_private(root/'video.mp4',root/'thumbnail.jpg',lesson,state,store,key,
        'Original English learning dialogue. Alex and Emma are illustrated fictional presenters. Synthetic narration; no background music.',category='27')
    ensure_captions(store,key,state,root)


def execute(stage,root,speaker=None,part=None):
    store,run_id=context(root)
    prefix=f'runs/{run_id}/'
    if stage in ('verify','cleanup'):
        current=store.load(prefix+'state.json',{})
        if current.get('format')=='english-fluency':
            from fluency_upload import ensure_captions
            ensure_captions(store,prefix+'state.json',current,root)
        from daily_publish import main
        verified = main(cleanup_media=stage=='cleanup', include_previous=stage=='cleanup')
        if stage=='verify':
            output = os.getenv('GITHUB_OUTPUT')
            if output:
                with Path(output).open('a') as stream:
                    stream.write(f'verified={str(verified).lower()}\n')
        if stage=='cleanup':
            state=store.load(prefix+'state.json',{})
            if state.get('format')=='english-fluency':
                record_history(store,run_id,state['story'])
        return
    if stage=='upload':
        upload_only(store,run_id,root);return
    state=store.load(prefix+'state.json',{})
    if state.get('phase') in ('upload_started','uploaded','complete','verified_private'):
        raise RuntimeError('Existing upload: production stages cannot regenerate this run')
    if stage=='prepare':
        if os.getenv('DAILY_ENABLED')!='true':
            raise RuntimeError('Daily production is disabled')
        saved=store.load(prefix+'lesson.json',None)
        if saved is None:
            saved=generate_lesson(dt.datetime.now(ZoneInfo('Asia/Dhaka')).date().isoformat(),store.load('fluency-history.json',[]))
            store.save(prefix+'lesson.json',saved)
        validate_lesson(saved)
        record_history(store,run_id,saved)
        print(json.dumps({'stage':'prepare','lesson_saved':True,'run':run_id}));return
    lesson=load_lesson(store,run_id)
    if stage=='voice':
        files=voice_cache(lesson,root,speaker)
        for key in files:
            store.upload(root/'speech'/f'{key}.wav',prefix+f'speech-{key}.wav')
        store.save(prefix+f'voice-{speaker}.json',dict(lesson_hash=digest(lesson),speaker=speaker,files=files))
    elif stage=='thumbnail':
        thumbnail(lesson,root/'thumbnail.jpg',variant_key=run_id+digest(lesson))
        store.upload(root/'thumbnail.jpg',prefix+'thumbnail.jpg')
        store.save(prefix+'thumbnail.json',dict(lesson_hash=digest(lesson),sha256=file_hash(root/'thumbnail.jpg')))
    elif stage=='timing':
        for name in SPEAKERS: restore_voice(store,run_id,root,lesson,name)
        events,total=prepare_audio(lesson,root,'kokoro',require_cached=True)
        manifest=timing_manifest(lesson,events,total,root)
        for name in manifest['asset_hashes']:store.upload(root/name,prefix+name)
        store.save(prefix+'timing.json',manifest)
    elif stage=='render':
        if part not in range(PARTS):raise ValueError('Invalid render worker')
        timing=load_timing(store,run_id,lesson)
        for name in ('narration.wav','timeline.json'):
            download(store,run_id,root,name,timing['asset_hashes'][name])
        events=json.loads((root/'timeline.json').read_text())
        start,end=timing['ranges'][part]
        clip=f'render-{part:03}.mp4'
        render_frames(lesson,root,events,timing['total_seconds'],FPS,start,end,root/clip)
        store.upload(root/clip,prefix+clip)
        store.save(prefix+f'render-{part:03}.json',dict(part=part,lesson_hash=digest(lesson),timing_hash=digest(timing),
                    fps=FPS,range=[start,end],sha256=file_hash(root/clip)))
    elif stage=='assemble':
        timing=load_timing(store,run_id,lesson)
        manifests=[store.load(prefix+f'render-{i:03}.json',{}) for i in range(PARTS)]
        validate_parts(lesson,timing,manifests)
        for name,sha in timing['asset_hashes'].items():download(store,run_id,root,name,sha)
        thumb=store.load(prefix+'thumbnail.json',{})
        if (thumb.get('lesson_hash')!=digest(lesson) or not isinstance(thumb.get('sha256'),str)
                or not re.fullmatch(r'[a-f0-9]{64}',thumb['sha256'])):
            raise RuntimeError('Thumbnail belongs to a different lesson or lacks a valid hash')
        download(store,run_id,root,'thumbnail.jpg',thumb.get('sha256'))
        clips=[]
        for i,manifest in enumerate(manifests):
            path=download(store,run_id,root,f'render-{i:03}.mp4',manifest['sha256'])
            probe=json.loads(__import__('subprocess').check_output(['ffprobe','-v','error','-show_streams','-of','json',str(path)]))
            streams=probe['streams'];expected=timing['ranges'][i][1]-timing['ranges'][i][0]
            if (len(streams)!=1 or streams[0]['codec_name']!='h264' or int(streams[0]['nb_frames'])!=expected
                    or streams[0]['r_frame_rate']!=f'{FPS}/1' or streams[0]['width']!=1280 or streams[0]['height']!=720):
                raise RuntimeError('Render chunk frame count or codec mismatch')
            clips.append("file '"+str(path.resolve())+"'")
        (root/'concat.txt').write_text('\n'.join(clips)+'\n')
        pending=root/'assembled.pending.mp4'
        run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',root/'concat.txt','-i',root/'narration.wav',
            '-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','192k','-af','loudnorm=I=-16:TP=-1.5:LRA=7',
            '-t',str(timing['total_seconds']),'-movflags','+faststart',pending])
        pending.replace(root/'video.mp4')
        write_validation(lesson,root,timing['total_seconds'],'kokoro',FPS)
        store.upload(root/'video.mp4',prefix+'video.mp4')
        store.save(prefix+'validation.json',json.loads((root/'validation.json').read_text()))
        store.save(prefix+'state.json',dict(phase='prepared',story=lesson,hash=digest(lesson),format='english-fluency'))
    else:raise ValueError('Unknown production stage')
    print(json.dumps({'stage':stage,'speaker':speaker,'part':part,'run':run_id,'passed':True}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['prepare','voice','thumbnail','timing','render','assemble','upload','verify','cleanup'])
    parser.add_argument('--output',type=Path,default=Path('output/english-fluency'))
    parser.add_argument('--speaker',choices=SPEAKERS)
    parser.add_argument('--part',type=int,choices=range(PARTS))
    args=parser.parse_args()
    execute(args.stage,args.output,args.speaker,args.part)

if __name__=='__main__':main()
