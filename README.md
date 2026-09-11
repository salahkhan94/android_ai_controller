# Android AI Controller

Current scope: **written text prompts only**. Phase 6 (photo/image understanding
and photo-targeted comments or likes) is cancelled. Screenshots are retained for
UI diagnostics and verification, not for generating comments about images.

Title-only prompt cards without an exposed answer are skipped and recorded in
`profile.json` under `unsupported_prompt_descriptions`. Only written title/answer
pairs are offered to the comment generator.

## Run the complete workflow

Start Genymotion and Appium, open the current Hinge Discover profile, and run:

```bash
.venv/bin/python run.py
```

Your API key is loaded from the project `.env`. The runner:

1. Scans the current profile's written prompts and generates three comment options.
2. Shows the recommended prompt title, their response, and the exact proposed comment.
3. Automatically selects the recommended candidate and prints the full prompt and comment.
   Use `--manual` to restore per-comment approval.
4. Reconnects to the emulator, rechecks the profile, prepares the approved text,
   verifies it, and attempts one standard like/comment submission.
5. Records the outcome and continues when Hinge advances to another profile. The previous target is never retried.

Generated comments must not contain em dashes. The generation instructions forbid
them and validation rejects any draft set that still contains one. Older drafts
with em dashes must be regenerated before preparation.

The runner pastes once, then verifies the exact field text once immediately before
submission. Clipboard copy/restore notifications are not additional paste actions.
No clipboard writes occur after sending. The standalone preparation command also
performs its own readback because it may be used independently of the runner.

Scanning confirms boundaries with one unchanged swipe and a stationary XML read.
Preparation rechecks the prompts, then searches upward from the bottom instead
of returning to the top first. The initial scan still verifies return-to-top
continuity, so some bidirectional scrolling is intentional.

Automatic sending is the default. The terminal prints the name, written prompts,
responses, candidate comments and chosen comment. For up to three written prompts,
candidates must cover each one. Press Ctrl+C to stop; an already-started send
cannot be undone. `--manual` restores interactive approval and cancellation.

Use `--max-profiles 1` for one profile or another positive limit. By default the
runner continues until quota, an error, or interruption. Duplicate attempts remain
blocked. Existing composers must be closed before starting. Keep the emulator
untouched while it operates. No purchases or Roses are sent.

All artifacts and the shared duplicate-send ledger live under the project's
`captures/`, even when launched from a different working directory. Each invocation
adds a `run_<id>/run.json` with its approval and links to phase artifacts.

Use `--model`, `--tone`, `--max-chars`, `--about-me`, or `--max-scrolls` to adjust
generation/scanning; `--help` lists the options. Exit code 0 means cancelled or
UI-confirmed success, 2 means uncertain submission, and 1 means stopped on an error.

On this Genymotion device, keep **Show virtual keyboard** enabled when using a
physical keyboard. Suppressing it caused Hinge to scroll the comment box away
and lose input focus. The working setting is already enabled; on a new emulator:

```bash
adb -s 127.0.0.1:6555 shell settings put secure show_ime_with_hard_keyboard 1
```

Preparation errors print an evidence directory. Its `preparation.json` includes
the failed stage and `failure_capture` screenshot/XML. A focus-loss error stops
before typing; do not bypass target checks to continue.

Phase 1 captures the current Hinge screen for inspection. `observation.py` is
separate from future extraction, model reasoning, and action execution.
`android_tester.py` remains the original connection smoke test.

## Capture

