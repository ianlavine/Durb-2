(() => {
  const canvas = document.getElementById('sandboxCanvas');
  const ctx = canvas.getContext('2d');
  const resetButton = document.getElementById('resetButton');
  const generateButton = document.getElementById('generateButton');
  const movementListEl = document.getElementById('movementList');

  const DEFAULT_SERVICE_BASE = 'http://127.0.0.1:5051';
  let configuredBase = window.__SHARP_SERVICE_URL_BASE || window.__SHARP_SERVICE_URL || DEFAULT_SERVICE_BASE;
  if (configuredBase.endsWith('/resolve')) {
    configuredBase = configuredBase.slice(0, -'/resolve'.length);
  }
  configuredBase = configuredBase.replace(/\/$/, '');
  const RESOLVE_URL = `${configuredBase || DEFAULT_SERVICE_BASE}/resolve`;
  const GENERATE_URL = `${configuredBase || DEFAULT_SERVICE_BASE}/generate`;

  const MAX_MOVE_CAP = 12.0;
  const NODE_RADIUS = 16;
  const NODE_HIT_RADIUS = 22;
  const NODE_MOVE_DURATION_MS = 620;
  const TRIANGLE_SPACING = 28;
  const TRIANGLE_LENGTH = 26;
  const TRIANGLE_WIDTH = 14;
  const EDGE_COLOR = '#68d5ff';
  const EDGE_OUTLINE = 'rgba(9, 20, 32, 0.65)';
  const EPSILON = 1e-6;
  const DEFAULT_WORLD_BOUNDS = { minX: 0, minY: -18, width: 275, height: 108 };

  let renderWidth = window.innerWidth;
  let renderHeight = window.innerHeight;
  let currentDpr = window.devicePixelRatio || 1;
  let view = null;
  let worldBounds = { ...DEFAULT_WORLD_BOUNDS };

  const movementLogs = [];

  const state = {
    nodes: [],
    edges: [],
    nextNodeId: 1,
    nextEdgeId: 1,
    selectedNodeId: null,
    hoverNodeId: null,
    mouse: { x: 0, y: 0 },
  };

  function resetMovementLogs() {
    movementLogs.length = 0;
    if (movementListEl) {
      movementListEl.innerHTML = '<div class="movement-empty">No movements yet.</div>';
    }
  }

  function appendMovementLog(message) {
    const timestamp = new Date();
    movementLogs.push({ message, timestamp });
    if (!movementListEl) return;
    if (movementLogs.length === 1) {
      movementListEl.innerHTML = '';
    }
    const entry = document.createElement('div');
    entry.className = 'movement-entry';
    const timeEl = document.createElement('time');
    timeEl.textContent = timestamp.toLocaleTimeString();
    entry.appendChild(timeEl);
    const msgEl = document.createElement('div');
    msgEl.textContent = message;
    entry.appendChild(msgEl);
    movementListEl.appendChild(entry);
    movementListEl.scrollTop = movementListEl.scrollHeight;
  }

  function recalculateView() {
    let minX = worldBounds.minX;
    let minY = worldBounds.minY;
    let maxX = worldBounds.minX + worldBounds.width;
    let maxY = worldBounds.minY + worldBounds.height;

    if (state.nodes.length) {
      for (const node of state.nodes) {
        if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) continue;
        minX = Math.min(minX, node.x);
        minY = Math.min(minY, node.y);
        maxX = Math.max(maxX, node.x);
        maxY = Math.max(maxY, node.y);
      }
    }

    const padX = (maxX - minX) * 0.08 + 6;
    const padY = (maxY - minY) * 0.08 + 6;
    minX -= padX;
    maxX += padX;
    minY -= padY;
    maxY += padY;

    const width = Math.max(1, maxX - minX);
    const height = Math.max(1, maxY - minY);
    const playableWidth = Math.max(1, renderWidth - 40);
    const playableHeight = Math.max(1, renderHeight - 40);
    const scale = Math.min(playableWidth / width, playableHeight / height);
    const offsetX = (renderWidth - scale * width) / 2 - scale * minX;
    const offsetY = (renderHeight - scale * height) / 2 - scale * minY;

    view = {
      minX,
      minY,
      maxX,
      maxY,
      scaleX: scale,
      scaleY: scale,
      offsetX,
      offsetY,
    };
  }

  function ensureView() {
    if (!view) {
      recalculateView();
    }
  }

  function worldToScreen(x, y) {
    ensureView();
    if (!view) return [x, y];
    return [x * view.scaleX + view.offsetX, y * view.scaleY + view.offsetY];
  }

  function screenToWorld(x, y) {
    ensureView();
    if (!view) return { x, y };
    return {
      x: (x - view.offsetX) / view.scaleX,
      y: (y - view.offsetY) / view.scaleY,
    };
  }

  function resizeCanvas() {
    currentDpr = window.devicePixelRatio || 1;
    renderWidth = window.innerWidth;
    renderHeight = window.innerHeight;
    canvas.width = Math.round(renderWidth * currentDpr);
    canvas.height = Math.round(renderHeight * currentDpr);
    canvas.style.width = `${renderWidth}px`;
    canvas.style.height = `${renderHeight}px`;
    recalculateView();
  }

  function clearSandbox() {
    state.nodes = [];
    state.edges = [];
    state.nextNodeId = 1;
    state.nextEdgeId = 1;
    state.selectedNodeId = null;
    state.hoverNodeId = null;
    worldBounds = { ...DEFAULT_WORLD_BOUNDS };
    resetMovementLogs();
    recalculateView();
  }

  function createNode(worldX, worldY, idOverride) {
    const hasOverride = Number.isFinite(idOverride);
    const assignedId = hasOverride ? Number(idOverride) : state.nextNodeId++;
    if (hasOverride) {
      state.nextNodeId = Math.max(state.nextNodeId, assignedId + 1);
    }
    return {
      id: assignedId,
      x: worldX,
      y: worldY,
      radius: NODE_RADIUS,
      attachedEdgeIds: [],
      moveStartX: worldX,
      moveStartY: worldY,
      targetX: worldX,
      targetY: worldY,
      moveStartTime: 0,
      moveDuration: 0,
      animating: false,
    };
  }

  function addNode(worldX, worldY) {
    state.nodes.push(createNode(worldX, worldY));
    recalculateView();
  }

  function getNodeById(id) {
    return state.nodes.find((n) => n.id === id) || null;
  }

  function edgeExists(a, b) {
    return state.edges.some((edge) => (
      (edge.sourceId === a && edge.targetId === b) ||
      (edge.sourceId === b && edge.targetId === a)
    ));
  }

  function removeEdgeById(edgeId) {
    const index = state.edges.findIndex((edge) => edge.id === edgeId);
    if (index === -1) return;
    const [removed] = state.edges.splice(index, 1);
    const source = getNodeById(removed.sourceId);
    const target = getNodeById(removed.targetId);
    if (source) {
      source.attachedEdgeIds = source.attachedEdgeIds.filter((id) => id !== edgeId);
    }
    if (target) {
      target.attachedEdgeIds = target.attachedEdgeIds.filter((id) => id !== edgeId);
    }
  }

  function addEdge(sourceId, targetId) {
    if (sourceId === targetId) return;
    if (edgeExists(sourceId, targetId)) return;

    const edge = {
      id: state.nextEdgeId++,
      sourceId,
      targetId,
      warpSegments: [],
    };
    state.edges.push(edge);

    const sourceNode = getNodeById(sourceId);
    const targetNode = getNodeById(targetId);
    if (sourceNode) sourceNode.attachedEdgeIds.push(edge.id);
    if (targetNode) targetNode.attachedEdgeIds.push(edge.id);

    const removed = destroyIntersectingEdges(edge);
    if (removed.length) {
      appendMovementLog(`Destroyed edges ${removed.join(', ')} due to intersection.`);
    }

    requestSharpAngleResolution(edge);
  }

  function findNodeAt(screenX, screenY) {
    for (let i = state.nodes.length - 1; i >= 0; i -= 1) {
      const node = state.nodes[i];
      const [nx, ny] = worldToScreen(node.x, node.y);
      if (Math.hypot(screenX - nx, screenY - ny) <= NODE_HIT_RADIUS) {
        return node;
      }
    }
    return null;
  }

  function handleCanvasClick(event) {
    const rect = canvas.getBoundingClientRect();
    const screenX = event.clientX - rect.left;
    const screenY = event.clientY - rect.top;

    const node = findNodeAt(screenX, screenY);
    if (!node) {
      if (state.selectedNodeId !== null) {
        state.selectedNodeId = null;
        return;
      }
      const worldPoint = screenToWorld(screenX, screenY);
      addNode(worldPoint.x, worldPoint.y);
      return;
    }

    if (state.selectedNodeId === null) {
      state.selectedNodeId = node.id;
    } else if (state.selectedNodeId === node.id) {
      state.selectedNodeId = null;
    } else {
      addEdge(state.selectedNodeId, node.id);
      state.selectedNodeId = null;
    }
  }

  function handleMouseMove(event) {
    const rect = canvas.getBoundingClientRect();
    state.mouse.x = event.clientX - rect.left;
    state.mouse.y = event.clientY - rect.top;

    const hoverNode = findNodeAt(state.mouse.x, state.mouse.y);
    state.hoverNodeId = hoverNode ? hoverNode.id : null;
  }

  async function requestSharpAngleResolution(newEdge) {
    if (!RESOLVE_URL) {
      appendMovementLog('No resolver URL configured.');
      return;
    }

    const payload = {
      nodes: state.nodes.map((node) => ({
        id: node.id,
        x: node.x,
        y: node.y,
      })),
      edges: state.edges.map((edge) => ({
        id: edge.id,
        sourceId: edge.sourceId,
        targetId: edge.targetId,
        warpSegments: edge.warpSegments || [],
      })),
      newEdgeId: newEdge.id,
    };

    try {
      const res = await fetch(RESOLVE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        appendMovementLog(`Resolver error: HTTP ${res.status}`);
        return;
      }

      const data = await res.json();
      const movements = Array.isArray(data.movements) ? data.movements : [];
      if (!movements.length) {
        appendMovementLog('No movement required.');
      }
      for (const move of movements) {
        const node = getNodeById(move.nodeId);
        if (!node) continue;
        const reasons = Array.isArray(move.limitReasons)
          ? move.limitReasons
          : move.limitReason
            ? [move.limitReason]
            : [];
        const moved = move.moved !== false;
        if (moved && Number.isFinite(move.x) && Number.isFinite(move.y)) {
          const startX = node.targetX ?? node.x;
          const startY = node.targetY ?? node.y;
          const distance = Math.hypot(move.x - startX, move.y - startY);
          moveNodeTo(node, move.x, move.y);
          let message = `Node ${move.nodeId} moved ${distance.toFixed(3)} `;
          if (reasons.includes('collision-limit')) {
            message += ' — limited by intersecting pipes';
          }
          appendMovementLog(message);
        } else if (reasons.length) {
          appendMovementLog(
            `Node ${move.nodeId} movement blocked (${reasons.includes('collision-limit') ? 'existing pipes' : reasons.join(', ')})`
          );
        }
      }
    } catch (error) {
      console.error('Sharp angle service failed', error);
      appendMovementLog('Resolver unavailable. Check console.');
    }
  }

  async function generateGraphFromServer(mode = 'sparse') {
    if (!GENERATE_URL) {
      appendMovementLog('No generator URL configured.');
      return;
    }
    try {
      const res = await fetch(GENERATE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      if (!res.ok) {
        appendMovementLog(`Generator error: HTTP ${res.status}`);
        return;
      }
      const data = await res.json();
      applyGeneratedGraph(data);
    } catch (error) {
      console.error('Graph generation failed', error);
      appendMovementLog('Generator unavailable. Check console.');
    }
  }

  function applyGeneratedGraph(data) {
    clearSandbox();
    const screen = data && data.screen;
    if (screen && Number.isFinite(screen.width) && Number.isFinite(screen.height)) {
      worldBounds = {
        minX: Number.isFinite(screen.minX) ? screen.minX : DEFAULT_WORLD_BOUNDS.minX,
        minY: Number.isFinite(screen.minY) ? screen.minY : DEFAULT_WORLD_BOUNDS.minY,
        width: screen.width || DEFAULT_WORLD_BOUNDS.width,
        height: screen.height || DEFAULT_WORLD_BOUNDS.height,
      };
    }

    state.nodes = [];
    state.edges = [];
    state.nextNodeId = 0;
    state.nextEdgeId = 0;

    const nodesData = Array.isArray(data?.nodes) ? data.nodes : [];
    nodesData.forEach((nodeInfo) => {
      const node = createNode(Number(nodeInfo?.x ?? 0), Number(nodeInfo?.y ?? 0), Number(nodeInfo?.id));
      node.attachedEdgeIds = [];
      state.nodes.push(node);
    });

    const edgesData = Array.isArray(data?.edges) ? data.edges : [];
    edgesData.forEach((edgeInfo) => {
      const sourceId = Number(edgeInfo?.sourceId ?? edgeInfo?.source);
      const targetId = Number(edgeInfo?.targetId ?? edgeInfo?.target);
      if (!Number.isFinite(sourceId) || !Number.isFinite(targetId)) {
        return;
      }
      const hasOverride = Number.isFinite(edgeInfo?.id);
      const assignedId = hasOverride ? Number(edgeInfo.id) : state.nextEdgeId++;
      if (hasOverride) {
        state.nextEdgeId = Math.max(state.nextEdgeId, assignedId + 1);
      }
      const edge = {
        id: assignedId,
        sourceId,
        targetId,
        warpSegments: edgeInfo?.warpSegments || [],
      };
      state.edges.push(edge);
      const sourceNode = getNodeById(sourceId);
      const targetNode = getNodeById(targetId);
      if (sourceNode && !sourceNode.attachedEdgeIds.includes(edge.id)) {
        sourceNode.attachedEdgeIds.push(edge.id);
      }
      if (targetNode && !targetNode.attachedEdgeIds.includes(edge.id)) {
        targetNode.attachedEdgeIds.push(edge.id);
      }
    });

    state.selectedNodeId = null;
    state.hoverNodeId = null;
    recalculateView();
    appendMovementLog(
      `Generated ${data?.mode || 'sparse'} graph (${state.nodes.length} nodes / ${state.edges.length} edges).`
    );
  }

  function drawEdge(edge) {
    const source = getNodeById(edge.sourceId);
    const target = getNodeById(edge.targetId);
    if (!source || !target) return;
    const [sx, sy] = worldToScreen(source.x, source.y);
    const [tx, ty] = worldToScreen(target.x, target.y);
    const dx = tx - sx;
    const dy = ty - sy;
    const length = Math.hypot(dx, dy);
    if (length <= EPSILON) return;

    const angle = Math.atan2(dy, dx);
    const count = Math.max(1, Math.floor(length / TRIANGLE_SPACING));
    const step = length / count;
    const ux = dx / length;
    const uy = dy / length;

    for (let i = 0; i < count; i += 1) {
      const dist = step * (i + 0.5);
      const cx = sx + ux * dist;
      const cy = sy + uy * dist;
      drawTriangle(cx, cy, angle);
    }
  }

  function drawTriangle(cx, cy, angle) {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(angle);
    ctx.beginPath();
    ctx.moveTo(-TRIANGLE_LENGTH * 0.5, -TRIANGLE_WIDTH * 0.5);
    ctx.lineTo(-TRIANGLE_LENGTH * 0.5, TRIANGLE_WIDTH * 0.5);
    ctx.lineTo(TRIANGLE_LENGTH * 0.5, 0);
    ctx.closePath();
    ctx.fillStyle = EDGE_COLOR;
    ctx.strokeStyle = EDGE_OUTLINE;
    ctx.lineWidth = 1.5;
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  function easeInOut(t) {
    return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
  }

  function moveNodeTo(node, worldX, worldY) {
    if (Math.hypot(worldX - node.x, worldY - node.y) <= EPSILON) {
      node.x = worldX;
      node.y = worldY;
      node.targetX = worldX;
      node.targetY = worldY;
      node.animating = false;
      recalculateView();
      return;
    }

    node.moveStartX = node.x;
    node.moveStartY = node.y;
    node.targetX = worldX;
    node.targetY = worldY;
    node.moveStartTime = performance.now();
    node.moveDuration = NODE_MOVE_DURATION_MS;
    node.animating = true;
    recalculateView();
  }

  function updateNodePositions(now) {
    let changed = false;
    for (const node of state.nodes) {
      if (!node.animating) continue;
      const elapsed = now - node.moveStartTime;
      const t = Math.min(1, elapsed / node.moveDuration);
      const eased = easeInOut(t);
      node.x = node.moveStartX + (node.targetX - node.moveStartX) * eased;
      node.y = node.moveStartY + (node.targetY - node.moveStartY) * eased;
      if (t >= 1 - EPSILON) {
        node.x = node.targetX;
        node.y = node.targetY;
        node.animating = false;
        changed = true;
      }
    }
    if (changed) {
      recalculateView();
    }
  }

  function orientation(ax, ay, bx, by, cx, cy) {
    const val = (by - ay) * (cx - bx) - (bx - ax) * (cy - by);
    if (Math.abs(val) < 1e-9) return 0;
    return val > 0 ? 1 : 2;
  }

  function segmentsIntersect(ax, ay, bx, by, cx, cy, dx, dy) {
    const o1 = orientation(ax, ay, bx, by, cx, cy);
    const o2 = orientation(ax, ay, bx, by, dx, dy);
    const o3 = orientation(cx, cy, dx, dy, ax, ay);
    const o4 = orientation(cx, cy, dx, dy, bx, by);
    if (o1 !== o2 && o3 !== o4) return true;
    const onSegment = (px, py, qx, qy, rx, ry) =>
      qx <= Math.max(px, rx) + EPSILON &&
      qx + EPSILON >= Math.min(px, rx) &&
      qy <= Math.max(py, ry) + EPSILON &&
      qy + EPSILON >= Math.min(py, ry);
    if (o1 === 0 && onSegment(ax, ay, cx, cy, bx, by)) return true;
    if (o2 === 0 && onSegment(ax, ay, dx, dy, bx, by)) return true;
    if (o3 === 0 && onSegment(cx, cy, ax, ay, dx, dy)) return true;
    if (o4 === 0 && onSegment(cx, cy, bx, by, dx, dy)) return true;
    return false;
  }

  function edgesShareNode(edgeA, edgeB) {
    return (
      edgeA.sourceId === edgeB.sourceId ||
      edgeA.sourceId === edgeB.targetId ||
      edgeA.targetId === edgeB.sourceId ||
      edgeA.targetId === edgeB.targetId
    );
  }

  function destroyIntersectingEdges(newEdge) {
    const removed = [];
    for (const edge of [...state.edges]) {
      if (edge.id === newEdge.id) continue;
      if (edgesShareNode(edge, newEdge)) continue;
      const aSource = getNodeById(edge.sourceId);
      const aTarget = getNodeById(edge.targetId);
      const bSource = getNodeById(newEdge.sourceId);
      const bTarget = getNodeById(newEdge.targetId);
      if (!aSource || !aTarget || !bSource || !bTarget) continue;
      if (
        segmentsIntersect(
          aSource.x,
          aSource.y,
          aTarget.x,
          aTarget.y,
          bSource.x,
          bSource.y,
          bTarget.x,
          bTarget.y,
        )
      ) {
        removeEdgeById(edge.id);
        removed.push(edge.id);
      }
    }
    return removed;
  }

  function draw(now = performance.now()) {
    updateNodePositions(now);
    ensureView();

    ctx.setTransform(currentDpr, 0, 0, currentDpr, 0, 0);
    ctx.clearRect(0, 0, renderWidth, renderHeight);
    ctx.fillStyle = '#05050a';
    ctx.fillRect(0, 0, renderWidth, renderHeight);

    for (const edge of state.edges) {
      drawEdge(edge);
    }

    if (state.selectedNodeId !== null) {
      const startNode = getNodeById(state.selectedNodeId);
      if (startNode) {
        const [sx, sy] = worldToScreen(startNode.x, startNode.y);
        ctx.setLineDash([6, 6]);
        ctx.strokeStyle = 'rgba(255,255,255,0.35)';
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(state.mouse.x, state.mouse.y);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    for (const node of state.nodes) {
      const [screenX, screenY] = worldToScreen(node.x, node.y);
      const isSelected = node.id === state.selectedNodeId;
      const isHovered = node.id === state.hoverNodeId;
      ctx.beginPath();
      ctx.arc(screenX, screenY, NODE_RADIUS, 0, Math.PI * 2);
      ctx.fillStyle = isSelected ? '#ffd166' : '#ff8c42';
      if (isHovered && !isSelected) {
        ctx.fillStyle = '#ffb482';
      }
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#1b1b2d';
      ctx.stroke();

      ctx.fillStyle = '#0f0f17';
      ctx.font = '12px "Fira Code", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(node.id, screenX, screenY);
    }

    requestAnimationFrame(draw);
  }

  canvas.addEventListener('click', handleCanvasClick);
  canvas.addEventListener('mousemove', handleMouseMove);
  resetButton?.addEventListener('click', clearSandbox);
  generateButton?.addEventListener('click', () => generateGraphFromServer('sparse'));
  window.addEventListener('resize', resizeCanvas);

  resizeCanvas();
  clearSandbox();
  draw();
})();
