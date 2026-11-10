# English fluency conversation videos

Migration in progress from the preserved horror pipeline.

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
