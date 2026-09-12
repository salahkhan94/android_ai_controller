# Observed match-reply UI, 2026-09-11

Read-only inspection; no reply was sent. Existing run.py remains unchanged.

- Matches tab: accessibility description starts with `Matches` and includes a
  new-message count. One tab was exposed.
- Your turn is a text heading `Your turn (1)`; Hidden has its own heading.
- Observed row is a clickable, long-clickable view containing name and preview
  TextViews, bounded inside the Your turn section. No stable thread resource ID
  was exposed. Duplicate names are currently rejected.
- Thread header contains a name TextView near the top plus Back/More buttons.
- Message bubbles use content-desc ` You: <text>. ` or ` Akisha: <text>. `.
  The final `. ` is accessibility formatting; the actual text may itself end in
  punctuation. XML order is not chronological: sort visible bubbles by vertical
  position, then merge overlapping sequences across swipes.
- Composer: android.widget.EditText resource ID
  `co.hinge.app:id/messageComposition`; placeholder `Send a message`.
- Empty composer shows Record voice note, resource ID
  `co.hinge.app:id/microphoneButton`. Send button with entered text remains unverified.
- Earliest observed viewport has the original profile prompt and initial like
  comment as unlabelled text, plus first speaker-labelled incoming bubble.
  Preserve that opening card as context without assigning an invented speaker.
- An initial Matches capture displayed No network connection. Later list
  extraction succeeded with that notice absent. Service fails closed on the notice.

Evidence (ignored captures):
- Matches: matches/20260911T043300_007134Z_f18a01bb
- Latest thread: matches/20260911T043336_425041Z_5d4db24c
- Earliest thread: matches/20260911T043444_790395Z_0772a540

Only the accessible UI was inspected. A stationary scroll boundary alone cannot
prove the server has supplied every historical message. Missing/ambiguous overlap
must remain an explicit extraction failure, not silent omission.

- Live full-history extraction succeeded with 52 entries (opening context plus
  labelled messages), saved in matches/20260911T044812_669249Z_29d5d678.
- Reacted message suffix observed: `. Akisha liked this message`. Parser removes
  only that recognized suffix and stores liked_by metadata separately.
- Date separators are preserved as system entries by the current parser.
