import { byId } from '/ui.js';
import { editor, visibleNodes, descendants, reparent, changed, CARD } from '/team-model.js';
import { graphNodes, graphEdges } from '/team-view.js';

let observer;
let canvas;
const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
const READABLE_ZOOM = 0.9;

export function paintGraph() {
  if (!byId('graph-nodes')) return;
  byId('graph-nodes').innerHTML = graphNodes();
  byId('graph-edges').innerHTML = graphEdges();
  applyViewport();
}

function applyViewport() {
  const { x, y, zoom } = editor.viewport;
  if (byId('graph-stage'))
    byId('graph-stage').style.transform = `translate(${x}px,${y}px) scale(${zoom})`;
  if (byId('graph-zoom')) byId('graph-zoom').textContent = `${Math.round(zoom * 100)}%`;
  const inverse = zoom ? 1 / zoom : 1;
  document.querySelectorAll('#graph-nodes .node-collapse').forEach((button) => {
    button.style.transform = `scale(${inverse})`;
  });
}

function graphBounds() {
  const nodes = visibleNodes(editor.team);
  if (!nodes.length) return null;
  return {
    minX: Math.min(...nodes.map((node) => node.x)),
    minY: Math.min(...nodes.map((node) => node.y)),
    maxX: Math.max(...nodes.map((node) => node.x)) + CARD.width,
    maxY: Math.max(...nodes.map((node) => node.y)) + CARD.height,
  };
}

function measureFit(current = byId('team-canvas')) {
  const box = graphBounds();
  if (!current || !box || current.clientWidth < 40 || current.clientHeight < 40) return 1;
  return clamp(
    Math.min(
      (current.clientWidth - 72) / (box.maxX - box.minX),
      (current.clientHeight - 150) / (box.maxY - box.minY),
    ),
    0.25,
    1,
  );
}

export function syncGraphChrome() {
  const root = document.querySelector('.collaboration');
  if (!root) return;
  root.classList.toggle('reading-nodes', editor.graphView === 'read');
  root.classList.toggle('inspector-collapsed', editor.inspectorOpen === false);
  const reading = byId('team-reading');
  if (reading) reading.hidden = editor.graphView !== 'read';
  document.querySelectorAll('[data-team-action="read-nodes"]').forEach((button) => {
    const on = editor.graphView === 'read';
    button.classList.toggle('active', on);
    button.setAttribute('aria-pressed', String(on));
  });
  document.querySelectorAll('[data-team-action="show-map"]').forEach((button) => {
    const on = editor.graphView !== 'read';
    button.classList.toggle('active', on);
    button.setAttribute('aria-pressed', String(on));
  });
  const collapse = document.querySelector('[data-team-action="toggle-inspector"]');
  if (collapse) {
    const open = editor.inspectorOpen !== false;
    collapse.setAttribute('aria-expanded', String(open));
    collapse.setAttribute('aria-label', open ? '收起节点设置' : '展开节点设置');
  }
}

function placeViewport(zoom) {
  const current = byId('team-canvas');
  const box = graphBounds();
  if (!current || !box) return;
  const node = editor.team.nodes.find((item) => item.id === editor.selected);
  editor.viewport.zoom = zoom;
  if (node) {
    editor.viewport.x = current.clientWidth / 2 - (node.x + CARD.width / 2) * zoom;
    editor.viewport.y = current.clientHeight / 2 - (node.y + CARD.height / 2) * zoom;
  } else {
    editor.viewport.x = (current.clientWidth - (box.maxX - box.minX) * zoom) / 2 - box.minX * zoom;
    editor.viewport.y = (current.clientHeight - (box.maxY - box.minY) * zoom) / 2 - box.minY * zoom;
  }
}

export function fitGraph({ shrink = false } = {}) {
  const current = byId('team-canvas');
  if (!current) return;
  const fitted = measureFit(current);
  const zoom = shrink ? fitted : Math.max(fitted, 1);
  const box = graphBounds();
  if (!box) return;
  editor.viewport.zoom = zoom;
  editor.viewport.x = (current.clientWidth - (box.maxX - box.minX) * zoom) / 2 - box.minX * zoom;
  editor.viewport.y = (current.clientHeight - (box.maxY - box.minY) * zoom) / 2 - box.minY * zoom;
  applyViewport();
  syncGraphChrome();
}

export function readGraph() {
  editor.graphView = 'read';
  editor.graphChosen = true;
  editor.viewport.userSet = true;
  placeViewport(1);
  applyViewport();
  syncGraphChrome();
}

export function showOverview() {
  editor.graphView = 'overview';
  editor.graphChosen = true;
  editor.viewport.userSet = false;
  fitGraph({ shrink: true });
}

function chooseInitialGraph() {
  const current = byId('team-canvas');
  if (!current || current.clientWidth < 40 || current.clientHeight < 40) return;
  editor.graphChosen = true;
  if (measureFit(current) < READABLE_ZOOM) readGraph();
  else showOverview();
}

export function focusNode(id) {
  const node = editor.team.nodes.find((item) => item.id === id);
  if (!node || !byId('team-canvas')) return;
  let parent = editor.team.nodes.find((item) => item.id === node.parentId);
  let expanded = false;
  while (parent) {
    if (parent.collapsed) expanded = true;
    parent.collapsed = false;
    parent = editor.team.nodes.find((item) => item.id === parent.parentId);
  }
  if (expanded) changed();
  const current = byId('team-canvas');
  editor.viewport.x = current.clientWidth / 2 - (node.x + CARD.width / 2) * editor.viewport.zoom;
  editor.viewport.y = current.clientHeight / 2 - (node.y + CARD.height / 2) * editor.viewport.zoom;
  paintGraph();
}

