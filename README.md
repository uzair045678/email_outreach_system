# outreach

A local, scheduled job-outreach pipeline for XR/AR/VR/Unity/Unreal work.

It discovers target companies, checks that they actually do XR work, finds a
contact, drafts a short personalized email with a **local** LLM, and queues it
for your manual approval in an Excel workbook. You approve in Excel; a separate
`send` command mails the approved, due messages through Himalaya at the
recipient's local time, then watches the mailbox for bounces and replies.

Everything runs on your machine. Nothing is sent until you approve it.

---

## What it does, in one run of each command

| command            | what it does |
|--------------------|--------------|
| `research`         | search (Tavily/Brave/Serper) â†’ fit-check â†’ find contact â†’ draft â†’ queue as `pending_review` |
| `send`             | send `approved` rows that are due, respecting warm-up caps, hourly/day caps, pause flag |
| `bounces`          | read inbox + spam, parse delivery-status notices, mark hard/soft bounces |
| `repair`           | for a hard-bounced row, find + verify the next best contact and re-draft the greeting |
| `replies`          | classify inbound replies (positive / negative / out-of-office / other) |
| `followups`        | draft one follow-up for contacts that stayed silent |
| `summary`          | write today's numbers to the Summary sheet (optionally email to you) |
| `freehire`         | poll freehire.me for new matching jobs; alert **you** only (never a company) |
| `import-contacted` | load your existing contacted list so it is never re-contacted |
| `doctor`           | check deps, config, keys, himalaya, models, GPU |
| `tune-gpu`         | probe `-ngl` values and report VRAM use |

Global flags: `--dry-run` (do everything except send/write) and `--limit N`.

---

## Requirements

- Windows 10/11, Python 3.10â€“3.11 (a 3.11 venv is already set up in `.venv`).
- [Himalaya](https://github.com/pimalaya/himalaya) v2 on `PATH`, with a mail
  account named `gmail` configured for SMTP send + IMAP read.
- `llama-server.exe` (the LM Studio llama.cpp bundle is used by default) and the
  downloaded GGUF models listed in `config.yaml`.
- An NVIDIA GPU with CUDA (default config targets a 4 GB GPU, loading one model
  at a time).

---

## Setup

1. **Activate the venv** (already created):
   ```powershell
   F:\_PROJECTS\EMails\.venv\Scripts\Activate.ps1
   ```
   If you need to recreate it:
   ```powershell
   py -3.11 -m venv F:\_PROJECTS\EMails\.venv
   F:\_PROJECTS\EMails\.venv\Scripts\python.exe -m pip install -r F:\_PROJECTS\EMails\requirements.txt
   ```

2. **Fill `profile.yaml`** â€” every `TODO` must be replaced. This is your data;
   the pipeline never invents it. `doctor` fails while any `TODO` remains.
   Two text fields shape every email: `base_story` (your self-introduction
   pitch, reused in your own voice) and `apply_instructions` (how each email
   must be written). The one-line "headline" (e.g. "Unity Developer",
   "XR/VR Developer") is **not** stored in the profile â€” it's generated per
   company from their stack + your real skills, and stored per row so you can
   review each one.

3. **Fill `.env`** (copy from `.env.example`). At minimum one search key; the
   fallback LLM keys are optional. Gmail credentials live in Himalaya's own
   config, never in `.env`.

4. **Check Himalaya**:
   ```powershell
   himalaya account list
   himalaya mailbox list
   ```
   The account must be named `gmail` and expose the folders in `config.yaml`
   (`Inbox`, `[Gmail]/Sent Mail`, `[Gmail]/Spam`, ...).

5. **Verify everything**:
   ```powershell
   F:\_PROJECTS\EMails\.venv\Scripts\python.exe -m outreach doctor
   ```
   Fix any `FAIL`. `fallback LLM keys` and `nvidia-smi` may be `WARN`.

6. **(Optional) import your existing contacts** so they are excluded:
   ```powershell
   python -m outreach import-contacted path\to\contacted.xlsx
   ```

---

## Daily workflow

1. `research` runs nightly at 10:30 PM and fills **Queue** rows with
   `status = pending_review` for the next day. When it finishes, Windows shows
   an approval reminder. If the computer was off, Task Scheduler runs the
   missed research job after the next sign-in.
2. Open `F:\_PROJECTS\EMails\outreach.xlsx` and, on each row you like, set
   **approve = yes** and **status = approved**. Edit subject/body freely.
3. `send` (every 30 min) mails approved rows whose send time has arrived and
   that pass the warm-up and daily caps.
4. `bounces` / `replies` run hourly and update row statuses.
5. Approve follow-ups the same way: `followups` queues them as
   `pending_review` with `template_variant = followup`.

**Only rows with `status = approved` and `approve` set are ever sent.**

### Pausing
Sending pauses automatically above `sending.hard_bounce_rate_pause`. To pause or
resume by hand, create/delete `F:\_PROJECTS\EMails\cache\pause_sending.flag`.

---

## Queue statuses

`pending_review` â†’ `approved` â†’ `scheduled` â†’ `sending` â†’ `sent` â†’ `delivered_assumed`

Side branches: `bounced_soft`, `bounced_hard`, `needs_new_address` â†’ (repair) â†’
`pending_review`/`approved`, `gave_up`, `replied`, `auto_reply`, `followup_pending`,
`followup_sent`, `suppressed`, `skipped`, `error`.

---

## freehire job watcher

Polls the public, keyless freehire.me API for jobs matching your keywords.
State lives in `cache/freehire/jobs.db` (deduped by `public_slug`) and mirrors to
the **Freehire** sheet in `outreach.xlsx`. It only alerts **you**; it never
emails a company.

New jobs wait quietly and land in **one digest email per day** at the first
slot in `freehire.digest_times` (default `08:00, 16:00, 20:00, 22:00` local
time from `profile.yaml`) that the hourly cycle actually hits - the times are
fallbacks, not four sends. A window only fires if a cycle runs inside
`[slot, slot+1h)`; if the laptop was asleep, that window is skipped and the
next one is used. An empty window does not consume the day. The digest groups
jobs **priority / medium / low** by title keywords (`freehire.categories`), one
Windows toast when it goes out, and each job is listed in exactly one digest,
ever. Quiet hours 23:00-08:00 defer the digest. The first run baselines
existing jobs silently, so you are not flooded.

| command | what it does |
|---------|--------------|
| `freehire` / `freehire run` | one watch cycle (the scheduled task calls this hourly) |
| `freehire applied <slug>` | mark applied -> job leaves the queue, no digest listing |
| `freehire skip <slug>`     | ignore the job -> same effect |
| `freehire draft <slug>`    | find the company contact, queue an "I already applied" heads-up email as `pending_review` |
| `freehire list`            | show tracked jobs and their statuses |

### Marking a job as applied (either way works - dual input)

1. `python -m outreach freehire applied <slug>` - every digest entry contains
   the exact command for that job, or
2. set `applied = y` on the Freehire sheet in Excel (dropdown); the next cycle
   reads it back and the job stops appearing in digests.

Applications made on freehire's own site are not visible to the API (no
endpoint exists), so record those with one of the two ways above.

