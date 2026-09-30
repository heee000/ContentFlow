# ContentFlow interface contract

ContentFlow uses a restrained enterprise application system inspired by IBM Carbon. The product is a dense operations workspace, so hierarchy comes from a precise grid, surface changes and hairlines rather than decorative cards.

## Visual grammar

- Canvas is white; the application rail is `#161616`; alternate work surfaces use `#f4f4f4`.
- `#0f62fe` is the only interactive accent. Green, yellow and red are reserved for status.
- Chinese system sans-serif is used for reliable local rendering. Display text stays light; labels and table headers use medium weight.
- Spacing follows a 4px base. Main units are 8, 12, 16, 24, 32 and 48px.
- Buttons, inputs, panels and tables use square corners. Depth uses 1px borders, never drop shadows.

## Application components

- Desktop: 256px dark rail, 48px utility header and 16-column content grid.
- Tablet: 72px icon rail and two-column metric grid.
- Phone: top bar plus horizontal section switcher; all tables become stacked records.
- Inputs use a gray fill and a 2px blue focus underline.
- Every async action has busy, success and error feedback. Destructive or irreversible actions are never the default button.
- Status labels remain compact, but interactive controls retain a minimum 44px target.

## Product information architecture

- The default navigation exposes one operating path: workspace → create → review → prepare assets → publish.
- Knowledge, channels, analytics, queues and administration remain available under a clearly named secondary group; they do not compete with the primary workflow.
- The dashboard computes one evidence-based next action from real workflow state. It never claims completion from fake or inferred platform success.
- Forms reveal common choices first. Scheduling, script fallback and manual export are progressive options, while safety warnings remain visible at the decision point.
- On phones, the four workflow stages remain directly reachable and secondary tools move into one native “more” selector.
- Every campaign receives a stable `CF-XXXXXX` project code. The utility header can scope the workspace to one project, and project code, campaign, product and content title travel together through review, assets, publishing, metrics and jobs.
- Asset preparation uses three explicit lanes: system processing, action required and ready. Campaign creation requires an explicit default media route; current-version cover tasks expose manual, AI and open-library routes at the decision point. Manual upload explains why human input is required, the exact file expected and what becomes unblocked after validation, while unavailable AI capability stays visible and clearly disabled.

## Motion and interaction

- Motion communicates input, navigation or state change only: 80–180ms button press, view entrance and toast arrival.
- Pressed controls move by at most 1px; selection uses borders and surface changes, never glow or ornamental bounce.
- Loading state stays explicit and disables duplicate submission. Success, failure, retry-safe and reconciliation-required remain text-labelled.
- Known workflow stages use persisted backend stage progress; unknown-duration asset work uses an indeterminate activity indicator. Neither may invent elapsed percentages, ETA or platform success.
- `prefers-reduced-motion` reduces animations and transitions to effectively zero.

## Anti-patterns

- No gradients, glass, glow, oversized rounded cards or ornamental illustrations.
- No fake analytics or fake platform publish success.
- No color-only state: every status includes a text label.
- No action hidden only on hover; touch and keyboard users must see the same controls.
- No flat list of every professional module in the primary navigation; advanced capabilities use progressive disclosure.
- No animation that delays work, implies false progress or ignores reduced-motion.
