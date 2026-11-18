# Apache ECharts graph bundle

`echarts.min.js` is a locally bundled and minified derivative of [Apache ECharts 6.1.0](https://github.com/apache/echarts/tree/6.1.0). It includes the graph chart, tooltip component, and SVG renderer. It exposes `WorkbenchECharts.init` within the enclosing script.

The bundle includes [ZRender 6.1.0](https://github.com/ecomfe/zrender/tree/6.1.0), [tslib 2.3.0](https://github.com/microsoft/tslib/tree/2.3.0), and the d3-derived code identified in the upstream ECharts license. The accompanying `LICENSE`, `NOTICE`, and `licenses/` files retain their upstream terms.

To regenerate the bundle, run these commands in a temporary directory.

```sh
npm install --save-exact echarts@6.1.0 esbuild@0.25.11
npx --no-install esbuild entry.mjs --bundle --minify --format=iife --global-name=WorkbenchECharts --platform=browser --target=es2019 --legal-comments=eof "--banner:js=/*! Generated Apache ECharts 6.1.0 graph build. See the adjacent vendor notices. */" --outfile=echarts.min.js
node -e "const f=require('node:fs'), p='echarts.min.js', s=f.readFileSync(p,'utf8'), i=s.indexOf('/*! Bundled license information:'); if (i>=0) f.writeFileSync(p,s.slice(0,i)+s.slice(i).replace(/[ \t]+$/gm,''));"
```

Use this `entry.mjs` with the commands above.

```js
import { init, use } from "echarts/core";
import { GraphChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";

use([GraphChart, TooltipComponent, SVGRenderer]);
export { init };
```
