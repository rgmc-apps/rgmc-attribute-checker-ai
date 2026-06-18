# Product

## Register

product

## Users

Internal RGMC Group staff, primarily the merchandising and product listing team. Non-technical users who upload clothing photos and need structured product descriptions for e-commerce listings. They work at a desk, daytime, in a fast-paced retail environment. Secondary users are IT staff who configure and maintain the system via the Config screen.

## Product Purpose

An internal GPT Vision tool that converts clothing product photos into structured listing copy. Users upload images, the tool sends them to OpenAI GPT-4V with a configurable prompt, and generates formatted product descriptions with attributes (material, fit, style, etc.) ready for download as Excel, CSV, or text files. Admins configure brands, prompt templates, and attribute sets per brand. Success: a merchandiser uploads 20 photos and gets a download-ready attribute sheet in under 5 minutes.

## Brand Personality

Practical, efficient, trustworthy. Three words: reliable, organized, fast. The tool serves an internal team and should feel purpose-built, not like a consumer app.

## Anti-references

Generic SaaS clones (Notion-style, Airtable-style): anonymous, template-looking, forgettable. The tool should feel like it was made specifically for RGMC's workflow.

## Design Principles

1. **Workflow first.** The 4-step flow (Upload, Prompt, Attributes, Generate) is the spine. Everything else supports it or stays out of the way.
2. **Earn trust with clarity.** Merchandisers are not developers. Labels, states, and feedback must be explicit. Never leave the user guessing what the system is doing.
3. **Configuration belongs in Config.** The main page is for production work. The Config page is for setup. Don't blur the boundary.
4. **Defaults that work out of the box.** A first-time user should be able to get a result without touching Config. Good defaults reduce setup friction.
5. **Feedback at every step.** Processing is async and can fail. Every state (loading, success, error, empty) needs a clear, helpful UI response.

## Accessibility & Inclusion

WCAG AA minimum. Desktop-first (office environment). Keyboard navigability required for all form interactions.
