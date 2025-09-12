(() => {
  // Automatically detect environment - no manual changes needed!
  const isLocalhost = window.location.hostname === 'localhost' || 
                     window.location.hostname === '127.0.0.1' || 
                     window.location.hostname === '';
  
  // Server URLs
  const LOCAL_WS_URL = 'ws://localhost:8765';
  const PRODUCTION_WS_URL = 'wss://durb-2.onrender.com';
  
  const WS_URL = isLocalhost ? LOCAL_WS_URL : PRODUCTION_WS_URL;
  
  console.log('Connecting to WebSocket:', WS_URL);

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
  let edges = new Map(); // id -> {source,target,on,flowing,flowStartTime}

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
  let goldValue = 0; // 0..10
  let nodeMaxJuice = 50;
  let hoveredNodeId = null;
  let hoveredEdgeId = null;
  
  // Abilities system
  let activeAbility = null; // null, 'bridge1way', 'reverse'
  let bridgeFirstNode = null; // first selected node for bridge building
  let mouseWorldX = 0; // current mouse position in world coordinates
  let mouseWorldY = 0;
  let capitalNodes = new Set(); // node IDs that are capitals
  let player1Capitals = 0; // capital count from backend
  let player2Capitals = 0; // capital count from backend
  
  // Peace period state
  let peacePeriodActive = false;
  let peaceTimeRemaining = 0.0;
  
  // Animation system for juice flow
  let animationTime = 0; // Global animation timer
  const JUICE_ANIMATION_SPEED = 4.0; // Speed of juice animation (higher = faster)
  const JUICE_ANIMATION_PHASES = 3; // Number of distinct color phases for juice animation

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
        console.log('poop');
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
        const goldNumber = document.getElementById('goldNumber');
        if (goldNumber) goldNumber.style.display = 'none';
        // Hide peace period UI when returning to menu
        const peaceIndicator = document.getElementById('peaceIndicator');
        const peaceTimer = document.getElementById('peaceTimer');
        if (peaceIndicator) peaceIndicator.style.display = 'none';
        if (peaceTimer) peaceTimer.style.display = 'none';
        nodes.clear();
        edges.clear();
        capitalNodes.clear(); // Clear capital nodes when returning to menu
        
        // Reset ability state when returning to menu
        activeAbility = null;
        bridgeFirstNode = null;
        
        // Reset peace period state
        peacePeriodActive = false;
        peaceTimeRemaining = 0.0;
        
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
      bottom: '20px',
      width: '48px',
      height: '600px', // Even taller gold bar with 7 sections
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
    // Create 7 segments
    goldSegments = [];
    for (let i = 0; i < 7; i++) {
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

    // Gold number display at top of gold bar
    const goldNumber = document.createElement('div');
    goldNumber.id = 'goldNumber';
    Object.assign(goldNumber.style, {
      position: 'absolute',
      right: '20px',
      bottom: '620px', // positioned above the gold bar (600px + 20px)
      fontSize: '100px',
      fontWeight: 'bold',
      color: '#ffd700',
      lineHeight: '1',
      textAlign: 'center',
      zIndex: 8,
      display: 'none', // initially hidden
    });
    goldNumber.textContent = '0';
    
    document.body.appendChild(goldNumber);

    // Peace period indicator (top right)
    const peaceIndicator = document.createElement('div');
    peaceIndicator.id = 'peaceIndicator';
    Object.assign(peaceIndicator.style, {
      position: 'absolute',
      top: '20px',
      right: '20px',
      background: 'rgba(0, 150, 0, 0.9)',
      color: '#ffffff',
      padding: '12px 20px',
      borderRadius: '8px',
      fontSize: '24px',
      fontWeight: 'bold',
      zIndex: 10,
      textAlign: 'center',
      border: '2px solid #00ff00',
      boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
      display: 'none',
      fontFamily: 'monospace'
    });
    
    // Peace timer (below peace indicator)
    const peaceTimer = document.createElement('div');
    peaceTimer.id = 'peaceTimer';
    Object.assign(peaceTimer.style, {
      position: 'absolute',
      top: '70px',
      right: '20px',
      background: 'rgba(0, 150, 0, 0.9)',
      color: '#ffffff',
      padding: '8px 16px',
      borderRadius: '6px',
      fontSize: '18px',
      fontWeight: 'bold',
      zIndex: 10,
      textAlign: 'center',
      border: '2px solid #00ff00',
      boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
      display: 'none',
      fontFamily: 'monospace'
    });
    
    document.body.appendChild(peaceIndicator);
    document.body.appendChild(peaceTimer);

    // Abilities container removed - abilities now only accessible via keyboard shortcuts and right-click

    // Capital counter display removed

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

  function update() {
    // Update animation timer for juice flow
    animationTime += 1/60; // Assuming 60 FPS, increment by frame time
    
    // Redraw if there are any flowing edges (for animation)
    let hasFlowingEdges = false;
    for (const [id, edge] of edges.entries()) {
      if (edge.flowing) {
        hasFlowingEdges = true;
        break;
      }
    }
    
    if (hasFlowingEdges) {
      redrawStatic();
    }
  }

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
      else if (msg.type === 'newEdge') handleNewEdge(msg);
      else if (msg.type === 'edgeReversed') handleEdgeReversed(msg);
      else if (msg.type === 'edgeUpdated') handleEdgeUpdated(msg);
      else if (msg.type === 'bridgeError') handleBridgeError(msg);
      else if (msg.type === 'reverseEdgeError') handleReverseEdgeError(msg);
      else if (msg.type === 'newCapital') handleNewCapital(msg);
      else if (msg.type === 'capitalError') handleCapitalError(msg);
      else if (msg.type === 'nodeDestroyed') handleNodeDestroyed(msg);
      else if (msg.type === 'destroyError') handleDestroyError(msg);
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
    capitalNodes.clear(); // Clear capital nodes from previous game
    
    // Reset ability state from previous game
    activeAbility = null;
    bridgeFirstNode = null;

    for (const arr of msg.nodes) {
      const [id, x, y, size, owner] = arr;
      nodes.set(id, { x, y, size, owner });
    }
    for (const arr of msg.edges) {
      const [id, s, t, bidir, fwd] = arr;
      edges.set(id, { source: s, target: t, on: false, flowing: false, flowStartTime: null });
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
        if (Number(pid) === myPlayerId) goldValue = Math.max(0, Math.min(7, Number(val) || 0));
      }
    }
    // Capital counts from backend
    if (Array.isArray(msg.capitals)) {
      for (const [pid, count] of msg.capitals) {
        if (Number(pid) === 1) player1Capitals = Number(count) || 0;
        if (Number(pid) === 2) player2Capitals = Number(count) || 0;
      }
    }
    // Peace period info
    if (msg.peace) {
      peacePeriodActive = msg.peace.active || false;
      peaceTimeRemaining = Number(msg.peace.timeRemaining) || 0.0;
    }
    computeTransform(game.scale.gameSize.width, game.scale.gameSize.height);
    const menu = document.getElementById('menu');
    menu && menu.classList.add('hidden');
    if (overlayMsg) overlayMsg.style.display = 'none';
    redrawStatic();
    if (statusText) {
      statusText.setText(!myPicked ? 'Choose Starting Node' : '');
      statusText.setStyle({ font: '48px monospace', color: '#ffffff' });
      statusText.setPosition(game.scale.gameSize.width / 2 - statusText.width / 2, 16);
      statusText.setDepth(10);
      statusText.setVisible(!myPicked);
    }
    updateGoldBar();
    updatePeacePeriodUI();
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
    // Reset peace period state when game ends
    peacePeriodActive = false;
    peaceTimeRemaining = 0.0;
    const buttons = document.getElementsByTagName('button');
    for (const b of buttons) {
      if (b.textContent === 'Forfeit') b.textContent = 'Quit';
    }
    // Do not clear board; leave last state visible (stale, no updates)
    redrawStatic();
    // Hide peace period UI when game ends
    const peaceIndicator = document.getElementById('peaceIndicator');
    const peaceTimer = document.getElementById('peaceTimer');
    if (peaceIndicator) peaceIndicator.style.display = 'none';
    if (peaceTimer) peaceTimer.style.display = 'none';
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
          const wasFlowing = e.flowing;
          e.on = !!on;
          e.flowing = !!flowing;
          
          // Track when flow starts for initialization animation
          if (!wasFlowing && e.flowing) {
            e.flowStartTime = animationTime;
          } else if (!e.flowing) {
            e.flowStartTime = null;
          }
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
        if (Number(pid) === myPlayerId) goldValue = Math.max(0, Math.min(7, Number(val) || 0));
      }
    }
    // Capital counts from backend
    if (Array.isArray(msg.capitals)) {
      for (const [pid, count] of msg.capitals) {
        if (Number(pid) === 1) player1Capitals = Number(count) || 0;
        if (Number(pid) === 2) player2Capitals = Number(count) || 0;
      }
    }
    // Peace period info
    if (msg.peace) {
      peacePeriodActive = msg.peace.active || false;
      peaceTimeRemaining = Number(msg.peace.timeRemaining) || 0.0;
    }
    if (statusText) {
      statusText.setText(!myPicked ? 'Choose Starting Node' : '');
      statusText.setStyle({ font: '48px monospace', color: '#ffffff' });
      statusText.setPosition(game.scale.gameSize.width / 2 - statusText.width / 2, 16);
      statusText.setDepth(10);
      statusText.setVisible(!myPicked);
    }
    updateGoldBar();
    updatePeacePeriodUI();
    redrawStatic();
  }

  function handleNewEdge(msg) {
    // Add new edge to the frontend map
    if (msg.edge) {
      const edge = msg.edge;
      edges.set(edge.id, {
        source: edge.source,
        target: edge.target,
        on: edge.on,
        flowing: edge.flowing,
        flowStartTime: edge.flowing ? animationTime : null
      });
      redrawStatic();
    }
    
    // Reset bridge building state on successful edge creation
    if (activeAbility === 'bridge1way') {
      activeAbility = null;
      bridgeFirstNode = null;
    }
  }

  function handleEdgeReversed(msg) {
    // Update existing edge with new source/target after reversal
    if (msg.edge) {
      const edge = msg.edge;
      const existingEdge = edges.get(edge.id);
      if (existingEdge) {
        // Update the source and target (they've been swapped)
        existingEdge.source = edge.source;
        existingEdge.target = edge.target;
        const wasFlowing = existingEdge.flowing;
        existingEdge.on = edge.on;
        existingEdge.flowing = edge.flowing;
        
        // Track flow start time for initialization animation
        if (!wasFlowing && existingEdge.flowing) {
          existingEdge.flowStartTime = animationTime;
        } else if (!existingEdge.flowing) {
          existingEdge.flowStartTime = null;
        }
        redrawStatic();
      }
    }
    
    // Reset reverse mode on successful edge reversal
    if (activeAbility === 'reverse') {
      activeAbility = null;
      bridgeFirstNode = null;
    }
  }

  function handleEdgeUpdated(msg) {
    // Update existing edge state (used for energy redirection)
    if (msg.edge) {
      const edge = msg.edge;
      const existingEdge = edges.get(edge.id);
      if (existingEdge) {
        const wasFlowing = existingEdge.flowing;
        existingEdge.on = edge.on;
        existingEdge.flowing = edge.flowing;
        
        // Track flow start time for initialization animation
        if (!wasFlowing && existingEdge.flowing) {
          existingEdge.flowStartTime = animationTime;
        } else if (!existingEdge.flowing) {
          existingEdge.flowStartTime = null;
        }
        redrawStatic();
      }
    }
  }

  function handleBridgeError(msg) {
    // Show error message to the player
    showErrorMessage(msg.message || "Invalid Pipe!");
  }

  function handleNewCapital(msg) {
    // Add new capital to the frontend for visual rendering
    if (msg.nodeId) {
      capitalNodes.add(msg.nodeId);
      redrawStatic();
      // Capital counter will be updated by next tick message from backend
    }
    
    // Reset capital creation state on successful capital creation
    if (activeAbility === 'capital') {
      activeAbility = null;
      bridgeFirstNode = null;
    }
  }

  function handleReverseEdgeError(msg) {
    // Show error message to the player
    showErrorMessage(msg.message || "Can't reverse this pipe!");
  }

  function handleCapitalError(msg) {
    // Show error message to the player
    showErrorMessage(msg.message || "Invalid Capital!");
  }

  function handleNodeDestroyed(msg) {
    // Remove the destroyed node from the frontend
    if (msg.nodeId) {
      nodes.delete(msg.nodeId);
      // Also remove from capital nodes if it was a capital
      capitalNodes.delete(msg.nodeId);
      redrawStatic();
    }
    
    // Reset destroy mode on successful node destruction
    if (activeAbility === 'destroy') {
      activeAbility = null;
      bridgeFirstNode = null;
    }
  }

  function handleDestroyError(msg) {
    // Show error message to the player
    showErrorMessage(msg.message || "Can't destroy this node!");
  }

  function showErrorMessage(message) {
    // Create or update error message element
    let errorMsg = document.getElementById('errorMessage');
    if (!errorMsg) {
      errorMsg = document.createElement('div');
      errorMsg.id = 'errorMessage';
      Object.assign(errorMsg.style, {
        position: 'absolute',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        background: 'rgba(255, 0, 0, 0.9)',
        color: 'white',
        padding: '12px 20px',
        borderRadius: '8px',
        fontSize: '18px',
        fontWeight: 'bold',
        zIndex: 15,
        textAlign: 'center',
        border: '2px solid #ff0000',
        boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
        pointerEvents: 'none',
        display: 'none'
      });
      document.body.appendChild(errorMsg);
    }
    
    errorMsg.textContent = message;
    errorMsg.style.display = 'block';
    
    // Auto-hide after 2 seconds
    setTimeout(() => {
      errorMsg.style.display = 'none';
    }, 2000);
  }

  function redrawStatic() {
    // Draw edges first, then nodes
    graphicsEdges.clear();
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) {
      graphicsNodes.clear();
      // Hide HUD when menu is visible (graph is not drawn)
      if (hudContainer) hudContainer.style.display = 'none';
      const goldNumber = document.getElementById('goldNumber');
      if (goldNumber) goldNumber.style.display = 'none';
      return; // Do not draw game under menu
    }
    
    // Show gold bar when graph is being drawn and we have nodes/game data
    if (hudContainer && nodes.size > 0) {
      hudContainer.style.display = 'block';
      const goldNumber = document.getElementById('goldNumber');
      if (goldNumber) goldNumber.style.display = 'block';
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
      
      // Hover effect: player's color border when eligible for starting node pick
      if (hoveredNodeId === id && !myPicked && (n.owner == null)) {
        const myColor = ownerToColor(myPlayerId);
        graphicsNodes.lineStyle(3, myColor, 1);
        graphicsNodes.strokeCircle(nx, ny, r + 3);
      }
      
      // Hover effect: show if node can be targeted for flow
      // Only show this when no ability is active to avoid conflicting with ability-specific highlights
      if (hoveredNodeId === id && myPicked && !activeAbility) {
        if (canTargetNodeForFlow(id)) {
          const myColor = ownerToColor(myPlayerId);
          graphicsNodes.lineStyle(3, myColor, 0.8);
          graphicsNodes.strokeCircle(nx, ny, r + 3);
        }
      }
      
      // Bridge building highlight: selected first node
      if (bridgeFirstNode === id && activeAbility === 'bridge1way') {
        graphicsNodes.lineStyle(4, 0xffd700, 1); // gold color
        graphicsNodes.strokeCircle(nx, ny, r + 4);
      }
      
      // Bridge building hover: show gold highlight for valid nodes
      if (hoveredNodeId === id && activeAbility === 'bridge1way') {
        if (bridgeFirstNode === null) {
          // Before selecting first node: highlight owned nodes
          if (n.owner === myPlayerId) {
            graphicsNodes.lineStyle(3, 0xffd700, 0.8); // gold highlight
            graphicsNodes.strokeCircle(nx, ny, r + 3);
          }
        } else if (bridgeFirstNode !== id) {
          // After selecting first node: highlight valid targets
          // During peace period, don't highlight opponent's nodes
          if (peacePeriodActive) {
            if (n.owner === null || n.owner === myPlayerId) {
              graphicsNodes.lineStyle(3, 0xffd700, 0.7); // semi-transparent gold
              graphicsNodes.strokeCircle(nx, ny, r + 3);
            }
          } else {
            // Normal behavior - highlight any other node as valid target
            graphicsNodes.lineStyle(3, 0xffd700, 0.7); // semi-transparent gold
            graphicsNodes.strokeCircle(nx, ny, r + 3);
          }
        }
      }
      
      // Capital creation hover: show player secondary color highlight for valid capital locations
      // (don't mislead with highlighting if it won't work)
      if (hoveredNodeId === id && activeAbility === 'capital' && canCreateCapitalAt(id)) {
        const playerSecondaryColor = ownerToSecondaryColor(myPlayerId);
        graphicsNodes.lineStyle(3, playerSecondaryColor, 0.8);
        graphicsNodes.strokeCircle(nx, ny, r + 3);
      }
      
      // Destroy mode hover: show black highlight for owned nodes
      if (hoveredNodeId === id && activeAbility === 'destroy' && n.owner === myPlayerId) {
        graphicsNodes.lineStyle(3, 0x000000, 0.8); // black highlight
        graphicsNodes.strokeCircle(nx, ny, r + 3);
      }
      
      // Draw star for capital nodes
      if (capitalNodes.has(id)) {
        const starColor = n.owner === 1 ? 0xffa500 : 0x9932cc; // orange for player 1, purple for player 2
        drawStar(nx, ny, r * 0.6, starColor);
      }
    }
    
    // Draw bridge preview using actual edge drawing logic
    if (bridgeFirstNode !== null && activeAbility === 'bridge1way') {
      const firstNode = nodes.get(bridgeFirstNode);
      if (firstNode) {
        // Create a temporary "mouse node" for the preview
        const mouseNode = {
          x: mouseWorldX,
          y: mouseWorldY,
          juice: 2.0, // small default size for consistent radius calculation
          size: 2.0,
          owner: null
        };
        
        // Create a temporary edge object for preview
        const previewEdge = {
          flowing: false, // preview shows as non-flowing (outlined)
          on: false
        };
        
        // Draw the preview edge using the same logic as real edges
        drawBridgePreview(previewEdge, firstNode, mouseNode);
      }
    }
  }

  function computeTransform(viewW, viewH) {
    if (nodes.size === 0) return;
    let minX = 0, minY = 0, maxX = 100, maxY = 100;
    // Nodes are already in 0..100 logical space; fit that rect to screen
    const padding = 60; // top/bottom padding
    const rightReservedPx = 80; // space for gold bar and margins
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

  function ownerToSecondaryColor(ownerId) {
    if (ownerId == null) return 0x000000;
    // Secondary colors: orange for player 1 (red), purple for player 2 (blue)
    if (ownerId === 1) return 0xffa500; // orange
    if (ownerId === 2) return 0x9932cc; // purple  
    return 0xff00ff; // fallback magenta
  }

  function canReverseEdge(edge) {
    if (!edge) return false;
    
    const sourceNode = nodes.get(edge.source);
    const targetNode = nodes.get(edge.target);
    if (!sourceNode || !targetNode) return false;
    
    const sourceOwner = sourceNode.owner;
    const targetOwner = targetNode.owner;
    
    // New rules: must own at least one node AND source can't be opponent's
    
    // Must own at least one node
    if (sourceOwner !== myPlayerId && targetOwner !== myPlayerId) {
      return false; // Don't own any nodes
    }
    
    // Source node cannot belong to opponent
    // Get opponent IDs (assuming 2-player game for now, but could be expanded)
    const opponentIds = [];
    for (const [playerId] of players.entries()) {
      if (playerId !== myPlayerId) {
        opponentIds.push(playerId);
      }
    }
    
    if (opponentIds.includes(sourceOwner)) {
      return false; // Source node belongs to opponent
    }
    
    return true; // Can reverse
  }

  function canTargetNodeForFlow(targetNodeId) {
    // Check if I have any edges that I own (source node owned by me) that point to this target node
    for (const [edgeId, edge] of edges.entries()) {
      if (edge.target === targetNodeId) {
        const sourceNode = nodes.get(edge.source);
        if (sourceNode && sourceNode.owner === myPlayerId) {
          // During peace period, only allow targeting if it won't attack opponent's node
          if (peacePeriodActive) {
            const targetNode = nodes.get(edge.target);
            if (targetNode && targetNode.owner !== null && targetNode.owner !== myPlayerId) {
              return false; // Would attack during peace period
            }
          }
          return true; // Found at least one edge I can flow through to this node
        }
      }
    }
    return false;
  }

  function canCreateCapitalAt(nodeId) {
    const node = nodes.get(nodeId);
    if (!node || node.owner !== myPlayerId) {
      return false; // Must own the node
    }
    
    // Check if already a capital
    if (capitalNodes.has(nodeId)) {
      return false; // Already a capital
    }
    
    // Check for adjacent capitals
    for (const [edgeId, edge] of edges.entries()) {
      let adjacentNodeId = null;
      
      // Find adjacent node (could be source or target)
      if (edge.source === nodeId) {
        adjacentNodeId = edge.target;
      } else if (edge.target === nodeId) {
        adjacentNodeId = edge.source;
      }
      
      // If adjacent node is a capital, can't create here
      if (adjacentNodeId && capitalNodes.has(adjacentNodeId)) {
        return false;
      }
    }
    
    return true; // All checks passed
  }

  // Input: during picking, click to claim a node once; during playing, edge interactions allowed
  window.addEventListener('click', (ev) => {
    if (gameEnded) return;
    const [wx, wy] = screenToWorld(ev.clientX, ev.clientY);
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    
    handleSingleClick(ev, wx, wy, baseScale);
  });


  function handleBridgeBuilding(wx, wy, baseScale, isRightClick = false) {
    if (activeAbility !== 'bridge1way') return false;
    
    const nodeId = pickNearestNode(wx, wy, 18 / baseScale);
    const edgeId = pickEdgeNear(wx, wy, 14 / baseScale);
    
    if (nodeId != null) {
      const node = nodes.get(nodeId);
      if (node) {
        if (bridgeFirstNode === null) {
          // Start bridge building - first node must be owned by player
          if (node.owner === myPlayerId) {
            if (goldValue >= 3) {
              bridgeFirstNode = nodeId;
              return true; // Handled
            } else {
              // Not enough gold for bridge building
              showErrorMessage("Not enough gold! Need 3 gold for new pipe.");
              return true; // Handled
            }
          }
        } else if (bridgeFirstNode !== nodeId) {
          // Complete bridge building - second node can be any node
          if (goldValue >= 3 && ws && ws.readyState === WebSocket.OPEN) {
            // During peace period, check if this would attack
            if (peacePeriodActive) {
              const targetNode = nodes.get(nodeId);
              if (targetNode && targetNode.owner !== null && targetNode.owner !== myPlayerId) {
                showErrorMessage("Cannot build bridge to attack during peace period!");
                return true; // Handled
              }
            }
            
            const token = localStorage.getItem('token');
            ws.send(JSON.stringify({
              type: 'buildBridge',
              fromNodeId: bridgeFirstNode,
              toNodeId: nodeId,
              cost: 3,
              token: token
            }));
            // Don't reset bridge building state here - wait for server response
            return true; // Handled
          } else if (goldValue < 3) {
            // Not enough gold to complete bridge building
            showErrorMessage("Not enough gold! Need 3 gold for new pipe.");
            return true; // Handled
          }
        } else {
          // Clicked same node, cancel selection
          bridgeFirstNode = null;
          return true; // Handled
        }
      }
    }
    
    // If we get here, it means we clicked on empty space, an edge, or an invalid node
    // Cancel bridge building
    activeAbility = null;
    bridgeFirstNode = null;
    return true; // Handled
  }

  function handleSingleClick(ev, wx, wy, baseScale) {
    // Handle bridge building mode
    if (handleBridgeBuilding(wx, wy, baseScale, false)) {
      return; // Bridge building was handled
    }
    
    // Handle reverse edge mode
    if (activeAbility === 'reverse') {
      const candidateEdgeId = pickEdgeNear(wx, wy, 14 / baseScale);
      if (candidateEdgeId != null) {
        const edge = edges.get(candidateEdgeId);
        if (edge) {
          // Reverse the edge
          const ability = { cost: 1 };
          if (goldValue >= ability.cost && ws && ws.readyState === WebSocket.OPEN) {
            const token = localStorage.getItem('token');
            ws.send(JSON.stringify({
              type: 'reverseEdge',
              edgeId: candidateEdgeId,
              cost: ability.cost,
              token: token
            }));
          }
          
          // Don't reset reverse mode here - wait for server response
          // Reset will happen in handleEdgeReversed() on success or stay active on error
        }
      }
      return; // Don't handle normal clicks in reverse mode
    }
    
    // Handle capital creation mode
    if (activeAbility === 'capital') {
      const candidateNodeId = pickNearestNode(wx, wy, 18 / baseScale);
      if (candidateNodeId != null) {
        const node = nodes.get(candidateNodeId);
        if (node) {
          // Attempt to create capital (backend will validate ownership and adjacency)
          const ability = { cost: 4 };
          if (goldValue >= ability.cost && ws && ws.readyState === WebSocket.OPEN) {
            const token = localStorage.getItem('token');
            ws.send(JSON.stringify({
              type: 'createCapital',
              nodeId: candidateNodeId,
              cost: ability.cost,
              token: token
            }));
          }
          
          // Don't reset capital creation state here - wait for server response
          // Reset will happen in handleNewCapital() on success or stay active on error
        }
      }
      return; // Don't handle normal clicks in capital mode
    }
    
    // Handle destroy mode
    if (activeAbility === 'destroy') {
      const candidateNodeId = pickNearestNode(wx, wy, 18 / baseScale);
      if (candidateNodeId != null) {
        const node = nodes.get(candidateNodeId);
        if (node && node.owner === myPlayerId) {
          // Attempt to destroy node (backend will validate ownership)
          const ability = { cost: 2 };
          if (goldValue >= ability.cost && ws && ws.readyState === WebSocket.OPEN) {
            const token = localStorage.getItem('token');
            ws.send(JSON.stringify({
              type: 'destroyNode',
              nodeId: candidateNodeId,
              cost: ability.cost,
              token: token
            }));
          }
          
          // Don't reset destroy state here - wait for server response
          // Reset will happen in handleNodeDestroyed() on success or stay active on error
        }
      }
      return; // Don't handle normal clicks in destroy mode
    }
    
    // Normal click handling
    let nodeId = null;
    let edgeId = null;
    
    const candidateNodeId = pickNearestNode(wx, wy, 18 / baseScale);
    if (candidateNodeId != null) {
      nodeId = candidateNodeId;
    } else {
      const candidateEdgeId = pickEdgeNear(wx, wy, 14 / baseScale);
      if (candidateEdgeId != null) {
        const e = edges.get(candidateEdgeId);
        if (e) {
          const sourceNode = nodes.get(e.source);
          // Only eligible if you own the source node
          if (sourceNode && sourceNode.owner === myPlayerId) edgeId = candidateEdgeId;
        }
      }
    }

    // Handle clicks - check for starting node pick first
    if (!myPicked && nodeId != null && ws && ws.readyState === WebSocket.OPEN) {
      const token = localStorage.getItem('token');
      ws.send(JSON.stringify({ type: 'clickNode', nodeId: nodeId, token }));
      return; // Return after handling starting node pick
    }
    
    // Handle all other clicks (node flow targeting, edge clicks, etc.)
    if (ws && ws.readyState === WebSocket.OPEN) {
      const token = localStorage.getItem('token');
      
      if (nodeId != null) {
        // Check if this is a node we can target for flow
        if (canTargetNodeForFlow(nodeId)) {
          // Redirect energy towards this node
          ws.send(JSON.stringify({
            type: 'redirectEnergy',
            targetNodeId: nodeId,
            token: token
          }));
          return;
        } else {
          // Regular node click (for other purposes like building capitals, etc.)
          ws.send(JSON.stringify({ type: 'clickNode', nodeId: nodeId, token }));
          return;
        }
      }
      
      if (edgeId != null) {
        ws.send(JSON.stringify({ type: 'clickEdge', edgeId, token }));
      }
    }
  }

  // Right-click: activate new pipe ability on node, complete bridge building, or reverse edge direction
  window.addEventListener('contextmenu', (ev) => {
    if (gameEnded) return;
    const [wx, wy] = screenToWorld(ev.clientX, ev.clientY);
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    
    // Handle bridge building mode first
    if (handleBridgeBuilding(wx, wy, baseScale, true)) {
      ev.preventDefault();
      return; // Bridge building was handled
    }
    
    // Check if we're right-clicking on a node to start bridge building
    const nodeId = pickNearestNode(wx, wy, 18 / baseScale);
    if (nodeId != null) {
      const node = nodes.get(nodeId);
      if (node && node.owner === myPlayerId) {
        ev.preventDefault(); // Always prevent default when clicking on owned node
        if (goldValue >= 3) {
          activeAbility = 'bridge1way';
          bridgeFirstNode = nodeId;
        } else {
          // Not enough gold for bridge building
          showErrorMessage("Not enough gold! Need 3 gold for new pipe.");
        }
        return;
      }
    }
    
    // Fall back to edge reversal if not on a valid node or not in bridge building mode
    const edgeId = pickEdgeNear(wx, wy, 14 / baseScale);
    if (edgeId != null && ws && ws.readyState === WebSocket.OPEN) {
      ev.preventDefault();
      const token = localStorage.getItem('token');
      ws.send(JSON.stringify({ type: 'reverseEdge', edgeId, cost: 1, token }));
    }
  });

  // Keyboard shortcuts for abilities
  window.addEventListener('keydown', (ev) => {
    if (gameEnded) return;
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) return;
    
    switch (ev.key.toLowerCase()) {
      case 'a':
        ev.preventDefault();
        handleAbilityClick('bridge1way');
        break;
      case 'd':
        ev.preventDefault();
        handleAbilityClick('destroy');
        break;
      case 'escape':
        // Cancel active ability
        if (activeAbility) {
          activeAbility = null;
          bridgeFirstNode = null;
        }
        break;
    }
  });

  // Mouse move: handle hover effects
  window.addEventListener('mousemove', (ev) => {
    if (gameEnded) return;
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) return; // Don't handle hover when menu is visible
    
    const [wx, wy] = screenToWorld(ev.clientX, ev.clientY);
    mouseWorldX = wx;
    mouseWorldY = wy;
    
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    
    let needsRedraw = false;
    
    // In bridge building, capital, or destroy mode, only check for node hover, not edge hover
    if (activeAbility === 'bridge1way' || activeAbility === 'capital' || activeAbility === 'destroy') {
      const nodeId = pickNearestNode(wx, wy, 18 / baseScale);
      
      // Update hovered node
      if (hoveredNodeId !== nodeId) {
        hoveredNodeId = nodeId;
        needsRedraw = true;
      }
      
      // Clear edge hover during bridge mode
      if (hoveredEdgeId !== null) {
        hoveredEdgeId = null;
        needsRedraw = true;
      }
      
      // Always redraw during bridge mode to update the preview line (but not for capital or destroy mode)
      if (activeAbility === 'bridge1way') {
        needsRedraw = true;
      }
    } else {
      // Normal hover behavior
      const nodeId = pickNearestNode(wx, wy, 18 / baseScale);
      const edgeId = nodeId ? null : pickEdgeNear(wx, wy, 14 / baseScale);
      
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
    }
    
    if (needsRedraw) {
      redrawStatic();
    }
  });

  function screenToWorld(px, py) {
    if (!view) return [px, py];
    return [(px - view.offsetX) / view.scaleX, (py - view.offsetY) / view.scaleY];
  }

  function handleAbilityClick(abilityName) {
    // Allow abilities during peace and playing phases
    
    const abilities = {
      'bridge1way': { cost: 3 },
      'reverse': { cost: 1 },
      'destroy': { cost: 2 }
    };
    
    const ability = abilities[abilityName];
    if (!ability) return;
    
    // Check if player has enough gold
    if (goldValue < ability.cost) {
      return; // Not enough gold, do nothing (could add visual feedback later)
    }
    
    // Toggle ability activation
    if (activeAbility === abilityName) {
      // Deactivate
      activeAbility = null;
      bridgeFirstNode = null;
    } else if (abilityName === 'bridge1way') {
      // Activate bridge building
      activeAbility = abilityName;
      bridgeFirstNode = null;
    } else if (abilityName === 'reverse') {
      // Activate reverse mode (similar to capital - click an edge to reverse)
      activeAbility = abilityName;
      bridgeFirstNode = null;
    } else if (abilityName === 'destroy') {
      // Activate destroy mode
      activeAbility = abilityName;
      bridgeFirstNode = null; // reuse for destroy node selection
    }
    // Placeholder abilities do nothing for now
  }



  function updateGoldBar() {
    const val = Math.max(0, Math.min(7, goldValue || 0));
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
    
    // Update gold number display
    const goldNumber = document.getElementById('goldNumber');
    if (goldNumber) {
      goldNumber.textContent = Math.floor(val).toString();
    }
  }

  function updatePeacePeriodUI() {
    const peaceIndicator = document.getElementById('peaceIndicator');
    const peaceTimer = document.getElementById('peaceTimer');
    
    if (peaceIndicator && peaceTimer) {
      if (peacePeriodActive) {
        peaceIndicator.style.display = 'block';
        peaceIndicator.textContent = 'PEACE';
        
        peaceTimer.style.display = 'block';
        peaceTimer.textContent = `${Math.ceil(peaceTimeRemaining)}s`;
      } else {
        peaceIndicator.style.display = 'none';
        peaceTimer.style.display = 'none';
      }
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

  function drawBridgePreview(e, sNode, tNode) {
    // Similar to drawEdge but draws in gold for preview
    // Bridge preview always goes from sNode (first selected) to tNode (mouse position)
    const from = sNode;
    const to = tNode;
    
    // Offset start/end by node radius so edges don't overlap nodes visually
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    const fromR = Math.max(1, (0.5 * Math.sqrt(Math.max(0, from.juice ?? from.size ?? 0.3))) * baseScale) + 1;
    const toR = Math.max(1, (0.5 * Math.sqrt(Math.max(0, to.juice ?? to.size ?? 0.3))) * baseScale) + 1;
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

    const previewColor = 0xffd700; // gold color for preview

    // All edges are now one-way: chain of triangles pointing to target
    const triH = 16;
    const triW = 12;
    const packedSpacing = triH; // packed: tip touches base of next
    const packedCount = Math.max(1, Math.floor(len / packedSpacing));
    
    // Calculate actual spacing to ensure last triangle tip touches node edge
    const actualSpacing = len / packedCount;
    
    for (let i = 0; i < packedCount; i++) {
      const cx = sx + (i + 0.5) * actualSpacing * ux;
      const cy = sy + (i + 0.5) * actualSpacing * uy;
      drawPreviewTriangle(cx, cy, triW, triH, angle, previewColor);
    }
  }

  function drawStar(cx, cy, radius, color) {
    // Draw a 5-pointed star
    const spikes = 5;
    const outerRadius = radius;
    const innerRadius = radius * 0.4;
    
    graphicsNodes.fillStyle(color, 1);
    graphicsNodes.beginPath();
    
    for (let i = 0; i < spikes * 2; i++) {
      const angle = (i * Math.PI) / spikes;
      const r = i % 2 === 0 ? outerRadius : innerRadius;
      const x = cx + Math.cos(angle - Math.PI / 2) * r;
      const y = cy + Math.sin(angle - Math.PI / 2) * r;
      
      if (i === 0) {
        graphicsNodes.moveTo(x, y);
      } else {
        graphicsNodes.lineTo(x, y);
      }
    }
    
    graphicsNodes.closePath();
    graphicsNodes.fillPath();
  }

  function drawPreviewTriangle(cx, cy, baseW, height, angle, color) {
    const halfW = baseW / 2;
    // Triangle points oriented such that tip points along +x before rotation
    const p1 = rotatePoint(cx + height / 2, cy, cx, cy, angle); // tip
    const p2 = rotatePoint(cx - height / 2, cy - halfW, cx, cy, angle); // base left
    const p3 = rotatePoint(cx - height / 2, cy + halfW, cx, cy, angle); // base right
    
    // Draw outlined triangle for preview
    graphicsEdges.lineStyle(2, color, 0.7);
    graphicsEdges.strokeTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
  }

  function drawEdge(e, sNode, tNode, edgeId) {
    const from = sNode;  // All edges go from source to target
    const to = tNode;
    const isHovered = (hoveredEdgeId === edgeId);
    
    // Offset start/end by node radius so edges don't overlap nodes visually
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    const fromR = Math.max(1, (0.5 * Math.sqrt(Math.max(0, from.juice ?? from.size ?? 0.3))) * baseScale) + 1;
    const toR = Math.max(1, (0.5 * Math.sqrt(Math.max(0, to.juice ?? to.size ?? 0.3))) * baseScale) + 1;
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

    // All edges are now one-way: chain of triangles pointing from source to target
    const triH = 16;
    const triW = 12;
    const packedSpacing = triH; // packed: tip touches base of next
    const packedCount = Math.max(1, Math.floor(len / packedSpacing));
    
    // Calculate actual spacing to ensure last triangle tip touches node edge
    const actualSpacing = len / packedCount;
    
    const canLeftClick = (from && from.owner === myPlayerId);
    // Only highlight edges that can actually be reversed (use the validation function)
    const canRightClick = isHovered && canReverseEdge(e);
    const leftClickHover = isHovered && canLeftClick;
    const rightClickHover = isHovered && canRightClick && !canLeftClick;
    
    let hoverColor = null;
    if (leftClickHover) {
      hoverColor = ownerToColor(myPlayerId); // Primary color for left-clickable
    } else if (rightClickHover) {
      hoverColor = ownerToSecondaryColor(myPlayerId); // Secondary color for right-clickable only
    }
    
    const hoverAllowed = leftClickHover || rightClickHover;
    for (let i = 0; i < packedCount; i++) {
      const cx = sx + (i + 0.5) * actualSpacing * ux;
      const cy = sy + (i + 0.5) * actualSpacing * uy;
      drawTriangle(cx, cy, triW, triH, angle, e, from, hoverColor, hoverAllowed, i, packedCount);
    }
  }

  function drawTriangle(cx, cy, baseW, height, angle, e, fromNode, overrideColor, isHovered, triangleIndex, totalTriangles) {
    const color = (overrideColor != null) ? overrideColor : edgeColor(e, fromNode);
    const halfW = baseW / 2;
    // Triangle points oriented such that tip points along +x before rotation
    const p1 = rotatePoint(cx + height / 2, cy, cx, cy, angle); // tip
    const p2 = rotatePoint(cx - height / 2, cy - halfW, cx, cy, angle); // base left
    const p3 = rotatePoint(cx - height / 2, cy + halfW, cx, cy, angle); // base right
    
    if (e.flowing) {
      // Animated juice flow effect
      const animatedColor = getAnimatedJuiceColor(color, triangleIndex || 0, totalTriangles || 1, e.flowStartTime);
      
      if (animatedColor === null) {
        // Triangle not yet filled - show grey outline (same as non-flowing)
        graphicsEdges.lineStyle(2, 0x999999, 1);
        graphicsEdges.strokeTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
      } else {
        // Triangle is filled - show animated color
        graphicsEdges.fillStyle(animatedColor.color, animatedColor.alpha);
        graphicsEdges.fillTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
      }
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
    // If flowing, use source node owner color; else grey
    if (!e.flowing) return 0x999999;
    let fromNode = fromNodeOrNull;
    if (!fromNode) {
      fromNode = nodes.get(e.source);  // Always use source node
    }
    return ownerToColor(fromNode ? fromNode.owner : null);
  }

  function getAnimatedJuiceColor(baseColor, triangleIndex, totalTriangles, flowStartTime) {
    // Calculate the normal animation colors first (used in both phases)
    const animationCycle = (animationTime * JUICE_ANIMATION_SPEED) % totalTriangles;
    const leadTriangle = Math.floor(animationCycle);
    
    // Calculate distance from this triangle to the lead triangle (wrapping around)
    let distanceFromLead;
    if (triangleIndex >= leadTriangle) {
      distanceFromLead = triangleIndex - leadTriangle;
    } else {
      distanceFromLead = (totalTriangles - leadTriangle) + triangleIndex;
    }
    
    // Always cycle through exactly 4 brightness levels, regardless of edge length
    const brightnessLevels = 4;
    const level = distanceFromLead % brightnessLevels; // Cycle through 0, 1, 2, 3
    const brightness = 1.0 - (level * 0.2); // 1.0, 0.8, 0.6, 0.4
    
    // Extract RGB components from hex color
    const r = (baseColor >> 16) & 0xFF;
    const g = (baseColor >> 8) & 0xFF;
    const b = baseColor & 0xFF;
    
    // Apply brightness multiplier and make colors lighter overall
    const lightnessFactor = 1.4; // Make colors 40% lighter
    const newR = Math.min(255, Math.floor(r * brightness * lightnessFactor));
    const newG = Math.min(255, Math.floor(g * brightness * lightnessFactor));
    const newB = Math.min(255, Math.floor(b * brightness * lightnessFactor));
    
    // Recombine into hex color
    const animatedColor = (newR << 16) | (newG << 8) | newB;
    
    // Check if we're in initialization phase
    if (flowStartTime !== null) {
      const timeSinceStart = animationTime - flowStartTime;
      const initializationDuration = totalTriangles / JUICE_ANIMATION_SPEED; // Time to fill all triangles
      
      if (timeSinceStart < initializationDuration) {
        // Initialization phase: fill triangles progressively from source to target
        const fillProgress = (timeSinceStart * JUICE_ANIMATION_SPEED);
        
        if (triangleIndex > fillProgress) {
          // This triangle hasn't been reached yet - return null to show grey outline
          return null;
        } else {
          // This triangle has been reached - show animated color
          return {
            color: animatedColor,
            alpha: 0.9
          };
        }
      }
    }
    
    // Normal phase (after initialization or if no initialization)
    return {
      color: animatedColor,
      alpha: 0.9
    };
  }

  function rotatePoint(x, y, cx, cy, angle) {
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    const dx = x - cx;
    const dy = y - cy;
    return [cx + dx * cos - dy * sin, cy + dx * sin + dy * cos];
  }
})();


