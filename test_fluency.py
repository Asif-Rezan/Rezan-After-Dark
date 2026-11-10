import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
from PIL import Image, ImageChops
from fluency_lessons import validate_lesson, generate_lesson
from fluency_render import CharacterAnimator, background, lesson_frame, thumbnail, ASSETS
from fluency_video import prepare_audio, digest, daily

EXAMPLE = Path(__file__).parent/'examples'/'ordering-coffee.json'

class FluencyTests(unittest.TestCase):
    def setUp(self):
        self.lesson = json.loads(EXAMPLE.read_text())

    def test_unknown_speaker_and_invalid_roleplay_rejected(self):
        bad = copy.deepcopy(self.lesson); bad['turns'][0]['speaker'] = 'Narrator'
        with self.assertRaises(ValueError): validate_lesson(bad)
        bad = copy.deepcopy(self.lesson); bad['turns'][14]['pause'] = .2
        with self.assertRaises(ValueError): validate_lesson(bad)

    def test_both_speakers_required(self):
        for turn in self.lesson['turns']: turn['speaker'] = 'Emma'
        with self.assertRaises(ValueError): validate_lesson(self.lesson)

    def test_generated_lessons_rotate_topic_and_include_learning_practice(self):
        def ask(prompt, schema):
            if 'headline' in schema['properties']:
                return {'angle':'Ordering Coffee Politely','headline':['ORDER','COFFEE','POLITELY']}
            return {'dialogue':{f'line_{i+1:02}':
                'Could you please tell me what you would like today?' if i%2==0 else
                'I would like a small coffee with oat milk please.' for i in range(16)}}
        lesson=generate_lesson('2026-11-10',[],ask)
        next_lesson=generate_lesson('2026-11-10',[{'topic_id':lesson['topic_id']}],ask)
        self.assertNotEqual(lesson['topic_id'],next_lesson['topic_id'])
        self.assertEqual({t['section'] for t in lesson['turns']},{'LISTEN','REPEAT','YOUR TURN','FINISH'})

    def test_audio_cache_is_speaker_sensitive_and_repeat_is_reused(self):
        lesson=copy.deepcopy(self.lesson)
        lesson['turns']=[dict(speaker=s,text='Hello there.',section='LISTEN',pause=.5) for s in ['Alex','Emma','Alex']]
        calls=[]
        def fake(text,speaker,path,backend):
            calls.append(speaker)
            with wave.open(str(path),'wb') as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
                audio.writeframes(b'\1\0'*24000)
        with tempfile.TemporaryDirectory() as temp, patch('fluency_video.make_speech',side_effect=fake):
            root=Path(temp)
            events,total=prepare_audio(lesson,root,'kokoro')
            self.assertEqual(calls,['Alex','Emma'])
            self.assertAlmostEqual(total,4.5)
            self.assertAlmostEqual(events[2]['start'],1.5)
            with wave.open(str(root/'narration.wav')) as audio:
                self.assertEqual(audio.getnframes(),108000)

    def test_speech_errors_do_not_promote_pending_cache(self):
        with tempfile.TemporaryDirectory() as temp, patch('fluency_video.make_speech',side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError): prepare_audio(self.lesson,Path(temp),'kokoro')
            self.assertFalse(any((Path(temp)/'speech').glob('[!-]*.wav')))

    def test_character_animation_changes_pixels_without_moving_subtitles(self):
        anim=CharacterAnimator()
        a=anim.frame(0,'Alex',.8); b=anim.frame(.7,'Alex',.8)
        self.assertIsNotNone(ImageChops.difference(a,b).getbbox())
        turn=self.lesson['turns'][3]
        a=lesson_frame(a,self.lesson,turn,.5)
        b=lesson_frame(b,self.lesson,turn,.5)
        self.assertIsNone(ImageChops.difference(a.crop((400,190,880,409)),b.crop((400,190,880,409))).getbbox())


    def test_mouth_changes_only_for_the_current_speaker(self):
        anim=CharacterAnimator()
        rest=anim.frame(0,None,0)
        talking=anim.frame(0,'Alex',1)
        self.assertIsNotNone(ImageChops.difference(rest.crop((220,263,330,332)),talking.crop((220,263,330,332))).getbbox())
        self.assertIsNone(ImageChops.difference(rest.crop((640,120,1280,720)),talking.crop((640,120,1280,720))).getbbox())

    def test_every_sample_caption_fits_safe_region(self):
        bg=background()
        for turn in self.lesson['turns']:
            self.assertEqual(lesson_frame(bg,self.lesson,turn,.5).size,(1280,720))

    def test_thumbnail_uses_real_alpha_character_with_safe_margins(self):
        art=Image.open(ASSETS/'emma-thumbnail.png')
        self.assertIn('A',art.getbands())
        self.assertEqual(art.getpixel((0,0))[3],0)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'thumbnail.png'; thumbnail(self.lesson,path)
            im=Image.open(path)
            self.assertEqual(im.size,(1280,720))
            self.assertEqual(im.getpixel((0,0)),(255,255,0))

    def test_ambiguous_upload_never_regenerates_or_reinserts(self):
        class Store:
            def load(self,key,default): return {'phase':'upload_started'}
        with patch('workflow.Store',Store), patch.dict('os.environ',{'SAVED_RUN_ID':'daily-123','DAILY_ENABLED':'true'}), patch('fluency_video.render') as render:
            with self.assertRaisesRegex(RuntimeError,'ambiguous'): daily(Path('unused'))
            render.assert_not_called()

    def test_upload_only_recovery_never_generates_a_missing_lesson(self):
        class Store:
            def load(self,key,default): return default
        with patch('workflow.Store',Store), patch.dict('os.environ',{'SAVED_RUN_ID':'daily-123','DAILY_ENABLED':'true'}), patch('fluency_video.generate_lesson') as generate:
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(RuntimeError,'No saved'): daily(Path(temp),True)
            generate.assert_not_called()