Use your Python environment with the Appium client installed, or set one up:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python capture_profile.py
```

Start Genymotion and Appium first. Open a profile manually, make a written prompt
visible, then press Enter in the terminal. The tool does not launch Hinge, tap,
scroll, type, or send. Use `--now` to capture immediately. Use `--help` for server,
device, package, and output overrides.

Each unique folder in `captures/` contains:

- `screen.png`: screenshot.
- `hierarchy.xml` and `hierarchy_after.xml`: XML reads bracketing the screenshot.
- `metadata.json`: timestamps, package/activity, window and screenshot dimensions,
  consistency, observation ID, and schema version. Written last on completion.
- `nodes.json`: raw XML attributes and parent relationships.
- `inspection.txt`: text, accessibility labels, and clickable node inventory.

Exit code 2 means the screen/tree changed; recapture before downstream use.
Unchanged XML cannot prove visual stability. Screen classification is manual.
Compare the screenshot with the inventory to establish whether prompt titles
and responses are exposed. Inspect bounds and parent relationships to associate
controls. No Hinge selectors or like-button classifications are assumed.
Node IDs are observation-local, not reusable selectors. Scroll manually and
capture again for additional sections.

Default captures are Git-ignored because they contain private profile content.
Keep custom output locations outside version control too.

## Print profile prompts (XML only)

With a Hinge Discover profile open, run:

```bash
.venv/bin/python print_profile_prompts.py
```

The reader moves to the top, scans down with overlapping vertical swipes, and
prints each distinct title/response pair once. It takes no screenshots and
does not like, skip, or send. It leaves the profile at the bottom.
Use `--visible-only` for the current XML without scrolling, or `--max-scrolls 40`
to increase the default limit of 30 gestures per direction.

Keep the emulator untouched during reading. Two swipes with unchanged app XML
content/bounds are treated as a boundary; this is a heuristic, not proof of
completeness if the app stalls. A changed/missing Skip label stops the scan,
but names are not unique profile identifiers. Only the observed English
`Prompt: … . Answer: …` accessibility format is supported. Content absent from
XML cannot be printed. Identical title/response pairs are deduplicated.

## Phase 2: structured profile items

```bash
.venv/bin/python extract_profile.py
```

This XML-only scan writes `captures/profile_<id>/profile.json` plus the XML for
each observation. It returns to the profile top to compare its content with the
start of the scan. Keep the emulator untouched throughout scanning.

Each item contains a scan-local ID, type, title, response, and observation history.
Each observation includes the raw accessibility description, XML node references,
card bounds, and associated like control's label, bounds, and state. Association
is restricted to equal-bounds card wrappers in the observed Hinge layout. Missing
buttons and ambiguous containers are retained as explicit statuses, never resolved
by selecting an unrelated global button.

`control_visible_in_observation` describes historical UI evidence. It is not
permission or readiness to click now: the profile has since scrolled. Every target
requires fresh resolution before any future action. Clipping and overlay occlusion
cannot be ruled out by XML bounds alone. No action execution or LLM integration is
part of Phase 2.

The report records heuristic scroll boundaries and continuity checks explicitly.
Skip-label and return-to-top content matches do not provide a unique account ID;
they cannot detect every transient switch or same-content profile. Unknown layouts
may require new association rules. Failures leave a report marked `incomplete` with
the evidence collected so far; do not use incomplete reports for downstream actions.

## Phase 3: generate comment drafts with OpenAI

Run Phase 2 first, then pass its `profile.json` path explicitly. Phase 3 does not
connect to the emulator; it sends only the extracted item IDs, titles, responses,
and optional personal facts to OpenAI. It displays three alternatives with one
recommendation and saves them as unapproved drafts. No likes or messages are sent.

Install the updated dependencies and set your API key in the same terminal:

```bash
.venv/bin/python -m pip install -r requirements.txt
read -rsp 'OpenAI API key: ' OPENAI_API_KEY
export OPENAI_API_KEY
.venv/bin/python generate_comments.py captures/profile_<id>/profile.json
```

Replace `profile_<id>` with your actual capture folder. The key is read from the
environment or the project-root `.env` file, which is loaded automatically. Exported
environment variables take precedence; `OPENAI_MODEL` can also be set in `.env`. Do not paste a key into
the code. The default model is `gpt-5.6-sol`; override it with `--model` or
`OPENAI_MODEL` using a model available to your API account that supports Responses
and structured outputs.

```bash
# Inspect the exact model request offline (no key or network required):
.venv/bin/python generate_comments.py captures/profile_<id>/profile.json --dry-run

# Ask for three options targeting just one prompt:
.venv/bin/python generate_comments.py captures/profile_<id>/profile.json \
  --item-id 'profile_<id>:prompt-2' --tone 'Dry wit, playful, lightly flirtatious'
