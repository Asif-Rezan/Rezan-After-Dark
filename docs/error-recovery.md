# Generation error recovery

The storyboard failure in Actions run 40261314201 was caused by a model response
outside the accepted 5–30 word range. It was a content validation failure, not a
narration or OAuth failure.

The decoder schema now constrains prompt word counts before generation.
Storyboard responses get up to three attempts. Valid shots survive partial
responses and restarts. If a shot still has no valid prompt, a short illustration
prompt is derived from its measured spoken window and recorded as an advisory.
Missing source narration and mismatched source hashes remain errors. The final
validation report lists storyboard advisories for review.

Image and narration outputs and individual video clips use temporary files and
atomic replacement, so failed attempts cannot leave reusable partial outputs.
Temporary transport errors and HTTP 408/429/5xx failures get three attempts with
backoff. Configuration errors, safety rejections and invalid outputs propagate.
Retries log operation names and exception types without private prompt text.

CLIP similarity is optional quality guidance: a model-loading/scoring failure is
recorded as an advisory. It does not disable the image generator's safety checker.
A failed shot does not prevent other shots in its shard from being generated;
the shard then fails explicitly with the unfinished shot indices. The existing
encrypted shard backup workflow preserves available completed files on failure.

Drive already has bounded retries, checksum verification and resumable upload
recovery. YouTube insertion is deliberately excluded from generic retries to
prevent duplicate uploads. Missing credentials, exhausted storage, failed media
validation, persistent image safety rejection, and unavailable required models
can still require intervention. Exception handling cannot guarantee completion
when required inputs or services remain unavailable.

Local checkpoints resume on the same filesystem. They do not survive disposal of
a GitHub runner unless separately saved. The failed preparation job did not reach
its Drive save step; rerunning that old commit would repeat the old behavior.
Start a new generation from the fixed commit. Tests run on main pushes and pull
requests, including failure-injection coverage; tests do not download/run full
production models or perform a live YouTube upload.