### Drafting an application heads-up email

`python -m outreach freehire draft <slug>` searches for the company's official
site, finds a contact email with the normal research stack, and queues a
`pending_review` row: *"I have applied for {title} at {company} ... here is my
information and CV"*. **It is never sent by itself** - you approve it in
`outreach.xlsx` like any other draft (or fill `to_email` yourself if no contact
was found).

### Tuning keywords and digest categories

`config.yaml -> freehire.keywords` - what gets searched (titles only,
`q_fields: title`; description matching is far too noisy on freehire).
`freehire.categories` - which title keywords place a job in the priority /
medium / low digest sections (highest tier wins, no match = low). To review
relevance before changing anything:

```powershell
python -m outreach --dry-run freehire run
```

That prints per-keyword totals with sample titles, any filter the API dropped
(`IGNORED_PARAMS=...`), and - inside a slot window - the digest that would be
sent. Note: `open_within_days` filters by freehire's crawl date (`created_at`),
not the original `posted_at`.

---

## Scheduled tasks

Register everything with one command (elevated PowerShell):
```powershell
powershell -ExecutionPolicy Bypass -File F:\_PROJECTS\EMails\install_tasks.ps1
```
This creates `Outreach-Research`, `-Send`, `-Bounces`, `-Replies`,
`-Followups`, `-Repair`, `-Summary`, `-Freehire` (freehire polls hourly).
`Outreach-Research` runs at 10:30 PM and has catch-up enabled for missed runs.
Remove them with `uninstall_tasks.ps1`. The pipeline's file locks make frequent
triggers safe.

---

## Files and folders

- `outreach.xlsx` â€” the workbook: **Queue**, **Contacted**, **BadAddresses**,
  **Suppressed**, **Summary**, **Log**, **Freehire**.
- `profile.yaml` â€” your details. `config.yaml` â€” non-secret settings.
- `.env` â€” secret API keys (git-ignored).
- `cache/` â€” HTML + search caches, processed-mail ids, `pause_sending.flag`,
  `freehire/jobs.db` (watched jobs + reminder state).
- `logs/` â€” rotating logs per command.
- `backups/` â€” rolling workbook backups (14 kept).
- `pending_updates.jsonl` â€” journal used if Excel has the workbook locked.

---

## Troubleshooting

- **`llama-server` exits with `0xC0000135`**: the CUDA DLLs are missing from
  `PATH`. `config.paths.llama_dll_dirs` handles this automatically; if you moved
  LM Studio, update that path.
- **`-fa` error from llama-server**: this build requires a value (`-fa on`),
  which `config.yaml` already sets.
- **Duplicate messages in Sent**: Gmail already stores SMTP-sent mail, so
  `himalaya.save_sent_copy` is `false`. Do not set it to `true` with Gmail.
- **Workbook is locked by Excel**: changes are journaled to
  `pending_updates.jsonl` and replayed the next time the workbook opens.
- **No drafts produced**: check `doctor` and the per-command log; a missing
  search key or model path is the usual cause.

