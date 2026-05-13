# Third-Party Licenses

## Game-icons.net

Icons used throughout the Maiko UI are sourced from
[game-icons.net](https://game-icons.net/), distributed under the
[Creative Commons Attribution 3.0 Unported License](https://creativecommons.org/licenses/by/3.0/).

Each icon's original artist is credited inside the upstream
[`@iconify-json/game-icons`](https://www.npmjs.com/package/@iconify-json/game-icons)
package metadata (see `node_modules/@iconify-json/game-icons/info.json`).

The mapping from Maiko icon names to game-icons identifiers lives in
`frontend/src/icons/index.jsx`.

## Lucide

A handful of helper icons still re-export from
[lucide-react](https://lucide.dev/) (MIT) for surfaces not yet ported.
Listed at the bottom of `frontend/src/icons/index.jsx`.

## Iconify

[`@iconify/react`](https://iconify.design/) (MIT) is the runtime
component used to render the bundled game-icons collection.
