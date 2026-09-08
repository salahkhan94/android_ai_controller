# Live profile inspection — 2026-09-08

Three live observations succeeded through Appium on device `127.0.0.1:6555`.
All had unchanged bracketing XML and package/activity state. The foreground
package was `co.hinge.app`, activity `.ui.AppActivity`; both screenshot and
window dimensions were 570 × 1230. Appium-Python-Client 5.3.1 was installed in
the project `.venv` for this inspection.

## Local evidence

Raw captures are private and Git-ignored; these paths are local references:

- `captures/20260908T051746_175366Z_e7178553`: two photo cards.
- `captures/20260908T051844_820043Z_43bffa58`: profile top and partially visible prompt.
- `captures/20260908T051914_311206Z_46669cb9`: full written prompt and like control.

The screenshots were visually inspected and compared with `nodes.json`.
Two vertical gestures repositioned the same displayed profile. No like, skip,
composer, or messaging control was activated. The emulator was left with the
written prompt visible. Profile text and photos are not reproduced in this report.

## Confirmed structure

- Written prompt title and response appear in a single `android.view.View`
  `content-desc`, formatted `Prompt: <title>. Answer: <response>`. Its `text`
  attribute is empty. Text-only extraction would miss this prompt.
- In the full-prompt observation, node 37 carries that description. Node 40 is
  an enabled, clickable `android.widget.Button` with `content-desc="Like prompt"`.
  Their shared card container is node 36: the description is its direct child,
  while the button is nested under nodes 38 and 39.
- Card bounds are `[28,528][542,925]`; button bounds are `[461,844][528,911]`.
  These coordinates and node IDs describe this observation only.
- Photos expose an ImageView description identifying them as profile photos,
  plus an associated button labeled `Like photo`. The image and button share
  a card ancestor. Multiple buttons have identical labels, so a global first
  match cannot identify the intended photo.
- Inspected prompt and photo controls have no resource IDs. Outer application
  containers do have IDs, but those do not distinguish profile items.
- The partially visible prompt already exposed its full answer in XML while
  its like button was absent. XML text availability does not imply action readiness.
- The profile name appeared as TextView text at the profile top, but was absent
  from the sticky header's text attributes in the scrolled observation. A photo
  label and skip label still referred to the profile, but these are not unique IDs.

## Phase 2 approach supported by this evidence

1. Find descriptions starting with `Prompt: `; split once on `. Answer: `,
   preserving the original description. Reject unexpected formats for review.
2. Associate a prompt with a unique `Like prompt` descendant in its nearest
   suitable card ancestor. Reject ambiguous containers; do not climb to the
   whole profile and attach an unrelated button.
3. Store text, card/control bounds, raw descriptor, and observation ID. Distinguish
   extractable content from a currently displayed and enabled actionable target.
4. Re-observe and resolve the card/control immediately before any future action.
   Do not persist Appium element handles or use these node indices as selectors.
5. Use bounded scrolling with overlapping content for discovery and deduplication.
   Do not use display name alone as a profile identity. Establish stronger continuity
   checks before combining content across observations.
6. Use vision for photo meaning and as fallback for missing accessibility text.
   This inspected written prompt does not need OCR.

Only one profile and one written prompt were inspected. Other prompt types,
locales, and layouts remain unverified. Composer behavior, comment limits,
submission, and confirmation were intentionally not tested in Phase 1.
