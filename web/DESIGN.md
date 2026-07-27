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

## Anti-patterns

- No gradients, glass, glow, oversized rounded cards or ornamental illustrations.
- No fake analytics or fake platform publish success.
- No color-only state: every status includes a text label.
- No action hidden only on hover; touch and keyboard users must see the same controls.
