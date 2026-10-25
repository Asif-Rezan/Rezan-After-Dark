# Rezan After Dark: horror story automation

Adapted from `Asif-Rezan/youtube-dark-psychology-automation` at commit `4556280dad46cbfbec6e4f49ae75224a6155b974`.

Creates one original English fictional horror video daily for **Rezan After Dark** (`UCwNhdtzL9XZMIAMXm81UCbw`), with private uploads at **06:00 Asia/Dhaka**. The reference workflow is preserved: twenty chapters, 15–20 minute measured narration, twenty CPU image workers, a new image every ten seconds, Kokoro voice, captions, original music, readable thumbnails, upload verification and recoverable private Drive checkpoints.

Script generation uses **Qwen/Qwen3-4B-Instruct-2507** from Hugging Face on the
runner's CPU. The pinned Unsloth Q4_K_M GGUF conversion of that base model is
about 2.5 GB and is verified against its SHA-256 before loading. Story outlines,
narration, duration revisions and shot prompts all use this same local model.
No text-generation API key, hosted text provider, or provider fallback is used.
Narration, captions, images and music keep their existing CPU backends.

## Horror stories and visuals

[horror_topics.json](horror_topics.json) rotates twelve original premises covering haunted houses, night shifts, forests, ghost trains, uncanny mirrors and other supernatural mysteries. Recent topics, titles and outlines are checked to reduce repetition. Each episode introduces original adult characters, consistent supernatural rules, escalating suspense and a final twist. Metadata explicitly labels the story as fiction; it never claims invented events are true.

Illustrations follow the actual narration windows, with recurring character descriptions, eerie cinematic light and non-graphic scenes. SSD-1B uses CPU at 1024×576, 16 inference steps, retaining the model safety checker. The thumbnail keeps the reference's readable 1280×720 layout with Rezan After Dark branding. Uploads use YouTube's Entertainment category.

## Required isolated setup

Use a **dedicated, previously unused Google Cloud project and Desktop OAuth client** for this repository. The approved project is `project-e02d8c94-35af-4ce3-ac5` (Rezan After Dark), project number `376050043753`, organization `919363005250`, owned by `asifrezan.office@gmail.com`. Enable YouTube Data API v3 and Google Drive API. Complete Google consent using `asifrezanit@gmail.com` for the Rezan After Dark channel and a separate, owner-only Drive folder in `asifrezanit@gmail.com`. Set these GitHub Actions secrets in **this repository only**:

- `YOUTUBE_TOKEN_JSON`: OAuth authorized-user JSON for the Rezan After Dark channel. Required scopes: `youtube.upload` and `youtube.force-ssl`.
- `DRIVE_TOKEN_JSON`: OAuth authorized-user JSON with Drive file access for the new app.
- `DRIVE_FOLDER_ID`: ID of the dedicated Rezan After Dark folder created by `setup_drive_folder.py` with the new Drive OAuth token. This is mandatory. The folder carries a channel-specific marker; the code rejects the source project's folder.

The expected channel ID is fixed in the workflows and upload checks. If the token belongs to a different channel, readiness and upload stop. Keep all credentials out of the repository and Actions logs.

The daily workflow is `.github/workflows/daily-upload.yml`. Run **Google OAuth check** first, then manually dispatch one daily workflow to verify a private HD upload before relying on the schedule. `cleanup_run` retries verification and cleanup of an already completed run without uploading again. A failed or ambiguous upload retains its Drive assets for reconciliation.

The source repository and its secrets, Drive folder, OAuth project, YouTube token, and schedule are independent of this repository. This repository's setup must be completed before its daily workflow can run successfully.

The OAuth app was configured in **Production** on October 19, 2026, before authorizing tokens. Keep it in Production to avoid the testing-mode seven-day refresh-token expiry. Google can still revoke authorization independently.

## Local checks

`python -m unittest -v` runs component and safety checks. Full video generation and upload require the cloud credentials and a live private test.

## Story duration recovery

Fresh daily stories keep the twenty-chapter fictional horror outline and target 110
spoken words per chapter, plus the closing CTA. Set `STORY_CHAPTER_WORD_TARGET`
between 95 and 125 to tune the initial request. Actual Kokoro audio duration,
not word count, controls acceptance: 900–1200 seconds is required, with 1020–1140
seconds preferred.

Preparation measures every chapter before making images. A slight overrun uses
at most a 4% FFmpeg tempo correction. Larger overruns use Qwen to shorten
selected chapters; underruns use Qwen to add relevant suspense, sensory details and character actions.
At most three correction attempts run. Changed chapters alone are resynthesized,
then word timestamps, captions, and every ten-second visual window are rebuilt.
Audio and transcript caches are tied to content hashes so changed text cannot
reuse stale timing. The corrected story, narration, captions, storyboard source
hash, and validated duration are checkpointed together before the twenty image
workers start. Recovery failure stops before rendering or upload. The prepare job
has a 150-minute limit to accommodate bounded Qwen and CPU narration retries.

## Recovering a Drive cleanup timeout

Open **Resume verified private video cleanup** in GitHub Actions and enter the original
`daily-<run ID>`, such as `daily-38644830219`. This workflow runs only
`daily_publish.py`: it rechecks the existing video's channel, exact metadata,
private visibility, successful HD processing, and confirmed thumbnail before
deleting any run media. It never generates or uploads a video.