```

`--max-chars` sets a draft length budget (default 180 Unicode code points), not a
verified Hinge limit. `--about-me /path/to/private-facts.txt` optionally supplies
your own facts for personalization. Keep that file outside version control.
The model is instructed to avoid invented personal claims and to treat profile
content as untrusted data. These instructions do not guarantee humor or factual
quality; review each draft yourself.

Outputs go to a unique `captures/drafts_<id>/` folder:

- `request.json` in dry-run mode: the exact request preview, without credentials.
- `drafts.json` after successful generation and validation: options, recommendation,
  target text, source scan/path/hash, model/usage information, and approval state.

Incomplete profiles, unknown targets, malformed/refused/incomplete model output,
duplicates, overlong comments, and quotes absent from the selected source are
rejected. Source-quote validation checks provenance, not the semantic truth of the
comment. The adapter disables automatic API retries and requests `store=False`;
this is not a guarantee of zero provider retention. Existing drafts are never
overwritten. Draft creation does not grant approval for future submission, and
saved targets require fresh UI resolution.

Implementation follows the official OpenAI documentation for
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
The configurable default is documented on the
[GPT-5.6 Sol model page](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

## Phase 4: prepare a comment without sending

```bash
.venv/bin/python prepare_comment.py captures/drafts_<id>/drafts.json --candidate c3
```

Choose `c1`, `c2`, `c3`, or `recommended`. The script checks the saved profile hash,
compares the current profile's written prompts against the saved report, re-finds
the target button from fresh XML, and opens its inline composer. It then verifies
that the field belongs to the exact selected prompt and pastes the chosen draft.
It leaves the composer open for review. It never activates Send Like or Send a Rose.

The inspected Hinge composer exposes `Edit comment` as a generic view without
readable XML text. Phase 4 uses native focus, clipboard paste, then Ctrl+A/Ctrl+C
to compare the complete field value to the chosen draft. A fresh clipboard marker
prevents a stale clipboard value from passing the check. The previous text clipboard
is restored afterward; non-text clipboard formats are not preserved by these APIs.
No Enter/editor-submit action is used. Control characters and WebDriver key codes
in drafts are rejected before interacting with Android.

Use `--inspect-only` to open and capture the composer without entering text.
If a composer is already open, the default stops. `--resume-composer` explicitly
allows replacing its current text after checking its profile label and exact prompt
association; this mode does not perform the full profile scan. Keep the emulator
untouched while the script operates. Same-name/same-content profiles cannot be
distinguished uniquely by these checks.

Each attempt writes `captures/preparation_<id>/preparation.json`, with before/after
captures when available. `text_verified_not_submitted` means exact text read-back
succeeded and the final capture was stable. A failure can leave partial UI changes;
inspect the composer before retrying. There is no automatic text-entry retry.
The original draft remains unapproved for submission. Successful read-back checks
the actual text, but does not establish Hinge's maximum comment length in general.

## Phase 5: submit one prepared comment

First run the preflight against a successful Phase 4 receipt:

```bash
.venv/bin/python submit_comment.py captures/preparation_<id>/preparation.json
```

It verifies the saved preparation against the draft/source, checks the current
profile and target composer, and copies the existing comment for exact comparison.
It does not replace text. It saves a preflight capture and stops without sending.

To explicitly authorize one actual Send Like click for that prepared comment:

```bash
.venv/bin/python submit_comment.py captures/preparation_<id>/preparation.json --send
```

Keep the emulator untouched. The send control must be the unique, visible,
enabled `Send like with message` button in the verified composer. Rose controls
and bare-like controls are excluded. Before clicking, the script writes and
flushes an exclusive attempt record under `captures/submissions/`. A second run
for the same observed profile is blocked even with a new scan ID or different
comment. Transport retries are disabled. No automatic send retry is implemented.

Outcomes are recorded conservatively:

- `attempted_outcome_unknown`: durable marker written before clicking; a crash
  may leave this state even if the click never happened.
- `profile_advanced_after_send`: two observations show a different Discover
  profile and no composer. This is UI evidence; server delivery is not independently
  confirmed.
- `uncertain`: timeout, unexpected screen, app change, or command error. The
  attempt stays blocked. Inspect the receipt and app manually before further action.

Do not delete attempt records merely to retry. The fingerprint uses profile label
and written prompts, not a stable account identifier: same-content profiles may
be conservatively blocked, and changed profile content can evade this local
deduplication. This is not a server-side exactly-once guarantee. UI checks are
sequential and cannot eliminate races caused by external interaction.

## Phase 5: verify and submit once

```bash
# Check the existing composer; does not send:
.venv/bin/python submit_comment.py captures/preparation_<id>/preparation.json

# Explicitly authorize one real submission of that exact prepared comment:
.venv/bin/python submit_comment.py captures/preparation_<id>/preparation.json --send
```

Phase 5 checks the receipt against the source draft, verifies current profile and
prompt association, copies the current comment for exact comparison, and resolves
only the selected composer's `Send like with message` control. It does not modify
the comment. Keep the emulator untouched during checking and submission.

Before clicking, it exclusively creates and flushes an attempt record under
`captures/submissions/`. Any recorded attempt blocks another send to the same
profile-label/prompt-text combination, including attempts from regenerated drafts.
Do not delete these records to retry an ambiguous send. The key is a conservative
local duplicate guard, not a server-side idempotency key or unique account identity.
It cannot prevent duplicates across machines, deleted records, or edited profile text.

The initial click is issued once. If it opens the inspected Rose upsell, the same
authorized run can choose `Send Like anyway` once, recording that step before
clicking it. It never chooses Send a Rose. An already-open sheet cannot bypass
preflight or be resumed by rerunning an attempted submission.
Timeouts, persistent composers, and profile advancement
without explicit confirmation remain uncertain (exit code 2). No retries occur.
English `Like sent`/`Your like was sent` confirmation labels are provisional until
observed in a real submission; `confirmed_by_ui` is UI evidence, not a delivery
receipt. Before/after captures are recorded when available. Default checks produce
`ready_not_sent` and do not consume a send attempt. Exit code 1 means a preflight
failure or other error; inspect the ledger if a process failed near submission.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

See `PROJECT_CONTEXT.txt` for the architecture and handoff.
Capability reference: [Appium UiAutomator2](https://github.com/appium/appium-uiautomator2-driver).
