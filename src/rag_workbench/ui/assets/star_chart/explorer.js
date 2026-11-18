const STAR_SERIES_ID = "knowledge-points";
const MAX_RESULTS = 40;
const BOUNDS = [-1.02, 1.02].map((size, index) => ({
  id: `__bounds-${index}`, x: size, y: size, symbolSize: 0, silent: true,
  itemStyle: { opacity: 0 }, label: { show: false }, tooltip: { show: false },
}));
const EDGE_APPEARANCE = {
  co_occurrence: { color: "#83c8e7", type: "solid", width: 2 },
  reviewed: { color: "#f2b88c", type: "dashed", width: 2.6 },
};
let nextStarSearchId = 0;

export default function mountStarGraph({ parentElement, data }) {
  const root = parentElement.querySelector(".wb-star-explorer");
  if (!root || !data?.nodes?.length) return;

  const doc = root.ownerDocument;
  const chartHost = root.querySelector(".wb-star-chart");
  const search = root.querySelector("[role=combobox]");
  const results = root.querySelector("[role=listbox]");
  const edgePicker = root.querySelector("select");
  const evidencePanel = root.querySelector(".wb-star-evidence");
  const inspector = root.querySelector(".wb-star-inspector");
  const back = root.querySelector("[data-action=back]");
  const reset = root.querySelector("[data-action=reset]");
  const nodes = new Map(data.nodes.map(node => [node.id, node]));
  const edges = new Map(data.edges.map(edge => [edge.id, edge]));
  const vocabulary = data.nodes.map(node => [node, node.label.toLocaleLowerCase()]);
  const history = [];
  const listeners = new AbortController();
  const listen = (element, event, handler) => {
    element.addEventListener(event, handler, { signal: listeners.signal });
  };
  const create = (tag, text) => {
    const element = doc.createElement(tag);
    if (text != null) element.textContent = text;
    return element;
  };
  const categories = new Map(data.nodes.map(node => [node.kind, node]));
  const nodeLegend = root.querySelector(".wb-star-node-key");
  for (const kind of ["material", "method", "property", "topic"]) {
    const category = categories.get(kind);
    if (!category) continue;
    const label = create("span", category.kindLabel);
    const dot = create("i");
    dot.style.backgroundColor = category.color;
    label.prepend(dot);
    nodeLegend.append(label);
  }

  results.id = `wb-star-results-${++nextStarSearchId}`;
  search.setAttribute("aria-controls", results.id);
  let center = nodes.has(data.initial) ? data.initial : data.nodes[0].id;
  let visibleEdges = [];
  let selectedEdge = "";
  let resultRows = [];
  let activeResult = -1;
  let positions = new Map();

  function tooltip({ dataType, data: item }) {
    const result = (dataType === "node" ? nodes : edges).get(item?.id);
    if (!result) return "";
    const card = create("div");
    card.className = "wb-star-tooltip-content";
    card.setAttribute("role", "tooltip");
    card.append(create("strong", result.label ?? result.title));
    const sources = [...new Set((result.evidence ?? []).map(proof => proof.title))];
    for (const title of sources.length ? sources : ["暂无可用的原文依据"]) {
      card.append(create("span", title));
    }
    return card;
  }

  const chart = init(chartHost, null, { renderer: "svg" });
  chart.setOption({
    animation: false,
    backgroundColor: "#111c2a",
    tooltip: {
      trigger: "item", renderMode: "html", className: "wb-star-tooltip", formatter: tooltip,
      backgroundColor: "#203149", borderColor: "#526d86", confine: true, enterable: false,
      showDelay: 45, hideDelay: 40, transitionDuration: 0, extraCssText: "pointer-events:none;",
    },
    series: [{
      id: STAR_SERIES_ID, type: "graph", layout: "none", roam: true, cursor: "pointer",
      zoom: 1, selectedMode: "single", scaleLimit: { min: 0.6, max: 2.8 },
      label: {
        show: true, position: "bottom", distance: 7, color: "#e2ecf7", fontSize: 12,
        width: 114, overflow: "truncate", fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif',
      },
      emphasis: { scale: 1.16, lineStyle: { width: 4, opacity: 1 } },
      select: { lineStyle: { width: 4, opacity: 1 } },
      data: BOUNDS,
    }],
  });

  function edgeLabel(edge) {
    return `${edge.title} · ${nodes.get(edge.source).label} ↔ ${nodes.get(edge.target).label}`;
  }

  function showEvidence() {
    const edge = edges.get(selectedEdge);
    const node = nodes.get(center);
    const heading = create("h2", edge ? edge.title : "知识点依据");
    heading.setAttribute("aria-live", "polite");
    evidencePanel.replaceChildren(heading, create("p", edge
      ? `${nodes.get(edge.source).label} ↔ ${nodes.get(edge.target).label}`
      : `${node.kindLabel} · ${node.label}`));
    const evidence = (edge ?? node).evidence ?? [];
    if (!evidence.length) {
      evidencePanel.append(create("p", "暂无可用的原文依据"));
    }
    for (const source of evidence) {
      const card = create("article");
      card.append(create("h3", source.title), create("p", source.text));
      evidencePanel.append(card);
    }
    inspector.scrollTop = 0;
  }

  function clearSearch() {
    results.hidden = true;
    search.setAttribute("aria-expanded", "false");
    search.removeAttribute("aria-activedescendant");
    search.value = nodes.get(center).label;
  }

  function arrange(ids, previousCenter) {
    const neighbors = ids.filter(id => id !== center && nodes.has(id));
    const step = 2 * Math.PI / Math.max(1, neighbors.length);
    const previousIndex = neighbors.indexOf(previousCenter);
    const incoming = positions.get(center);
    const start = previousIndex >= 0 && incoming
      ? Math.atan2(-incoming.y, -incoming.x) - step * previousIndex
      : -Math.PI / 2;
    positions = new Map([[center, { x: 0, y: 0, labelPosition: "bottom" }]]);
    neighbors.forEach((id, index) => {
      const phase = start + index * step;
      const x = Math.cos(phase);
      const y = Math.sin(phase);
      const labelPosition = Math.abs(x) > 0.72 ? (x < 0 ? "left" : "right") : (y < 0 ? "top" : "bottom");
      positions.set(id, { x, y, labelPosition });
    });
    return [...positions].map(([id, point]) => {
      const node = nodes.get(id);
      const isCenter = id === center;
      return {
        id, name: node.label, x: point.x, y: point.y, symbolSize: isCenter ? 30 : 22,
        itemStyle: {
          color: node.color, shadowColor: node.color, shadowBlur: isCenter ? 8 : 3,
          borderColor: isCenter ? "#e2ecf7" : "#111c2a", borderWidth: isCenter ? 2 : 1,
        },
        label: { position: point.labelPosition, fontWeight: isCenter ? 650 : 400 },
      };
    });
  }

  function draw(previousCenter) {
    const view = data.neighborhoods[center] ?? { nodes: [center], edges: [] };
    const displayedNodes = arrange(view.nodes, previousCenter);
    visibleEdges = view.edges.map(id => edges.get(id)).filter(edge => edge
      && positions.has(edge.source) && positions.has(edge.target));
    const counts = new Map();
    const pairKey = edge => [edge.source, edge.target].sort().join("|");
    for (const edge of visibleEdges) {
      const pair = pairKey(edge);
      counts.set(pair, (counts.get(pair) ?? 0) + 1);
    }
    const links = visibleEdges.map(edge => {
      const endpoints = [edge.source, edge.target].sort();
      const appearance = EDGE_APPEARANCE[edge.kind] ?? EDGE_APPEARANCE.co_occurrence;
      return {
        id: edge.id, source: endpoints[0], target: endpoints[1], name: edge.title,
        lineStyle: {
          ...appearance, opacity: 0.88,
          curveness: counts.get(pairKey(edge)) > 1 ? (edge.kind === "reviewed" ? 0.2 : -0.2) : 0.04,
        },
      };
    });
    chart.dispatchAction({ type: "hideTip" });
    chart.setOption({ series: [{ id: STAR_SERIES_ID, data: [...displayedNodes, ...BOUNDS], links }] });
    const prompt = create("option", visibleEdges.length ? "选择一条关联" : "暂无可用连线");
    prompt.value = "";
    edgePicker.replaceChildren(prompt, ...visibleEdges.map(edge => {
      const option = create("option", edgeLabel(edge));
      option.value = edge.id;
      return option;
    }));
    edgePicker.disabled = !visibleEdges.length;
    showEvidence();
  }

  function focusNode(id, recordHistory = true) {
    if (!nodes.has(id)) return;
    if (id !== center) {
      const previous = center;
      if (recordHistory) history.push(previous);
      center = id;
      selectedEdge = "";
      draw(previous);
    } else if (selectedEdge) {
      focusEdge("");
    }
    back.disabled = history.length === 0;
    clearSearch();
  }

  function focusEdge(id) {
    const previousIndex = visibleEdges.findIndex(edge => edge.id === selectedEdge);
    if (previousIndex >= 0) {
      chart.dispatchAction({ type: "unselect", seriesId: STAR_SERIES_ID, dataType: "edge", dataIndex: previousIndex });
    }
    const index = visibleEdges.findIndex(edge => edge.id === id);
    selectedEdge = index >= 0 ? id : "";
    if (index >= 0) {
      chart.dispatchAction({ type: "select", seriesId: STAR_SERIES_ID, dataType: "edge", dataIndex: index });
    }
    edgePicker.value = selectedEdge;
    showEvidence();
  }

  function highlightResult(index) {
    if (!resultRows.length) return;
    activeResult = (index + resultRows.length) % resultRows.length;
    resultRows.forEach((option, i) => option.setAttribute("aria-selected", String(i === activeResult)));
    const option = resultRows[activeResult];
    search.setAttribute("aria-activedescendant", option.id);
    if (option.offsetTop < results.scrollTop) results.scrollTop = option.offsetTop;
    const bottom = option.offsetTop + option.offsetHeight;
    if (bottom > results.scrollTop + results.clientHeight) results.scrollTop = bottom - results.clientHeight;
  }

  function searchNodes(query) {
    const needle = query.trim().toLocaleLowerCase();
    const matches = vocabulary.filter(([, key]) => key.includes(needle));
    matches.sort((a, b) => Number(b[1] === needle) - Number(a[1] === needle)
      || Number(b[1].startsWith(needle)) - Number(a[1].startsWith(needle)));
    resultRows = matches.slice(0, MAX_RESULTS).map(([node], index) => {
      const option = create("li", node.label);
      option.id = `${results.id}-${index}`;
      option.dataset.nodeId = node.id;
      option.setAttribute("role", "option");
      return option;
    });
    const empty = create("li", "没有匹配的知识点");
    empty.setAttribute("role", "presentation");
    results.replaceChildren(...(resultRows.length ? resultRows : [empty]));
    results.hidden = false;
    results.scrollTop = 0;
    activeResult = -1;
    search.setAttribute("aria-expanded", "true");
    search.removeAttribute("aria-activedescendant");
  }

  function selectOption(event) {
    const option = event.target.closest("[role=option]");
    if (option) focusNode(option.dataset.nodeId);
  }

  listen(search, "click", () => { if (results.hidden) searchNodes(""); });
  listen(search, "focus", () => { search.select(); searchNodes(""); });
  listen(search, "input", () => searchNodes(search.value));
  listen(search, "blur", clearSearch);
  listen(search, "keydown", event => {
    if (event.isComposing) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (results.hidden) searchNodes("");
      highlightResult(activeResult < 0 && event.key === "ArrowUp"
        ? resultRows.length - 1 : activeResult + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Enter" && !results.hidden && resultRows.length) {
      event.preventDefault();
      focusNode(resultRows[Math.max(0, activeResult)].dataset.nodeId);
    } else if (event.key === "Escape") {
      event.preventDefault();
      clearSearch();
    }
  });
  listen(results, "mousedown", event => { if (event.target.closest("[role=option]")) event.preventDefault(); });
  listen(results, "click", selectOption);
  listen(edgePicker, "change", () => focusEdge(edgePicker.value));
  listen(back, "click", () => focusNode(history.pop(), false));
  listen(reset, "click", () => chart.setOption({ series: [{ id: STAR_SERIES_ID, zoom: 1, center: null }] }));

  chart.on("click", ({ dataType, data: item }) => {
    if (dataType === "node") focusNode(item?.id);
    if (dataType === "edge") focusEdge(item?.id);
  });

  function resize() {
    chart.resize();
    const narrow = chartHost.clientWidth < 480;
    const horizontal = narrow ? "19%" : "15%";
    chart.setOption({ series: [{
      id: STAR_SERIES_ID, left: horizontal, right: horizontal, top: 43, bottom: 43,
      label: { width: narrow ? 76 : 114 },
    }] });
  }

  clearSearch();
  resize();
  draw();
  const observer = new ResizeObserver(resize);
  observer.observe(chartHost);
  return () => {
    listeners.abort();
    observer.disconnect();
    chart.dispose();
  };
}
