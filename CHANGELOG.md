# Changelog

All notable changes to `inflow-publisher` are documented here.

## [0.1.1] - 2026-08-10

- Raised short-post creation and editing from 140 to 500 Unicode graphemes.
- Added 500/501-grapheme CLI boundary regression tests for creation and editing.
- Updated short posts to accept at most 9 attachments and articles at most 10.
- Replaced the obsolete presigned upload flow with API-managed multipart uploads.

## [0.1.0] - 2026-08-07

Initial public release.

- Added the installable `inflow-publisher` Agent Skill and OpenAI UI metadata.
- Added a dependency-free Python CLI with `whoami`, `list`, `upload`, `post`, `article`, `get`, `edit`, `publish`, and `delete` commands.
- Added posts, article feed captions, title/summary/Markdown bodies, topics, covers, and attachments.
- Added idempotent creation, bounded retry, optimistic locking, and client-side length validation.
- Added the inflow Agent API v1 reference and mock API tests.
