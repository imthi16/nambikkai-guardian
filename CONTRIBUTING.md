# Contributing

Read `AGENTS.md` before changing the repository. Every feature starts with a GitHub issue and a
focused branch such as `feat/document-upload`. Use Conventional Commits, keep tenant and document
authorization inside data-access layers, and treat all document-derived content as untrusted.

Run `make check` before opening a pull request. Behavioral changes require meaningful tests;
AI-pipeline changes also require measurable evaluation coverage. Complete every section of the
pull-request template and include screenshots for visible UI changes. Never commit secrets, PII,
private documents, or generated model artifacts.

See `docs/DEVELOPMENT.md` for local setup and command details.
