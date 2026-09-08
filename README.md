# Android AI Controller

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

## Tests

```bash
python3 -m unittest discover -s tests -v
```

See `PROJECT_CONTEXT.txt` for the architecture and handoff.
Capability reference: [Appium UiAutomator2](https://github.com/appium/appium-uiautomator2-driver).