class FluencyUploadTests(unittest.TestCase):
    def setUp(self):
        self.lesson=json.loads(EXAMPLE.read_text())

    def test_prefix_is_required_in_title_description_and_first_tag(self):
        for field in ('title','description','tags'):
            bad=copy.deepcopy(self.lesson)
            bad[field]=['English'] if field=='tags' else 'Ordering Coffee'
            with self.assertRaises(ValueError): validate_lesson(bad)

    def test_upload_fields_are_explicit_and_english_education(self):
        from fluency_upload import video_body
        body=video_body(self.lesson,self.lesson['description'],'27')
        self.assertEqual(body['snippet']['defaultLanguage'],'en')
        self.assertEqual(body['snippet']['defaultAudioLanguage'],'en')
        self.assertEqual(body['snippet']['categoryId'],'27')
        self.assertEqual(body['status'],{'privacyStatus':'private','selfDeclaredMadeForKids':False,
            'license':'youtube','embeddable':True,'publicStatsViewable':True})

    def test_metadata_verification_rejects_missing_caption_or_wrong_language(self):
        from daily_publish import validate_remote
        state={'video_id':'v','story':self.lesson,'format':'english-fluency','captions_uploaded':True}
        video={'id':'v','snippet':{'channelId':'c','title':self.lesson['title'],'description':self.lesson['description'],
            'tags':self.lesson['tags'],'categoryId':'27','defaultLanguage':'en','defaultAudioLanguage':'en'},
            'status':{'uploadStatus':'processed','privacyStatus':'private','license':'youtube','selfDeclaredMadeForKids':False,
                'embeddable':True,'publicStatsViewable':True},'processingDetails':{'processingStatus':'succeeded'},
            'contentDetails':{'definition':'hd'}}
        self.assertTrue(validate_remote(video,state,'c'))
        state['captions_uploaded']=False
        with self.assertRaises(RuntimeError): validate_remote(video,state,'c')
        state['captions_uploaded']=True;video['snippet']['defaultAudioLanguage']='bn'
        with self.assertRaises(RuntimeError): validate_remote(video,state,'c')

    def test_caption_ambiguous_outcome_never_inserts_again(self):
        from unittest.mock import MagicMock
        from fluency_upload import ensure_captions
        client=MagicMock();client.captions().list().execute.return_value={'items':[]}
        client.captions().list.reset_mock()
        store=MagicMock();state={'video_id':'v','thumbnail_uploaded':True,'captions_upload_started':True}
        with tempfile.TemporaryDirectory() as temp, patch('googleapiclient.discovery.build',return_value=client), \
                patch('google.oauth2.credentials.Credentials.from_authorized_user_info'), \
                patch.dict('os.environ',{'YOUTUBE_TOKEN_JSON':'{}'}):
            with self.assertRaisesRegex(RuntimeError,'ambiguous'): ensure_captions(store,'key',state,Path(temp))
        client.captions().insert.assert_not_called()

    def test_existing_caption_track_is_reconciled_without_duplicate_insert(self):
        from unittest.mock import MagicMock
        from fluency_upload import ensure_captions
        client=MagicMock();client.captions().list().execute.return_value={'items':[{
            'id':'caption-1','snippet':{'language':'en','name':'English conversation','status':'serving'}}]}
        client.captions().list.reset_mock()
        store=MagicMock();state={'video_id':'v','thumbnail_uploaded':True,'captions_upload_started':True}
        with tempfile.TemporaryDirectory() as temp, patch('googleapiclient.discovery.build',return_value=client), \
                patch('google.oauth2.credentials.Credentials.from_authorized_user_info'), \
                patch.dict('os.environ',{'YOUTUBE_TOKEN_JSON':'{}'}):
            ensure_captions(store,'key',state,Path(temp))
        self.assertTrue(state['captions_uploaded']);client.captions().insert.assert_not_called()
        for call in client.captions().list.call_args_list:
            self.assertEqual(call.kwargs.get('videoId'),'v')

    def test_each_lesson_renders_a_topic_specific_thumbnail(self):
        from PIL import ImageChops
        other=copy.deepcopy(self.lesson);other['headline']=['PHONE','CALLS','IN ENGLISH']
        with tempfile.TemporaryDirectory() as temp:
            p,q=Path(temp)/'a.png',Path(temp)/'b.png'
            thumbnail(self.lesson,p);thumbnail(other,q)
            self.assertIsNotNone(ImageChops.difference(Image.open(p),Image.open(q)).getbbox())

    def test_external_daily_invocation_is_disabled_by_default(self):
        with patch.dict('os.environ', {'DAILY_ENABLED':'false'}):
            with self.assertRaisesRegex(RuntimeError, 'disabled'): daily(Path('unused'))

    def test_repeated_packaging_stops_after_bounded_attempts(self):
        from fluency_lessons import PREFIX, TOPICS
        calls=[]
        def ask(prompt,schema):
            calls.append(prompt)
            return {'angle':'Ordering Coffee Politely','headline':['ORDER','COFFEE','POLITELY']}
        history=[{'topic_id':topic[0], 'title':PREFIX+' | Ordering Coffee Politely | A2 Practice',
                  'headline':['ORDER','COFFEE','POLITELY']} for topic in TOPICS]
        with self.assertRaisesRegex(ValueError,'Duplicate title'):
            generate_lesson('2026-11-10',history,ask)
        self.assertEqual(len(calls),3)

if __name__=='__main__': unittest.main()
