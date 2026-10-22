# Agent workbench design

Use the existing procurement workbench as the subject: an evidence desk, with conversation
on the left and source records to the right. The distinctive element is an expandable
source receipt below each answer, linking a value to its exact captured span.

Palette: paper white #ffffff, slate canvas #eef3f7, navy ink #18354a, teal action #116b67,
blue evidence #285ba6, amber review #94631b. Use local system Chinese sans-serif for UI
and readable serif only for the workbench title; no remote font request. Left-align text,
tabular numerals for budgets, generous line height for source excerpts.

The initial view invites a real question with four grounded examples, not invented chat
history. Show the actual imported-record count and provider configuration state. Keep model
and token details inside the run receipt; they help evaluate a result but do not interrupt
the query flow. On narrow screens stack the source panel below the conversation. Visible
focus, semantic forms/status alerts, keyboard submission and reduced-motion support are required.

Review: avoid a generic analytics card grid or a marketing hero. The main surface is the
working conversation; the evidence panel and dataset drawer carry the procurement-specific
structure. Keep the old archive view available rather than rewriting its proven functionality.