export function zoomGraph(factor, x, y) {
  const current = byId('team-canvas');
  if (!current) return;
  const old = editor.viewport.zoom;
  const next = clamp(old * factor, 0.25, 1.8);
  x ??= current.clientWidth / 2;
  y ??= current.clientHeight / 2;
  editor.viewport.x = x - ((x - editor.viewport.x) * next) / old;
  editor.viewport.y = y - ((y - editor.viewport.y) * next) / old;
  editor.viewport.zoom = next;
  editor.viewport.userSet = true;
  applyViewport();
}

export function unmountCanvas() {
  observer?.disconnect();
  observer = null;
  canvas = null;
}

export function mountCanvas({ select, update, error, busy }) {
  const current = byId('team-canvas');
  if (!current || current === canvas) {
    if (current) paintGraph();
    return;
  }
  unmountCanvas();
  canvas = current;
  paintGraph();
  chooseInitialGraph();
  observer = new ResizeObserver(() => {
    const viewed = byId('team-canvas');
    if (!viewed || viewed.clientWidth < 40 || viewed.clientHeight < 40) return;
    if (!editor.graphChosen) {
      chooseInitialGraph();
      return;
    }
    if (editor.viewport.userSet || editor.graphView === 'read') return;
    if (measureFit(viewed) < READABLE_ZOOM) {
      readGraph();
      return;
    }
    fitGraph({ shrink: true });
  });
  observer.observe(current);
  let drag = null;
  const dropTarget = (event) => {
    const bounds = current.getBoundingClientRect();
    const x = (event.clientX - bounds.left - editor.viewport.x) / editor.viewport.zoom;
    const y = (event.clientY - bounds.top - editor.viewport.y) / editor.viewport.zoom;
    return visibleNodes(editor.team).find(
      (node) =>
        !drag.positions.has(node.id) &&
        x >= node.x &&
        x <= node.x + CARD.width &&
        y >= node.y &&
        y <= node.y + CARD.height,
    );
  };
  current.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || busy() || event.target.closest('.node-collapse')) return;
    const nodeElement = event.target.closest('[data-node]');
    const id = nodeElement?.dataset.node;
    const positions = new Map();
    if (id) {
      select(id);
      for (const key of [id, ...descendants(editor.team, id)]) {
        const node = editor.team.nodes.find((item) => item.id === key);
        positions.set(key, { x: node.x, y: node.y });
      }
    }
    drag = {
      id,
      x: event.clientX,
      y: event.clientY,
      positions,
      viewport: { ...editor.viewport },
      moved: false,
    };
    current.setPointerCapture(event.pointerId);
    current.classList.add('dragging');
    event.preventDefault();
  });
  current.addEventListener('pointermove', (event) => {
    if (!drag) return;
    const dx = event.clientX - drag.x,
      dy = event.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) < 4 && !drag.moved) return;
    drag.moved = true;
    if (drag.id) {
      for (const [id, position] of drag.positions) {
        const node = editor.team.nodes.find((item) => item.id === id);
        node.x = clamp(position.x + dx / editor.viewport.zoom, -90000, 90000);
        node.y = clamp(position.y + dy / editor.viewport.zoom, -90000, 90000);
        const element = byId('graph-nodes').querySelector(`[data-node="${id}"]`);
        if (element) {
          element.style.left = `${node.x}px`;
          element.style.top = `${node.y}px`;
        }
      }
      byId('graph-edges').innerHTML = graphEdges();
      current
        .querySelectorAll('.drop-target')
        .forEach((element) => element.classList.remove('drop-target'));
      const target = event.shiftKey ? dropTarget(event) : null;
      if (target) current.querySelector(`[data-node="${target.id}"]`)?.classList.add('drop-target');
    } else {
      editor.viewport.x = drag.viewport.x + dx;
      editor.viewport.y = drag.viewport.y + dy;
      applyViewport();
    }
  });
  const finish = (event, cancel = false) => {
    if (!drag) return;
    if (drag.id && drag.moved) {
      if (cancel) {
        for (const [id, position] of drag.positions)
          Object.assign(
            editor.team.nodes.find((node) => node.id === id),
            position,
          );
      } else {
        const target = event.shiftKey ? dropTarget(event) : null;
        if (target) {
          try {
            reparent(editor.team, drag.id, target.id);
          } catch (err) {
            error(err.message);
          }
        }
        changed();
      }
      paintGraph();
      update();
    }
    drag = null;
    current.classList.remove('dragging');
    if (current.hasPointerCapture(event.pointerId)) current.releasePointerCapture(event.pointerId);
  };
  current.addEventListener('pointerup', (event) => finish(event));
  current.addEventListener('pointercancel', (event) => finish(event, true));
  current.addEventListener(
    'wheel',
    (event) => {
      if (busy()) return;
      event.preventDefault();
      const bounds = current.getBoundingClientRect();
      zoomGraph(
        event.deltaY < 0 ? 1.08 : 1 / 1.08,
        event.clientX - bounds.left,
        event.clientY - bounds.top,
      );
    },
    { passive: false },
  );
  current.addEventListener('keydown', (event) => {
    if (event.target !== current || busy()) return;
    if (event.key === '+' || event.key === '=') zoomGraph(1.15);
    else if (event.key === '-') zoomGraph(1 / 1.15);
    else if (event.key === '0') showOverview();
    else return;
    event.preventDefault();
  });
}
