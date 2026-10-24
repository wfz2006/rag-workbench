# Third-party notices

## Open Claude UI

Portions of the UI design-token stylesheet are adapted from
[Caho1/open-claude-ui](https://github.com/Caho1/open-claude-ui),
commit `c1f923ae0051a871941b98a0e2e3183a545e6a85`,
`code-examples/cds-theme.css`.

Copyright (c) 2026 Caho1. Licensed under the MIT License.
The complete license is preserved at `licenses/open-claude-ui.MIT.txt`.
The Streamlit adaptation is in `src/rag_workbench/ui/assets/`.

The interaction adapter also references the behavior described in the same commit's
`useClaudeScrollPinning.ts`, `ClaudeMotionPresets.ts`, and `ChatComposer.tsx` examples.
Scroll-follow rules are independently implemented for Streamlit, with additional reference to
[assistant-ui's scroll-anchor documentation](https://www.assistant-ui.com/elements/scroll-anchor).
No assistant-ui code or runtime dependency is bundled.

Claude and Claude Code are Anthropic trademarks. This project remains an independent
RAG application using its own configured model provider; it is not an Anthropic client.

## Desktop distribution

The portable distribution bundles an Astral python-build-standalone runtime and pywebview.
Python's license is retained in runtime/LICENSE.txt; installed dependency licenses and metadata
remain in runtime/Lib/site-packages. THIRD_PARTY_MANIFEST.json records package versions and
available license metadata; requirements.lock.txt records hashed dependency requirements.

The optional BAAI/bge-small-zh-v1.5 weights are identified as MIT in the upstream model card:
https://huggingface.co/BAAI/bge-small-zh-v1.5 . The model card is retained with the cached snapshot.
The FlagEmbedding repository's upstream MIT license is also retained in
licenses/FlagEmbedding.MIT.txt (https://github.com/FlagOpen/FlagEmbedding/blob/master/LICENSE).

The book/quotation desktop icon is original generated artwork, created with the built-in
image_gen tool. Its generation prompt is preserved in docs/ICON_PROMPT.txt. The ICO contains
resampled sizes of the same PNG artwork; no Anthropic logo is bundled as the app identity.
