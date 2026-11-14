---
name: FreshGate
description: A composed evidence ledger for AI claim freshness operations.
colors:
  primary: "#2e4937"
  secondary: "#a34534"
  tertiary: "#9a7227"
  neutral-bg: "#f4f0e6"
  neutral-rail: "#eae5d8"
  neutral-panel: "#fcfaf4"
  neutral-ink: "#292920"
  neutral-muted: "#605f54"
  neutral-rule: "#d8d0bf"
  success-wash: "#e0e7dc"
  error-wash: "#f0e0d8"
  warning-wash: "#f2ead4"
typography:
  display:
    fontFamily: "Source Serif 4 Variable, Georgia, serif"
    fontSize: "34px"
    fontWeight: 540
    lineHeight: 1.15
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Source Serif 4 Variable, Georgia, serif"
    fontSize: "21px"
    fontWeight: 520
    lineHeight: 1.2
  body:
    fontFamily: "IBM Plex Sans Variable, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "IBM Plex Sans Variable, sans-serif"
    fontSize: "11px"
    fontWeight: 590
    lineHeight: 1.4
rounded:
  sm: "4px"
  control: "5px"
  card: "8px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.neutral-panel}"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "38px"
  button-secondary:
    backgroundColor: "{colors.neutral-panel}"
    textColor: "{colors.neutral-ink}"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "38px"
  input:
    backgroundColor: "{colors.neutral-panel}"
    textColor: "{colors.neutral-ink}"
    rounded: "{rounded.sm}"
    padding: "8px 10px"
    height: "38px"
  folio-card:
    backgroundColor: "{colors.neutral-panel}"
    textColor: "{colors.neutral-ink}"
    rounded: "{rounded.card}"
    padding: "23px 24px"
---

# Design System: FreshGate

## Overview

**Creative North Star: "Signal Ledger"**

FreshGate should feel like a careful record of trust decisions and the evidence behind them. Its warm, composed voice brings the clarity of an operator's ledger to a technical workflow: serif headings lend the page an editorial cadence while sans-serif labels keep states, controls, and returned data direct.

Archival cream, olive, brick, and brass accents give the system a grounded field-note character. A matching dark palette shifts those surfaces to ink and deep olive while preserving the same accent roles and readable evidence hierarchy. Operators can switch themes from any dashboard section; their theme preference persists in this browser.

Flat surfaces and fine rules carry most of the structure; tactile controls and restrained, state-led motion make the interface responsive without competing with the evidence.

**Key Characteristics:**
- Warm paper, dark ink, and restrained botanical green.
- Serif-led hierarchy with legible sans-serif controls and data.
- Thin borders and tonal shifts define the working surfaces.
- Motion explains active verification and arriving evidence; reduced-motion preferences are respected.
- Operational states and records come from FreshGate APIs.

## Colors

The palette pairs archival cream and ink with olive green, specimen brick, and brass; dark mode keeps those roles in a lower-luminance, softened palette. Accents clarify roles instead of decorating the whole canvas.

### Primary
- **Ledger Olive** ({colors.primary}): Primary actions, active navigation, verification progress, and successful or enabled states.

### Secondary
- **Specimen Brick** ({colors.secondary}): Error emphasis, source-note numbering, and destructive actions.

### Tertiary
- **Brass Marker** ({colors.tertiary}): Warm caution and alert emphasis.

### Neutral
- **Archival Cream** ({colors.neutral-bg}): Main application canvas.
- **Paper Rail** ({colors.neutral-rail}): Sidebar and secondary working areas.
- **Folio Stock** ({colors.neutral-panel}): Cards, forms, and evidence panels.
- **Ledger Ink** ({colors.neutral-ink}): Main text and headings.
- **Soft Graphite** ({colors.neutral-muted}): Supporting labels and explanatory copy.
- **Rule Stone** ({colors.neutral-rule}): Panel edges, dividers, and table structure.
- **Verdant Wash**, **Brick Wash**, and **Brass Wash** ({colors.success-wash}, {colors.error-wash}, {colors.warning-wash}): Subtle success, error, and caution backgrounds.

**The Evidence Color Rule.** Color supports the written status; meaning is never communicated by color alone.

## Typography

**Display Font:** Source Serif 4 Variable (with Georgia, serif)  
**Body Font:** IBM Plex Sans Variable (with sans-serif)  
**Label/Mono Font:** IBM Plex Sans Variable; tabular numerals for code and operational values.

**Character:** Source Serif 4 gives the ledger a measured editorial voice. IBM Plex Sans keeps dense controls, labels, and API-backed values crisp and familiar to operators.

