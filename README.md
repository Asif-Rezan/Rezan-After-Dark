# English fluency conversation videos

Creates original A2 English lessons with Alex (male) and Emma (female): a white background, two 3D storybook characters, centered timed subtitles, listen/repeat/role-play sections, Python character animation, and a separately rendered white thumbnail for every video. Existing Google credentials and expected channel identity stay isolated to this repository.

## Video and thumbnail format

- 1280×720 video at 12 fps by default (24 fps is supported locally).
- Video characters include complete outer arms and elbows, white side margins and motion-safe placement. A regression check sweeps both speakers through extreme poses and rejects overlap with the subtitle mask.
- Python/Pillow character breathing, torso sway, speech-energy-driven nods and three mouth poses (closed, slightly open, open). Characters move independently; subtitles stay fixed. This is 2D puppet animation of 3D-style artwork, approximate audio-reactive mouth animation rather than phoneme-accurate lip-sync or a rigged 3D model.
- Kokoro CPU voices: Alex `am_michael`, Emma `af_heart`, speed `0.9`, neutral pitch. No horror pitch filter, atmosphere or background music.
- Daily lessons contain three original conversations, sentence repetition with speaking pauses and role play. Measured audio must be 8–20 minutes; generation stops outside that range rather than padding the lesson. The checked-in short sample uses a separate 60–90 second duration gate.
- Qwen generates a fresh situation and three thumbnail headline lines. New runs reserve the title, headline and dialogue fingerprints before rendering so later runs can reject repeats. Each thumbnail places Alex and Emma on opposite sides of a white canvas, with the topic hook centered. Both characters change their expression and pose between consecutive new episodes, rotating through 16 pairings.

## Title and upload details

The title and description start with `Learn English While You Sleep | …`, and the first tag in the upload request is exactly `Learn English While You Sleep`. YouTube may return different tags. Verification attempts one update and records any remaining difference as a warning. Drive cleanup proceeds after the private HD upload passes the other verification checks. The script describes actual listening, repetition and speaking practice without promising learning during sleep.

Example title: **Learn English While You Sleep | Ordering Coffee | A2 Practice**.

Uploads explicitly set English metadata language (`en`), English audio language (`en`), Education category (`27`), private visibility, standard YouTube license, embedding enabled, public statistics enabled and not made for kids (the lessons target adults). English SRT captions are uploaded as a separate non-draft track as well as burned into the video. The final verification checks these details, exact title and description, correct channel, successful HD processing, thumbnail confirmation and serving captions before cleanup. Location, recording date, paid promotions and other inapplicable fields are not invented. End screens/cards and comment moderation are not exposed by this uploader and retain Studio settings.

## Local preview

