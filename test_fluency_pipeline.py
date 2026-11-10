import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch
import wave

from PIL import ImageChops
from fluency_video import frame_ranges,frame_at,digest,prepare_audio
from fluency_render import CharacterAnimator
from fluency_pipeline import voice_cache,restore_voice,validate_parts,upload_only,load_timing
from daily_publish import disposable,main as verify

EXAMPLE=Path(__file__).parent/'examples'/'ordering-coffee.json'

class PipelineTests(unittest.TestCase):
    def setUp(self):self.lesson=json.loads(EXAMPLE.read_text())

    def test_partitions_cover_uneven_frame_count_exactly_once(self):
        ranges=frame_ranges(1003,16)
        self.assertEqual(ranges[0][0],0)
        self.assertEqual(ranges[-1][1],1003)
        self.assertEqual([i for a,b in ranges for i in range(a,b)],list(range(1003)))
        self.assertLessEqual(max(b-a for a,b in ranges)-min(b-a for a,b in ranges),1)

    def test_parallel_boundary_frames_match_sequential_global_time(self):
        events=[dict(start=0,end=2,turn=self.lesson['turns'][0],audio='voice.wav'),
                dict(start=2,end=3,turn=self.lesson['turns'][0],practice=True),
                dict(start=3,end=5.1,turn=self.lesson['turns'][2],audio='voice.wav')]
        energy=[.8]*62;animator=CharacterAnimator();saved={};event_index=0
        targets={i for a,b in frame_ranges(62,16) for i in (a,b-1)}
        for i in range(62):
            im,event_index=frame_at(self.lesson,animator,events,energy,5.1,12,i,event_index)
            if i in targets:saved[i]=im
        for i,expected in saved.items():
            actual,_=frame_at(self.lesson,animator,events,energy,5.1,12,i,0)
            self.assertIsNone(ImageChops.difference(expected,actual).getbbox())

    def test_voice_worker_generates_only_assigned_speaker_and_reuses_repeats(self):
        lesson=copy.deepcopy(self.lesson)
        lesson['turns']=[dict(speaker=s,text='Hello there.',section='LISTEN',pause=.5) for s in ['Emma','Alex','Alex']]
        calls=[]
        def fake(text,speaker,path,backend):
            calls.append(speaker)
            with wave.open(str(path),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(b'\1\0'*24000)
        with tempfile.TemporaryDirectory() as tmp,patch('fluency_pipeline.make_speech',side_effect=fake):
            manifest=voice_cache(lesson,Path(tmp),'Alex')
        self.assertEqual(calls,['Alex']);self.assertEqual(len(manifest),1)

    def test_timing_cannot_silently_synthesize_missing_parallel_audio(self):
        with tempfile.TemporaryDirectory() as tmp,patch('fluency_video.make_speech') as generate:
            with self.assertRaisesRegex(RuntimeError,'missing'):
                prepare_audio(self.lesson,Path(tmp),'kokoro',require_cached=True)
            generate.assert_not_called()

    def test_restore_voice_rejects_foreign_or_incomplete_manifest(self):
        store=MagicMock();store.load.return_value={'speaker':'Emma','lesson_hash':digest(self.lesson),'files':{}}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):restore_voice(store,'daily-1',Path(tmp),self.lesson,'Alex')
        store.download.assert_not_called()

    def test_assembly_rejects_foreign_missing_overlapping_and_unhashed_chunks(self):
        timing={'ranges':[list(r) for r in frame_ranges(1003,16)],'fps':12,'frame_count':1003}
        manifests=[dict(part=i,lesson_hash=digest(self.lesson),timing_hash=digest(timing),fps=12,
                        range=timing['ranges'][i],sha256='a'*64) for i in range(16)]
        validate_parts(self.lesson,timing,manifests)
        for field,value in [('part',0),('lesson_hash','foreign'),('fps',24),('range',[0,50]),('sha256',None)]:
            bad=copy.deepcopy(manifests);bad[1][field]=value
            with self.assertRaises(RuntimeError):validate_parts(self.lesson,timing,bad)
        with self.assertRaises(RuntimeError):validate_parts(self.lesson,timing,manifests[:3])

    def test_new_disposable_media_whitelist_is_strict(self):
        for name in ['speech-'+'a'*64+'.wav','render-000.mp4','render-003.mp4','render-015.mp4']:
            self.assertTrue(disposable(name))
        for name in ['render-016.mp4','speech-secret.wav','voice-Alex.json','timing.json','lesson.json','render-000.json','../render-000.mp4']:
            self.assertFalse(disposable(name))

    def test_upload_only_refuses_ambiguous_insertion_before_any_generation(self):
        store=MagicMock();store.load.return_value={'phase':'upload_started'}
        with tempfile.TemporaryDirectory() as tmp,patch('workflow.upload_private') as insert,patch('fluency_pipeline.generate_lesson') as generate:
            with self.assertRaisesRegex(RuntimeError,'ambiguous'):upload_only(store,'daily-1',Path(tmp))
            insert.assert_not_called();generate.assert_not_called();store.download.assert_not_called()

    def test_verify_only_writes_proof_but_never_cleans(self):
        story={'title':'Title','description':'Description','tags':[]}
        state={'phase':'complete','video_id':'v','thumbnail_uploaded':True,'story':story,'hash':'abc'}
        store=MagicMock();store.load.side_effect=lambda key,default:state if key.endswith('state.json') else []
        client=MagicMock();client.videos().list().execute.return_value={'items':[{
            'id':'v','snippet':{'channelId':'c',**story},'status':{'privacyStatus':'private','uploadStatus':'processed'},
            'processingDetails':{'processingStatus':'succeeded'},'contentDetails':{'definition':'hd'}}]}
        with patch('workflow.Store',return_value=store),patch('googleapiclient.discovery.build',return_value=client), \
            patch('google.oauth2.credentials.Credentials.from_authorized_user_info'), \
            patch.dict('os.environ',{'SAVED_RUN_ID':'daily-1','YOUTUBE_TOKEN_JSON':'{}','EXPECTED_CHANNEL_ID':'c'}), \
            patch('daily_publish.cleanup') as cleanup:
            verify(cleanup_media=False)
            cleanup.assert_not_called()
        self.assertEqual(state['phase'],'verified_private')
        self.assertTrue(state['verification']['hd'])

if __name__=='__main__':unittest.main()

class PipelineIntegrityTests(unittest.TestCase):
    def test_voice_restore_rejects_corrupted_bytes(self):
        from fluency_pipeline import speech_key
        lesson=json.loads(EXAMPLE.read_text())
        lesson['turns']=[dict(speaker='Alex',text='Hello there.',section='LISTEN',pause=.5)]
        key=speech_key(lesson['turns'][0]);store=MagicMock()
        store.load.return_value={'speaker':'Alex','lesson_hash':digest(lesson),'files':{key:'a'*64}}
        store.find.return_value='file-id'
        store.download.side_effect=lambda fid,path:path.write_bytes(b'corrupted')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,'hash mismatch'):
                restore_voice(store,'daily-1',Path(tmp),lesson,'Alex')

    def test_timing_manifest_rejects_missing_audio_integrity_proof(self):
        lesson=json.loads(EXAMPLE.read_text());store=MagicMock()
        manifest=dict(lesson_hash=digest(lesson),fps=12,parts=16,total_seconds=600,frame_count=7200,
                      ranges=[list(r) for r in frame_ranges(7200,16)],asset_hashes={})
        store.load.return_value=manifest
        with self.assertRaisesRegex(RuntimeError,'Timing manifest'):load_timing(store,'daily-1',lesson)
