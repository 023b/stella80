# Third-party components

Bundled in `stella80/static/` so the app makes no network requests. Each keeps its own licence.

| Component | Version | Licence | Use |
|---|---|---|---|
| [KaTeX](https://katex.org) | 0.16.11 | MIT | maths rendering |
| [marked](https://marked.js.org) | 12.0.2 | MIT | Markdown rendering |
| [DOMPurify](https://github.com/cure53/DOMPurify) | 3.1.6 | Apache-2.0 or MPL-2.0 | sanitising rendered answers |
| [Mermaid](https://mermaid.js.org) | bundled build | MIT | diagrams |
| [Plotly.js](https://plotly.com/javascript/) | 2.35.2 | MIT | function plots and 3D surfaces |
| [math.js](https://mathjs.org) | bundled build | Apache-2.0 | evaluating plotted expressions |
| [VT323](https://fonts.google.com/specimen/VT323) | | SIL Open Font License 1.1 | terminal font |
| [Righteous](https://fonts.google.com/specimen/Righteous) | | SIL Open Font License 1.1 | logo font |
| [IBM Plex Mono](https://github.com/IBM/plex) | | SIL Open Font License 1.1 | body font |

Runtime dependencies installed by pip are listed in `requirements.txt`. Models are pulled by you through Ollama
and come with their own licences.
