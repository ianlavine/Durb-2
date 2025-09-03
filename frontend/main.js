(() => {
  const HOST = location.hostname || 'localhost';
  const WS_URL = `ws://${HOST}:8765`;

  const config = {
    type: Phaser.AUTO,
    parent: 'game',
    width: window.innerWidth,
    height: window.innerHeight,
    backgroundColor: '#d0d0d0',
    scene: { preload, create, update },
  };

  const game = new Phaser.Game(config);

  let ws = null;
  let screen = null;
  let nodes = new Map(); // id -> {x,y,size,owner}
  let edges = new Map(); // id -> {source,target,bidir,forward,on,flowing}

  let graphicsEdges;
  let graphicsNodes;
  let statusText;
  let view = null; // {minX, minY, maxX, maxY, scale, offsetX, offsetY}
  let players = new Map(); // id -> {color}
  let gameEnded = false;
  let overlayMsg = null;
  let hudContainer = null; // repurposed as gold bar container (right side)
  let goldSegments = []; // array of {wrap, fill}
  let myPlayerId = null;
  let phase = 'picking';
  let myPicked = false;
  let goldValue = 0; // 0..5
  let nodeMaxJuice = 50;
  let hoveredNodeId = null;
  let hoveredEdgeId = null;

  function preload() {}

  function create() {
    graphicsEdges = this.add.graphics();
    graphicsNodes = this.add.graphics();
    statusText = this.add.text(10, 10, 'Connect to start a game', { font: '16px monospace', color: '#cccccc' });

    tryConnectWS();
    const menu = document.getElementById('menu');
    const playBtn = document.getElementById('playBtn');
    if (playBtn) {
      playBtn.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          document.getElementById('lobby').style.display = 'block';
          ws.send(JSON.stringify({ type: 'joinLobby' }));
        }
      });
    }
    // Quit overlay button
    const quitBtn = document.createElement('button');
    quitBtn.textContent = 'Forfeit';
    Object.assign(quitBtn.style, { position: 'absolute', left: '10px', top: '10px', zIndex: 10, padding: '8px 14px', borderRadius: '8px', border: 'none', background: '#ff5555', color: '#111', cursor: 'pointer', display: 'none' });
    document.body.appendChild(quitBtn);
    quitBtn.addEventListener('click', () => {
      if (!gameEnded) {
        const token = localStorage.getItem('token');
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'quitGame', token }));
        }
      } else {
        // After game over, quit returns to menu
        const menu = document.getElementById('menu');
        const lobby = document.getElementById('lobby');
        if (menu && lobby) {
          lobby.textContent = '';
          lobby.style.display = 'none';
          menu.classList.remove('hidden');
        }
        quitBtn.style.display = 'none';
        if (overlayMsg) overlayMsg.style.display = 'none';
        // Hide HUD when returning to menu
        if (hudContainer) hudContainer.style.display = 'none';
        nodes.clear();
        edges.clear();
        redrawStatic();
      }
    });
    // Toggle quit button visibility based on menu
    const observer = new MutationObserver(() => {
      const menuVisible = !menu.classList.contains('hidden');
      quitBtn.style.display = menuVisible ? 'none' : 'block';
    });
    observer.observe(menu, { attributes: true, attributeFilter: ['class'] });
    // Also hide win/lose overlay when menu is visible
    const menuObserver = new MutationObserver(() => {
      const menuVisible = !menu.classList.contains('hidden');
      if (menuVisible && overlayMsg) overlayMsg.style.display = 'none';
    });
    menuObserver.observe(menu, { attributes: true, attributeFilter: ['class'] });

    // Overlay message (centered) for Win/Lose messages
    overlayMsg = document.createElement('div');
    Object.assign(overlayMsg.style, {
      position: 'absolute',
      left: '50%',
      top: '15%',
      transform: 'translateX(-50%)',
      color: '#ffffff',
      fontFamily: 'monospace',
      fontSize: '64px',
      letterSpacing: '2px',
      textShadow: '0 2px 8px rgba(0,0,0,0.6)',
      display: 'none',
      zIndex: 9,
    });
    document.body.appendChild(overlayMsg);

    // Right-side vertical gold bar with 5 sections
    const gold = document.createElement('div');
    Object.assign(gold.style, {
      position: 'absolute',
      right: '20px',
      top: '50%',
      transform: 'translateY(-50%)',
      width: '48px',
      height: '420px',
      background: '#1a1a1a',
      border: '1px solid #444',
      borderRadius: '10px',
      padding: '6px',
      display: 'none',
      zIndex: 8,
      boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
      boxSizing: 'border-box',
    });
    const stack = document.createElement('div');
    Object.assign(stack.style, {
      position: 'relative',
      width: '100%',
      height: '100%',
      display: 'flex',
      flexDirection: 'column-reverse', // fill from bottom up
      gap: '6px',
    });
    gold.appendChild(stack);
    // Create 5 segments
    goldSegments = [];
    for (let i = 0; i < 5; i++) {
      const segment = document.createElement('div');
      Object.assign(segment.style, {
        position: 'relative',
        width: '100%',
        flex: '1 1 0',
        background: '#2a2a2a',
        border: '1px solid #555',
        borderRadius: '6px',
        overflow: 'hidden',
      });
      const fill = document.createElement('div');
      Object.assign(fill.style, {
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: 0,
        height: '0%',
        background: '#ffd700', // gold color
      });
      segment.appendChild(fill);
      stack.appendChild(segment);
      goldSegments.push({ wrap: segment, fill });
    }
    document.body.appendChild(gold);
    hudContainer = gold;
    window.addEventListener('resize', () => {
      this.scale.resize(window.innerWidth, window.innerHeight);
      computeTransform(this.scale.gameSize.width, this.scale.gameSize.height);
      // Recenter top prompt if visible
      if (statusText && statusText.visible) {
        statusText.setPosition(this.scale.gameSize.width / 2 - statusText.width / 2, 16);
      }
      redrawStatic();
    });
  }

  function update() {}

  function tryConnectWS() {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      console.log('WS connected');
      if (statusText) statusText.setText('Connected. Checking for active game...');
      // Ask server to send init if a game exists
      const storedToken = localStorage.getItem('token');
      if (storedToken) ws.send(JSON.stringify({ type: 'requestInit', token: storedToken }));
    };
    ws.onclose = () => {
      console.log('WS disconnected, retrying in 2s');
      if (statusText) statusText.setText('Disconnected. Retrying...');
      setTimeout(tryConnectWS, 2000);
    };
    ws.onerror = (e) => console.error('WS error', e);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'init') handleInit(msg);
      else if (msg.type === 'tick') handleTick(msg);
      else if (msg.type === 'lobbyJoined') handleLobby(msg);
      else if (msg.type === 'gameOver') handleGameOver(msg);
    };
  }

  function handleInit(msg) {
    gameEnded = false;
    // Ensure Forfeit label during active game
    const buttons = document.getElementsByTagName('button');
    for (const b of buttons) {
      if (b.textContent === 'Quit') b.textContent = 'Forfeit';
    }
    screen = msg.screen || null;
    nodes.clear();
    edges.clear();
    players.clear();

    for (const arr of msg.nodes) {
      const [id, x, y, size, owner] = arr;
      nodes.set(id, { x, y, size, owner });
    }
    for (const arr of msg.edges) {
      const [id, s, t, bidir, fwd] = arr;
      edges.set(id, { source: s, target: t, bidir: !!bidir, forward: !!fwd, on: false, flowing: false });
    }
    if (Array.isArray(msg.players)) {
      for (const arr of msg.players) {
        const [id, color] = arr;
        players.set(id, { color });
      }
    }
    if (msg.token) localStorage.setItem('token', msg.token);
    if (msg.myPlayerId != null) localStorage.setItem('myPlayerId', String(msg.myPlayerId));
    myPlayerId = (msg.myPlayerId != null) ? Number(msg.myPlayerId) : Number(localStorage.getItem('myPlayerId') || '0');
    if (msg.settings && typeof msg.settings.nodeMaxJuice === 'number') nodeMaxJuice = msg.settings.nodeMaxJuice;
    // Phase and pick status
    phase = typeof msg.phase === 'string' ? msg.phase : 'picking';
    myPicked = false;
    if (Array.isArray(msg.picked)) {
      for (const [pid, picked] of msg.picked) {
        if (Number(pid) === myPlayerId) myPicked = !!picked;
      }
    }
    // Gold value for my player
    goldValue = 0;
    if (Array.isArray(msg.gold)) {
      for (const [pid, val] of msg.gold) {
        if (Number(pid) === myPlayerId) goldValue = Math.max(0, Math.min(5, Number(val) || 0));
      }
    }
    computeTransform(game.scale.gameSize.width, game.scale.gameSize.height);
    const menu = document.getElementById('menu');
    menu && menu.classList.add('hidden');
    if (overlayMsg) overlayMsg.style.display = 'none';
    redrawStatic();
    if (statusText) {
      statusText.setText(phase === 'picking' && !myPicked ? 'Choose Starting Node' : '');
      statusText.setStyle({ font: '48px monospace', color: '#ffffff' });
      statusText.setPosition(game.scale.gameSize.width / 2 - statusText.width / 2, 16);
      statusText.setDepth(10);
      statusText.setVisible(phase === 'picking' && !myPicked);
    }
    updateGoldBar();
  }

  function handleLobby(msg) {
    const lobby = document.getElementById('lobby');
    if (lobby) {
      lobby.textContent = msg.status === 'waiting' ? 'Waiting for another player...' : 'Starting...';
    }
    if (msg.token) localStorage.setItem('token', msg.token);
    // Hide the PLAY button while waiting
    const playBtn = document.getElementById('playBtn');
    if (playBtn) playBtn.style.display = 'none';
  }

  function handleGameOver(msg) {
    const myId = Number(localStorage.getItem('myPlayerId') || '0');
    const text = (msg.winnerId === myId) ? 'You win' : 'You lose';
    if (overlayMsg) {
      overlayMsg.textContent = text;
      overlayMsg.style.display = 'block';
    }
    gameEnded = true;
    const buttons = document.getElementsByTagName('button');
    for (const b of buttons) {
      if (b.textContent === 'Forfeit') b.textContent = 'Quit';
    }
    // Do not clear board; leave last state visible (stale, no updates)
    redrawStatic();
    // Ensure menu elements are ready for return
    const menu = document.getElementById('menu');
    const lobby = document.getElementById('lobby');
    const playBtn = document.getElementById('playBtn');
    if (lobby) {
      lobby.textContent = '';
      lobby.style.display = 'none';
    }
    if (playBtn) {
      playBtn.style.display = 'block';
    }
    // Don't show menu immediately - wait for user to click Quit button
  }

  function handleTick(msg) {
    // Update states (not visualized yet, but we store them for future use)
    if (Array.isArray(msg.nodes)) {
      for (const arr of msg.nodes) {
        const [id, size, owner] = arr;
        const n = nodes.get(id);
        if (n) {
          n.size = size;
          n.owner = owner;
        }
      }
    }
    if (Array.isArray(msg.edges)) {
      for (const arr of msg.edges) {
        const [id, on, flowing, fwd] = arr;
        const e = edges.get(id);
        if (e) {
          e.on = !!on;
          e.flowing = !!flowing;
          e.forward = !!fwd;
        }
      }
    }
    // Phase and pick status, and gold update
    if (typeof msg.phase === 'string') phase = msg.phase;
    if (Array.isArray(msg.picked)) {
      for (const [pid, picked] of msg.picked) {
        if (Number(pid) === myPlayerId) myPicked = !!picked;
      }
    }
    if (Array.isArray(msg.gold)) {
      for (const [pid, val] of msg.gold) {
        if (Number(pid) === myPlayerId) goldValue = Math.max(0, Math.min(5, Number(val) || 0));
      }
    }
    if (statusText) {
      statusText.setText(phase === 'picking' && !myPicked ? 'Choose Starting Node' : '');
      statusText.setStyle({ font: '48px monospace', color: '#ffffff' });
      statusText.setPosition(game.scale.gameSize.width / 2 - statusText.width / 2, 16);
      statusText.setDepth(10);
      statusText.setVisible(phase === 'picking' && !myPicked);
    }
    updateGoldBar();
    redrawStatic();
  }

  function redrawStatic() {
    // Draw edges first, then nodes
    graphicsEdges.clear();
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) {
      graphicsNodes.clear();
      // Hide HUD when menu is visible (graph is not drawn)
      if (hudContainer) hudContainer.style.display = 'none';
      return; // Do not draw game under menu
    }
    
    // Show gold bar when graph is being drawn and we have nodes/game data
    if (hudContainer && nodes.size > 0) {
      hudContainer.style.display = 'block';
    }
    for (const [id, e] of edges.entries()) {
      const s = nodes.get(e.source);
      const t = nodes.get(e.target);
      if (!s || !t) continue;
      drawEdge(e, s, t, id);
    }

    graphicsNodes.clear();
    for (const [id, n] of nodes.entries()) {
      const [nx, ny] = worldToScreen(n.x, n.y);
      const color = ownerToColor(n.owner);
      graphicsNodes.fillStyle(color, 1);
      const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
      const radius = Math.max(0.3, 0.5 * Math.sqrt(Math.max(0, n.size ?? n.juice ?? 0.3))) * baseScale;
      const r = Math.max(1, radius);
      graphicsNodes.fillCircle(nx, ny, r);
      
      // Max-size thick black border
      const juiceVal = (n.juice != null ? n.juice : (n.size || 0));
      if (juiceVal >= nodeMaxJuice - 1e-6) {
        graphicsNodes.lineStyle(4, 0x000000, 1);
        graphicsNodes.strokeCircle(nx, ny, r + 2);
      }
      
      // Hover effect: player's color border when eligible (picking phase)
      if (hoveredNodeId === id && phase === 'picking' && !myPicked && (n.owner == null)) {
        const myColor = ownerToColor(myPlayerId);
        graphicsNodes.lineStyle(3, myColor, 1);
        graphicsNodes.strokeCircle(nx, ny, r + 3);
      }
    }
  }

  function computeTransform(viewW, viewH) {
    if (nodes.size === 0) return;
    let minX = 0, minY = 0, maxX = 100, maxY = 100;
    // Nodes are already in 0..100 logical space; fit that rect to screen
    const padding = 60; // top/bottom padding
    const rightReservedPx = 96; // space for gold bar and margin
    const width = Math.max(1, maxX - minX);
    const height = Math.max(1, maxY - minY);
    const scaleX = (viewW - padding * 2 - rightReservedPx) / width;
    const scaleY = (viewH - padding * 2) / height;
    // Use independent scaling for x and y to fully utilize window aspect
    // Left edge can go to the edge; anchor left and center vertically
    const offsetX = padding - scaleX * minX;
    const offsetY = (viewH - scaleY * height) / 2 - scaleY * minY;
    view = { minX, minY, maxX, maxY, scaleX, scaleY, offsetX, offsetY };
  }

  function worldToScreen(x, y) {
    if (!view) return [x, y];
    return [x * view.scaleX + view.offsetX, y * view.scaleY + view.offsetY];
  }

  function ownerToColor(ownerId) {
    if (ownerId == null) return 0x000000; // unowned nodes black
    const p = players.get(ownerId);
    if (!p || !p.color) return 0xff00ff;
    // Convert hex like #ffcc00 to 0xffcc00
    try {
      return parseInt(p.color.replace('#', '0x'));
    } catch (e) {
      return 0xff00ff;
    }
  }

  // Input: during picking, click to claim a node once; during playing, edge interactions allowed
  window.addEventListener('click', (ev) => {
    if (gameEnded) return;
    const [wx, wy] = screenToWorld(ev.clientX, ev.clientY);
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    // Determine hover eligibility by phase and ownership
    let nodeId = null;
    let edgeId = null;
    if (phase === 'picking' && !myPicked) {
      const candidateNodeId = pickNearestNode(wx, wy, 18 / baseScale);
      if (candidateNodeId != null) {
        const cn = nodes.get(candidateNodeId);
        if (cn && (cn.owner == null)) nodeId = candidateNodeId;
      }
    } else if (phase === 'playing') {
      const candidateEdgeId = pickEdgeNear(wx, wy, 14 / baseScale);
      if (candidateEdgeId != null) {
        const e = edges.get(candidateEdgeId);
        if (e) {
          const fromId = e.forward ? e.source : e.target;
          const fromNode = nodes.get(fromId);
          // Only eligible if you own the from-node
          if (fromNode && fromNode.owner === myPlayerId) edgeId = candidateEdgeId;
        }
      }
    }

    if (phase === 'picking') {
      if (!myPicked && nodeId != null && ws && ws.readyState === WebSocket.OPEN) {
        const token = localStorage.getItem('token');
        ws.send(JSON.stringify({ type: 'clickNode', nodeId: nodeId, token }));
      }
      return; // ignore edge clicks during picking
    } else {
      if (nodeId != null && ws && ws.readyState === WebSocket.OPEN) {
        const token = localStorage.getItem('token');
        ws.send(JSON.stringify({ type: 'clickNode', nodeId: nodeId, token }));
        return;
      }
      if (edgeId != null && ws && ws.readyState === WebSocket.OPEN) {
        const token = localStorage.getItem('token');
        ws.send(JSON.stringify({ type: 'clickEdge', edgeId, token }));
      }
    }
  });

  // Right-click: toggle direction on bidirectional edges
  window.addEventListener('contextmenu', (ev) => {
    if (gameEnded) return;
    if (phase !== 'playing') return; // disable during picking
    const [wx, wy] = screenToWorld(ev.clientX, ev.clientY);
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    const edgeId = pickEdgeNear(wx, wy, 14 / baseScale);
    if (edgeId != null && ws && ws.readyState === WebSocket.OPEN) {
      ev.preventDefault();
      const token = localStorage.getItem('token');
      ws.send(JSON.stringify({ type: 'toggleEdgeDirection', edgeId, token }));
    }
  });

  // Mouse move: handle hover effects
  window.addEventListener('mousemove', (ev) => {
    if (gameEnded) return;
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) return; // Don't handle hover when menu is visible
    
    const [wx, wy] = screenToWorld(ev.clientX, ev.clientY);
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    
    // Check for node hover first (nodes take priority over edges)
    const nodeId = pickNearestNode(wx, wy, 18 / baseScale);
    const edgeId = nodeId ? null : pickEdgeNear(wx, wy, 14 / baseScale);
    
    let needsRedraw = false;
    
    // Update hovered node
    if (hoveredNodeId !== nodeId) {
      hoveredNodeId = nodeId;
      needsRedraw = true;
    }
    
    // Update hovered edge
    if (hoveredEdgeId !== edgeId) {
      hoveredEdgeId = edgeId;
      needsRedraw = true;
    }
    
    if (needsRedraw) {
      redrawStatic();
    }
  });

  function screenToWorld(px, py) {
    if (!view) return [px, py];
    return [(px - view.offsetX) / view.scaleX, (py - view.offsetY) / view.scaleY];
  }

  function updateGoldBar() {
    const val = Math.max(0, Math.min(5, goldValue || 0));
    const full = Math.floor(val);
    const frac = val - full;
    for (let i = 0; i < goldSegments.length; i++) {
      const seg = goldSegments[i];
      if (!seg) continue;
      let h = 0;
      if (i < full) h = 100;
      else if (i === full) h = Math.max(0, Math.min(100, frac * 100));
      else h = 0;
      seg.fill.style.height = `${h}%`;
    }
  }

  function pickNearestNode(wx, wy, maxDist) {
    let bestId = null;
    let bestD2 = (maxDist != null ? maxDist * maxDist : 100);
    for (const [id, n] of nodes.entries()) {
      const dx = n.x - wx;
      const dy = n.y - wy;
      const d2 = dx * dx + dy * dy;
      if (d2 <= bestD2) {
        bestId = id;
        bestD2 = d2;
      }
    }
    return bestId;
  }

  function pickEdgeNear(wx, wy, maxDist) {
    const maxD2 = maxDist * maxDist;
    let bestId = null;
    let bestD2 = maxD2;
    for (const [id, e] of edges.entries()) {
      const a = nodes.get(e.source);
      const b = nodes.get(e.target);
      if (!a || !b) continue;
      const d2 = pointToSegmentDistanceSquared(wx, wy, a.x, a.y, b.x, b.y);
      if (d2 <= bestD2) {
        bestId = id;
        bestD2 = d2;
      }
    }
    return bestId;
  }

  function pointToSegmentDistanceSquared(px, py, x1, y1, x2, y2) {
    const vx = x2 - x1;
    const vy = y2 - y1;
    const wx = px - x1;
    const wy = py - y1;
    const c1 = vx * wx + vy * wy;
    if (c1 <= 0) return (px - x1) ** 2 + (py - y1) ** 2;
    const c2 = vx * vx + vy * vy;
    if (c2 <= c1) return (px - x2) ** 2 + (py - y2) ** 2;
    const b = c1 / c2;
    const bx = x1 + b * vx;
    const by = y1 + b * vy;
    return (px - bx) ** 2 + (py - by) ** 2;
  }

  function drawEdge(e, sNode, tNode, edgeId) {
    const from = e.forward ? sNode : tNode;
    const to = e.forward ? tNode : sNode;
    const isHovered = (hoveredEdgeId === edgeId);
    
    // Offset start/end by node radius so edges don't overlap nodes visually
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    const fromR = Math.max(1, (0.5 * Math.sqrt(Math.max(0, from.juice ?? from.size ?? 0.3))) * baseScale) + 3;
    const toR = Math.max(1, (0.5 * Math.sqrt(Math.max(0, to.juice ?? to.size ?? 0.3))) * baseScale) + 3;
    const [sx0, sy0] = worldToScreen(from.x, from.y);
    const [tx0, ty0] = worldToScreen(to.x, to.y);
    const dx0 = tx0 - sx0;
    const dy0 = ty0 - sy0;
    const len0 = Math.max(1, Math.hypot(dx0, dy0));
    const ux0 = dx0 / len0;
    const uy0 = dy0 / len0;
    const sx = sx0 + ux0 * fromR;
    const sy = sy0 + uy0 * fromR;
    const tx = tx0 - ux0 * toR;
    const ty = ty0 - uy0 * toR;

    const dx = tx - sx;
    const dy = ty - sy;
    const len = Math.max(1, Math.hypot(dx, dy));
    const ux = dx / len;
    const uy = dy / len;
    const angle = Math.atan2(uy, ux);

    if (!e.bidir) {
      // One-way: chain of larger triangles pointing to 'to'
      const triH = 16;
      const triW = 12;
      const spacing = triH; // packed: tip touches base of next
      const count = Math.max(1, Math.floor(len / spacing));
      const canClick = (from && from.owner === myPlayerId);
      const hoverAllowed = isHovered && canClick;
      const hoverColor = hoverAllowed ? ownerToColor(myPlayerId) : null;
      for (let i = 0; i < count; i++) {
        const cx = sx + (i + 0.5) * spacing * ux;
        const cy = sy + (i + 0.5) * spacing * uy;
        drawTriangle(cx, cy, triW, triH, angle, e, from, hoverColor, hoverAllowed);
      }
    } else {
      // Two-way: circles along path and a head triangle showing current direction
      const radius = 4; // keep current size
      const spacing = radius * 2; // packed circles
      const count = Math.max(2, Math.floor(len / spacing));
      // Use player's color for highlight when eligible, otherwise fall back to flow color
      const canClick = (from && from.owner === myPlayerId);
      const color = canClick ? ownerToColor(myPlayerId) : edgeColor(e, from);
      for (let i = 1; i < count - 1; i++) {
        const cx = sx + i * spacing * ux;
        const cy = sy + i * spacing * uy;
        if (e.flowing) {
          graphicsEdges.fillStyle(color, 1);
          graphicsEdges.fillCircle(cx, cy, radius);
        } else {
          graphicsEdges.lineStyle(2, 0x999999, 1);
          graphicsEdges.strokeCircle(cx, cy, radius);
        }
        // Add hover border using player's color only if eligible
        if (isHovered && canClick) {
          graphicsEdges.lineStyle(2, ownerToColor(myPlayerId), 1);
          graphicsEdges.strokeCircle(cx, cy, radius + 1);
        }
      }
      // Head triangle near 'to'
      const headDist = Math.min(len - 10, Math.max(10, len - 16));
      const hx = sx + headDist * ux;
      const hy = sy + headDist * uy;
      // Triangle is always green; hover only if eligible
      drawTriangle(hx, hy, 18, 14, angle, { ...e, flowing: true }, from, 0x00ff66, isHovered && canClick);
    }
  }

  function drawTriangle(cx, cy, baseW, height, angle, e, fromNode, overrideColor, isHovered) {
    const color = (overrideColor != null) ? overrideColor : edgeColor(e, fromNode);
    const halfW = baseW / 2;
    // Triangle points oriented such that tip points along +x before rotation
    const p1 = rotatePoint(cx + height / 2, cy, cx, cy, angle); // tip
    const p2 = rotatePoint(cx - height / 2, cy - halfW, cx, cy, angle); // base left
    const p3 = rotatePoint(cx - height / 2, cy + halfW, cx, cy, angle); // base right
    if (e.flowing) {
      graphicsEdges.fillStyle(color, 1);
      graphicsEdges.fillTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
    } else {
      graphicsEdges.lineStyle(2, 0x999999, 1);
      graphicsEdges.strokeTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
    }
    
    // Add hover border using the same color
    if (isHovered) {
      graphicsEdges.lineStyle(2, color, 1);
      graphicsEdges.strokeTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
    }
  }

  function edgeColor(e, fromNodeOrNull) {
    // If flowing, use from-node owner color; else grey
    if (!e.flowing) return 0x999999;
    let fromNode = fromNodeOrNull;
    if (!fromNode) {
      fromNode = e.forward ? nodes.get(e.source) : nodes.get(e.target);
    }
    return ownerToColor(fromNode ? fromNode.owner : null);
  }

  function rotatePoint(x, y, cx, cy, angle) {
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    const dx = x - cx;
    const dy = y - cy;
    return [cx + dx * cos - dy * sin, cy + dx * sin + dy * cos];
  }
})();