Install FFmpeg and Python 3.12+:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-fluency-preview.txt
python fluency_video.py sample --backend edge --output output/animated-sample
```

This optional preview uses Edge Jenny/Guy over the network and needs no API key. Production stays on Kokoro CPU. Outputs include `video.mp4`, `thumbnail.jpg`, `preview.jpg`, `captions.srt`, `timeline.json`, `lesson.json` and `validation.json`. Repeated lines reuse speaker-specific content-addressed speech files.

For production narration, also install `requirements.txt`, CPU PyTorch, `requirements-fluency-voice.txt` and the system `espeak-ng` dependency. Run `python fluency_voice_check.py` to test both voices. Qwen additionally needs `requirements-story.txt` (the pinned CPU wheel is intended for the Linux runner):

```sh
python fluency_video.py generate --output output/lesson
# Review/edit the generated lesson before local rendering.
python fluency_video.py render --lesson output/lesson/lesson.json --output output/lesson
python -m unittest -v
```

## GitHub daily workflow and recovery

`.github/workflows/daily-upload.yml` starts at **00:00 Asia/Dhaka**, retaining the previous cron time. This is the generation start, not a guaranteed completion time. Videos remain private. The daily job uses the same dedicated Drive folder and channel `UCwNhdtzL9XZMIAMXm81UCbw`. Required repository secrets remain `YOUTUBE_TOKEN_JSON`, `DRIVE_TOKEN_JSON`, `DRIVE_FOLDER_ID`; YouTube authorization needs `youtube.upload` and `youtube.force-ssl`. See the preserved setup notes in [docs/horror-version.md](docs/horror-version.md).

The scheduled workflow explicitly sets `DAILY_ENABLED=true`; direct `daily` invocation is disabled unless this flag is set. The example external cron remains disabled by default.

A fresh run saves the validated lesson privately before rendering, then saves completed media and a hash manifest before inserting a YouTube video. The workflow has separate jobs: Prepare → parallel Alex/Emma voice workers (with Thumbnail alongside) → Audio timing → sixteen parallel animation/render workers → Assemble → Private YouTube upload → Verify → Drive cleanup. Video chunks contain no audio; assembly muxes the full narration once to prevent chunk-boundary audio gaps. All render workers use global frame indices, so subtitles, mouth motion, body motion and progress stay continuous. Minimal voice dependencies and pip caching reduce repeated setup. Runtime improvement depends on runner availability and Drive transfer overhead; no fixed speed multiplier is promised. Metadata/caption/thumbnail failures retain assets.

- Fresh dispatch: leave both inputs blank.
- `resume_run= daily-<original GitHub run ID>`: restore and verify completed media, then upload only. A missing asset stops recovery; it never silently regenerates in this mode. If the video already exists, reconcile thumbnail/caption completion and run verification instead of inserting again.
- `cleanup_run= daily-<original GitHub run ID>`: verify an already completed upload and resume tagged Drive cleanup. The cleanup stage also checks older daily runs with tagged temporary media and cleans those whose existing uploads pass verification. If captions are still incomplete, use `resume_run` instead.
- An ambiguous `upload_started` state blocks a second video insertion. An ambiguous caption insertion is reconciled by listing the fixed English track name before considering another insertion.
- Saved audio and captions use exact measured timings. Video, thumbnail and SRT SHA-256 values are checked during upload-only restoration.
- Parallel voice and render jobs write separate private Drive files and manifests; no plaintext media is exposed through GitHub artifacts. Manifests bind speaker/chunks to the lesson, timing, frame rate, exact frame ranges and SHA-256 hashes. Missing or foreign assets stop assembly/upload. Disposable voice WAVs and all sixteen render chunks are included in verified cleanup.
- `--verify-only` persists verification proof without deleting media; the separate cleanup job runs only after verification success and rechecks the private video before deletion.
- The local speech cache survives retries on the same machine. A lost cloud runner cannot recover partial local narration; a normal rerun may regenerate it from the saved lesson. Upload-only recovery requires a fully saved media bundle.

The **English fluency production check** workflow runs a real Qwen lesson generation, both Kokoro voices and a complete animated sample render/FFmpeg decode without Google credentials. The component check runs all unit tests.

Workflow runs retain the job graph and code revision they started with. Updating this graph does not accelerate or restart an already active serial run. The next fresh run uses the parallel graph; the shared concurrency group and `cancel-in-progress: false` preserve the active production run.

Historical horror modules and manual workflows are preserved for old-run recovery. They are not used by the daily English workflow. The legacy automatic strict horror generation trigger is now manual-only.

No channel name, banner, external Google credentials or public visibility is changed by this migration.

## Restore the previous horror version

The exact pre-migration commit is [`ff434287f401a72cdcc02de9fa56cd1a8e3414a5`](https://github.com/Asif-Rezan/Rezan-After-Dark/commit/ff434287f401a72cdcc02de9fa56cd1a8e3414a5).

Permanent annotated GitHub tag: [`horror-before-english-fluency-2026-11-10`](https://github.com/Asif-Rezan/Rezan-After-Dark/tree/horror-before-english-fluency-2026-11-10). The original README is preserved in [docs/horror-version.md](docs/horror-version.md).

Inspect the old version without changing main:

```sh
git fetch origin --tags
git switch -c restore/horror horror-before-english-fluency-2026-11-10
```

Restore tracked project files on main with a new auditable commit (first commit/stash local changes):

```sh
git switch main
git pull --ff-only
git restore --source=horror-before-english-fluency-2026-11-10 --staged --worktree -- .
git commit -m "Restore preserved horror pipeline"
git push origin main
```

This restores tracked files, including workflow schedules. It does not restore external credentials, Google account settings, Drive data or channel branding. No force push or history deletion is needed.
