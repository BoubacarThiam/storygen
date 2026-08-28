# StoryGen — PRODUCT.md

## What it is

A "coût zéro" pipeline that turns a text prompt into a short narrative video
(script → images → voice-over → subtitles → montage), in French by default,
9:16 or 16:9. The web interface (`src/storygen/webapp.py` +
`src/storygen/templates/index.html`) is a single-page form: type a prompt,
watch a real-time pipeline progress, get a video to watch/download.

## Register

**Product.** This is a tool, not a marketing page — one task (submit a
prompt, watch it become a video), no persuasion, no scroll-story. Design
serves the task; it should disappear into it.

## Users

Primarily the solo developer (Boubacar) testing/using the pipeline himself,
plus anyone he shares the link with casually. Not a public multi-tenant
SaaS — no accounts, no dashboard, no history beyond the current job. Used
at a desk, often late/iterative sessions, sometimes to just check that a
prompt idea "works" before trusting it.

## Purpose / desired outcome

Submit a prompt with minimal friction, understand what stage the pipeline
is at without guessing (script/images/voice/subtitles/montage are 5 real,
distinct, sequential stages — a genuine pipeline, not decorative steps),
and land on a video that's obviously ready to watch/download the moment
it's done.

## Brand personality

Grounded, warm, unpretentious — echoes the stories themselves (everyday
Senegalese life: fishermen, seamstresses, elders) without turning the tool
UI into a "brand experience." The tool itself stays a restrained, honest
dev-tool: dark neutral surface, one warm accent (not the full warm/teal
palette used on the one-off video-preview Artifact pages, which are a
different register — a showcase moment, not a task surface).

## Anti-references

- Generic dark SaaS dashboard (purple/blue gradients, glassmorphism, hero
  metrics) — this is a single-form utility, not a product marketing page.
- Cutesy AI-generator packaging (confetti, mascots, gradient text on the
  logo) — the pipeline's real steps and constraints (cost, time, model
  fallbacks) are already the interesting/honest story; no need to oversell.

## Accessibility

Keyboard-operable form, visible focus states, ≥4.5:1 text contrast, error
states that explain what happened (the pipeline surfaces real upstream
errors — e.g. a missing API key, a rate-limited model — verbatim; the UI
should present them clearly, not swallow them).
