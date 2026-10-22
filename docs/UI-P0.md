# P0 UI decisions

The interface is a procurement reading workspace, with a notice table and a source/evidence
detail panel. Desktop uses a 1.8:1 split; narrow screens put details below the list.
The official-source strip communicates permission uncertainty and the empty state tells
the operator to import a lawfully held file. There is no sample-data mode or hero dashboard.

Palette: page #F5F7FA, surface #FFFFFF, ink #20314B, source-link blue #245CAF,
connected state #23775A, review/unknown amber #86550A. Chinese system sans fonts prioritize
legibility without remote font requests. Alignment is left; a blue detail-panel edge marks
the source evidence section. The review against the brief removed decorative statistics,
charts and animated effects. Controls have keyboard focus, errors are announced, details
load cancellably, and amounts remain decimal strings.

Local build passed. Browser visual inspection in this execution environment was blocked
by the cloud browser's inability to reach the local loopback server; do not claim visual
or browser E2E acceptance based on a successful build.