### Hierarchy
- **Display** (weight 540, 34px, line-height 1.15, tracking -0.035em): Main page titles.
- **Title** (weight 520, 21px, line-height 1.2): Panel and section headings.
- **Body** (weight 400, 13px, line-height 1.6): Explanatory copy and form content.
- **Label** (weight 590, 11px, line-height 1.4): Field labels and compact controls.

**The Two-Voice Rule.** Use serif for hierarchy and evidence folio titles; use sans-serif for statuses, controls, navigation, and operational data.

## Layout

The desktop workspace uses a fixed left navigation rail and a sticky, low-profile top bar. Main content is centered within a 1560px maximum width, with generous page margins; forms, evidence panels, and data tables use compact internal spacing.

At tablet widths, the verification flow and system columns collapse into one column while evidence tables remain locally scrollable. At mobile widths, the sidebar becomes a slide-in drawer, top-bar and content margins tighten, and the evidence route adapts to preserve its sequence. Keep the page usable from 320px upward without horizontal page overflow.

The recurring spacing rhythm is 4, 8, 12, 16, and 24px, with larger section gaps for page-level groups.

## Elevation & Depth

The system is flat by default. Fine borders and warm tonal layers separate panels from the cream canvas, with no ambient card shadows. The mobile navigation drawer is the exception: its shadow clarifies that it overlays the page. Focus rings and active states supply their own contrast.

Motion is short and purposeful: page content settles in, evidence notes arrive in sequence, controls transition between states, and the verification route moves only while a request is in flight. Honor prefers-reduced-motion.

**The Border-First Rule.** Use a rule or tonal shift before adding a shadow; reserve elevation for a genuinely overlapping surface.

## Shapes

Use gently rounded, compact geometry: 4px for fields and small controls, 5px for buttons and navigation, and 7–8px for folio panels. Circular forms are reserved for status markers and the brand mark. Tables and ledger summaries use square, rule-led edges.

## Components

### Buttons
- **Shape:** Compact, softly rounded controls (5px).
- **Primary:** Ledger Olive with Folio Stock text; 38px tall with 14px horizontal padding.
- **Hover / Focus:** Hover shifts green and lifts the control by 1px. Keyboard focus uses a visible 2px green outline with a 3px offset.
- **Secondary:** Light paper fill with a quiet stone border; hover warms the surface and strengthens the border.
- **Disabled:** Use a distinct muted surface with legible text, show a not-allowed cursor, and retain the action label.

### Chips and Status Marks
- **Style:** Small text paired with a 6–7px status dot. Use green for good, brick for bad, brass for caution, and gray for unknown.
- **State:** Keep the status word visible so meaning survives without color.

### Cards and Containers
- **Corner Style:** 7–8px for folio panels; 4–5px for nested controls.
- **Background:** Folio Stock for primary panels and warm paper tones for secondary or route panels.
- **Shadow Strategy:** Flat at rest with a thin warm border; use a shadow only for the mobile overlay drawer.
- **Internal Padding:** Common panel padding is 20–24px, tightening responsively.

### Inputs and Fields
- **Style:** Warm-white fill, 1px stone border, 4px radius, and compact 12–13px sans-serif text.
- **Focus:** Green border with a faint green halo and visible keyboard focus outline.
- **Error / Disabled:** Errors use a brick notice with a written explanation; disabled actions remain legible.

### Navigation
- **Style:** Fixed paper-rail sidebar on desktop; active items receive a pale green background, stronger text, and a narrow edge marker.
- **Responsive:** Convert the sidebar into a slide-in drawer on mobile; its scrim and movement honor reduced motion.

### Theme Switch
- **Style:** Keep the compact labeled switch in the top bar, using a sun or moon icon and clear on/off state.
- **Behavior:** Apply theme tokens across navigation, tables, forms, notices, menus, and evidence surfaces. Persist only the theme preference in browser storage; never store provider credentials there.

### Evidence Folios
- **Style:** Present each returned claim with its freshness and authority state, then source notes in a clear reading order.
- **Behavior:** Source notes may enter in sequence after results arrive. Use only evidence and metadata returned by the API; represent unavailable values explicitly.

## Do's and Don'ts

- **Do** keep real service state, policies, claims, alerts, audit rows, and replay records tied to FreshGate APIs.
- **Do** distinguish empty, loading, unavailable, failed, and successful states with direct language.
- **Do** keep request-scoped provider credentials out of browser storage.
- **Do** pair status colors with text and preserve visible keyboard focus.
- **Don't** fill an empty dashboard with invented operational metrics or pretend records.
- **Don't** use motion that obscures evidence or persists when reduced motion is requested.
- **Don't** use shadows to make every card appear elevated; borders and tonal paper layers are the default.