Small JSON checkpoints use bounded backoff for timeouts, connection errors, rate
limits, and temporary server errors. Retries keep one Drive file ID. A new file
uses a preallocated ID, so a timed-out create cannot create duplicate checkpoints;
an ambiguous result is accepted only if its saved JSON matches. Cleanup saves an
ID manifest before deletion and checkpoints each ten deletions and at completion.
If interrupted, it resumes from the manifest and treats previously deleted IDs
as complete after Drive returns 404. Essential state and story files are preserved.

### Drive timeouts and upload-only shard recovery

Drive reads and uploads retry temporary failures with bounded exponential backoff
(5 failures per operation; 2, 4, 8, 16 second delays). Media uses 8 MiB resumable
chunks, a preallocated file ID, size/MD5 verification and an on-disk ID journal.
An ambiguous response is reconciled against that same ID. Matching files are
skipped; duplicates, privacy failures and identity conflicts stop the operation.
Small JSON checkpoints use the same verified, idempotent write path. YouTube
insertion is deliberately outside these retries and keeps its existing guards.

Daily image jobs retry upload-only once on the same runner if saving fails. If
that fails, completed/partial work and reserved IDs are encrypted with AES-GCM
before a GitHub artifact is saved for 14 days. The encryption key is derived from
the existing Drive refresh token; keep that token available until recovery is
finished. Replacing it makes old backups unreadable. No plain media, credentials,
or resumable session URLs are placed in the artifact. A hard runner termination
or backup service failure can still prevent preservation.

Use **Recover original shard uploads only** with the original numeric GitHub run
ID and shard number. If available, supply the original artifact name
`private-shard-<shard>-<attempt>`. It checks the original prepared state and exact
story/plan/duration, then reports missing assets before writing anything. With a
complete local/Drive bundle it uploads only missing/different assets and writes
a verified per-shard manifest. It never generates images, narration or videos,
never calls YouTube, and never deletes media. A request can also be triggered by
committing `.github/recovery-request.json` on main, e.g.
`{"run_id":"38730964321","shard":9,"backup_artifact":""}`.

If the original runner is gone and Drive or encrypted backups do not contain all
required assets, recovery stops. Do not use **Re-run failed jobs** to retry only
an upload: the old workflow reruns generation on a fresh runner and uses its old
code revision. Successful upload-only shard recovery does not automatically
assemble or upload a video; inspect all shard results and existing YouTube state
before continuing those stages.

## Horror audio atmosphere

Original MusicRNN notes are voiced as sparse A-minor chimes over slowly shifting low drones and subtle pulses at 52 BPM. Soft fades and restrained levels avoid abrupt stingers; the existing loudness normalization and narration ducking keep speech clear. No music-generation API or additional key is needed.

Narration uses Kokoro `am_michael` at speed `0.82`, with a gentle two-semitone pitch reduction. Pitch processing preserves duration; changed voice settings invalidate cached audio and captions. Measured narration must be 15–20 minutes, targeting 18 minutes, with images changing every 10 seconds (the final image may be shorter).

## Local Qwen script generation

`story_cpu.py` pins the base model identity to `Qwen/Qwen3-4B-Instruct-2507`.
Its CPU artifact is
[`unsloth/Qwen3-4B-Instruct-2507-GGUF`](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF),
file `Qwen3-4B-Instruct-2507-Q4_K_M.gguf`, revision
`a06e946bb6b655725eafa393f4a9745d460374c9`, SHA-256
`3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597`.
This is a compressed conversion of the requested model, not a different fine-tune.

Install `pip install -r requirements.txt -r requirements-story.txt` for text
inference. The latter installs the pinned llama.cpp Python CPU wheel from the
maintainer's official wheel index. Public model downloads explicitly use no
Hugging Face token. No external text API is contacted. The model is reused for
all requests within a process and runs with a 16,384-token context and at most
four CPU threads by default. Recent-story context is capped to keep prompts
within that budget. The model download is about 2.5 GB on the first use per
runner, and CPU inference takes longer than a hosted API.

JSON generation uses llama.cpp's schema grammar and then validates locally
against the original schema. At most three attempts are allowed for incomplete
or invalid JSON; download/checksum retries are also bounded. Context overflow
and runtime failures stop with privacy-safe errors. Generated scripts and raw
runtime error messages are not placed in public logs.

The daily job installs the small text runtime and runs `story_check.py` before
installing video dependencies. **Qwen CPU script generation check** exercises
that same runtime without secrets. **Free strict story generation check**
generates and validates a complete twenty-chapter story using private recent
history, then saves it privately to Drive. Google OAuth checks now test only
Google access and do not require a text API credential.

To verify a code change, run the Qwen readiness check, then the complete story
check, then launch **Daily horror story publishing** on `main` with `cleanup_run`
empty. Launch a new run; rerunning an old failed run uses its old commit.
The previous hosted text client, authorization workflow and hosted audio
backends have been removed. The old text API secret is unused and unnecessary.

Qwen narration batches are checked using actual word counts, not the model's
claim about length. At the default 110-word target, each chapter must contain
100–130 words before audio generation. Short/long batches receive their measured
counts and prior draft for a bounded rewrite, with at most three attempts.
Persistent length failure stops before producing video assets. Measured audio
still controls the final 15–20 minute acceptance check.
