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
  let players = new Map(); // id -> {color, secondaryColors}
  let playerOrder = [];
  let playerStats = new Map(); // id -> {nodes:0, gold:0}
  let eliminatedPlayers = new Set();
  let myEliminated = false;
  let gameEnded = false;
  let overlayMsg = null;
  let goldDisplay = null; // gold number display in bottom right
  let myPlayerId = null;
  let phase = 'picking';
  let myPicked = false;
  let goldValue = 0; // no limit
  let nodeMaxJuice = 50;
  let hoveredNodeId = null;
  let hoveredEdgeId = null;
  
  // Abilities system
  let activeAbility = null; // null, 'bridge1way', 'reverse'
  let bridgeFirstNode = null; // first selected node for bridge building
  let mouseWorldX = 0; // current mouse position in world coordinates
  let mouseWorldY = 0;
  let bridgeCostDisplay = null; // current bridge cost display text object

  // Progress bar for node count victory
  let progressBar = null;
  let progressBarInner = null;
  let progressMarkerLeft = null;
  let progressMarkerRight = null;
  let progressSegments = new Map();
  let winThreshold = 40; // default, will be updated from backend
  let totalNodes = 60; // default, will be updated from backend

  // Timer system
  let timerDisplay = null;
  let gameStartTime = null;
  let gameDuration = 5 * 60; // 5 minutes in seconds
  
  // Auto-expand system
  let autoExpandToggle = null;
  let homeAutoExpandToggle = null;
  let myAutoExpand = false; // my player's auto-expand setting
  let persistentAutoExpand = false; // persistent setting stored in localStorage
  
  // Speed system
  let speedSlider = null;
  let speedRange = null;
  let speedValue = null;
  let homeSpeedSlider = null;
  let homeSpeedRange = null;
  let homeSpeedValue = null;
  let mySpeedLevel = 6; // my player's speed level setting
  let persistentSpeedLevel = 6; // persistent setting stored in localStorage
  let selectedPlayerCount = 2;

  // Money transparency system
  let moneyIndicators = []; // Array of {x, y, text, color, startTime, duration}

  let sceneRef = null;
  let quitButton = null;
  
  
  // Animation system for juice flow
  let animationTime = 0; // Global animation timer
  const JUICE_ANIMATION_SPEED = 4.0; // Speed of juice animation (higher = faster)
  const JUICE_ANIMATION_PHASES = 3; // Number of distinct color phases for juice animation
  
  function calculateNodeRadius(node, baseScale) {
    if (node.owner === null) {
      // Unowned nodes: use original logic but with better shrinking and double the radius
      const juiceVal = (node.juice != null ? node.juice : (node.size || 0));
      
      // Use the original square root logic but with a much smaller minimum
      // This allows nodes to shrink to almost nothing before dying
      // Double the radius to make unowned nodes twice as big
      const radius = Math.max(0.2, 1.0 * Math.sqrt(Math.max(0, juiceVal))) * baseScale;
      
      return radius;
    } else {
      // Owned nodes: use original logic
      return Math.max(0.3, 0.5 * Math.sqrt(Math.max(0, node.size ?? node.juice ?? 0.3))) * baseScale;
    }
  }

  function preload() {}

  // Auto-expand persistence functions
  function loadPersistentAutoExpand() {
    const saved = localStorage.getItem('autoExpand');
    persistentAutoExpand = saved === 'true';
    return persistentAutoExpand;
  }

  function savePersistentAutoExpand(value) {
    persistentAutoExpand = value;
    localStorage.setItem('autoExpand', value.toString());
  }

  // Speed persistence functions
  function loadPersistentSpeedLevel() {
    const saved = localStorage.getItem('speedLevel');
    persistentSpeedLevel = saved ? parseInt(saved) : 6;
    return persistentSpeedLevel;
  }

  function savePersistentSpeedLevel(value) {
    persistentSpeedLevel = value;
    localStorage.setItem('speedLevel', value.toString());
  }

  function getSpeedMultiplier(level) {
    const speedMultipliers = [0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.25, 1.5, 1.75, 2.0];
    const levelIndex = Math.max(0, Math.min(9, level - 1));
    return speedMultipliers[levelIndex];
  }

  function formatSpeedDisplay(level) {
    const multiplier = getSpeedMultiplier(level);
    return multiplier === 1.0 ? '1x' : `${multiplier}x`;
  }

  // Helper: convert 0xRRGGBB -> "#rrggbb"
  function toCssColor(hex) {
    if (typeof hex === 'string') return hex;
    return '#' + (hex >>> 0).toString(16).padStart(6, '0');
  }

  // Bridge cost calculation
  function calculateBridgeCost(fromNode, toNode) {
    if (!fromNode || !toNode) return 0;

    const baseWidth = screen && Number.isFinite(screen.width) ? screen.width : 100;
    const baseHeight = screen && Number.isFinite(screen.height) ? screen.height : 100;
    const normX = 100 / Math.max(1, baseWidth);
    const normY = 100 / Math.max(1, baseHeight);

    // Normalize distance so it matches backend math regardless of viewport stretch
    const dx = (toNode.x - fromNode.x) * normX;
    const dy = (toNode.y - fromNode.y) * normY;
    const distance = Math.sqrt(dx * dx + dy * dy);

    if (distance === 0) return 0;

    const BASE_COST = 1;
    const COST_PER_UNIT = 0.25; // align with backend scaling (slightly steeper)

    const cost = BASE_COST + distance * COST_PER_UNIT;
    return Math.round(cost);
  }

// Replace the whole function with this Phaser version:
function updateBridgeCostDisplay(fromNode, toNode) {
  if (!sceneRef || !fromNode || !toNode) return;

  // midpoint between nodes (reads nicely, like your reversal capture $ signs)
  const midX = (fromNode.x + toNode.x) / 2;
  const midY = (fromNode.y + toNode.y) / 2;
  const [sx, sy] = worldToScreen(midX, midY);

  const cost = calculateBridgeCost(fromNode, toNode);
  const canAfford = goldValue >= cost;
  const text = `$${cost}`;

  if (!bridgeCostDisplay) {
    bridgeCostDisplay = sceneRef.add.text(sx, sy - 20, text, {
      fontFamily: 'monospace',
      fontSize: '20px',
      fontStyle: 'bold',
      color: canAfford ? '#cd853f' : '#000000', // Use browny gold color when affordable, black when not
      stroke: canAfford ? '#000000' : '#ffffff', // Black outline when affordable, white outline when unaffordable
      strokeThickness: 3,
    })
    .setOrigin(0.5, 0.5)
    .setDepth(1000);
  } else {
    bridgeCostDisplay.setText(text);
    bridgeCostDisplay.setPosition(sx, sy - 20);
    bridgeCostDisplay.setColor(canAfford ? '#cd853f' : '#000000'); // Use browny gold color when affordable, black when not
    bridgeCostDisplay.setStroke(canAfford ? '#000000' : '#ffffff', 3); // Black outline when affordable, white outline when unaffordable
    bridgeCostDisplay.setVisible(true);
  }
}

function hideBridgeCostDisplay() {
  if (bridgeCostDisplay) {
    bridgeCostDisplay.destroy();
    bridgeCostDisplay = null;
  }
}


  // Money indicator functions
  // Create an animated text indicator that rises & fades out
  function createMoneyIndicator(x, y, text, color, duration = 2000) {
    const indicator = {
      x,
      y,
      text,
      color,
      startTime: Date.now(),
      duration,
      textObj: null
    };

    const [sx, sy] = worldToScreen(x, y);
    if (sceneRef) {
      indicator.textObj = sceneRef.add.text(sx, sy, text, {
        fontFamily: 'monospace',
        fontSize: '20px',
        fontStyle: 'bold',
        color: toCssColor(color),
        stroke: '#000000',
        strokeThickness: 3
      })
      .setOrigin(0.5, 0.5)
      .setDepth(1000)
      .setAlpha(1);

      indicator.textObj.setShadow(2, 2, '#000000', 4, false, true);
    }

    moneyIndicators.push(indicator);
  }

  // Update positions/opacity each frame, and clean up expired indicators
  function updateMoneyIndicators() {
    const now = Date.now();
    moneyIndicators = moneyIndicators.filter(indicator => {
      const elapsed = now - indicator.startTime;
      const progress = elapsed / indicator.duration;

      if (progress >= 1) {
        if (indicator.textObj) indicator.textObj.destroy();
        return false;
      }

      // Rise & fade
      const alpha = 1 - progress;
      const offsetY = progress * 30;
      const [sx, sy] = worldToScreen(indicator.x, indicator.y - offsetY);

      if (indicator.textObj) {
        indicator.textObj.setPosition(sx, sy);
        indicator.textObj.setAlpha(alpha);
        indicator.textObj.setVisible(true);
      }
      return true;
    });
  }

  // Called from redrawStatic(); now just toggles visibility if menu is open
  function drawMoneyIndicators() {
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    moneyIndicators.forEach(ind => {
      if (ind.textObj) ind.textObj.setVisible(!menuVisible);
    });
  }

  function clearMoneyIndicators() {
    moneyIndicators.forEach(ind => ind.textObj && ind.textObj.destroy());
    moneyIndicators = [];
  }

  function updateHomeAutoExpandToggle() {
    if (!homeAutoExpandToggle) return;
    
    const toggleSwitch = homeAutoExpandToggle.querySelector('.toggle-switch');
    if (toggleSwitch) {
      if (persistentAutoExpand) {
        toggleSwitch.classList.add('enabled');
      } else {
        toggleSwitch.classList.remove('enabled');
      }
    }
  }

  function updateHomeSpeedSlider() {
    if (!homeSpeedRange || !homeSpeedValue) return;
    
    homeSpeedRange.value = persistentSpeedLevel;
    homeSpeedValue.textContent = persistentSpeedLevel;
    mySpeedLevel = persistentSpeedLevel;
  }

  function create() {
    sceneRef = this;
    graphicsEdges = this.add.graphics();
    graphicsNodes = this.add.graphics();
    statusText = this.add.text(10, 10, 'Connect to start a game', { font: '16px monospace', color: '#cccccc' });

    // Load persistent settings
    loadPersistentAutoExpand();
    loadPersistentSpeedLevel();

    tryConnectWS();
    const menu = document.getElementById('menu');
    const playBtn = document.getElementById('playBtn');
    const playBotBtn = document.getElementById('playBotBtn');
    const buttonContainer = document.querySelector('.button-container');
    const difficultyDropdown = document.getElementById('difficultyDropdown');
    const playerCountButtons = document.querySelectorAll('.player-count-option');

    if (playerCountButtons && playerCountButtons.length) {
      playerCountButtons.forEach((btn) => {
        btn.addEventListener('click', () => {
          playerCountButtons.forEach((b) => b.classList.remove('active'));
          btn.classList.add('active');
          const desired = parseInt(btn.getAttribute('data-count'), 10);
          selectedPlayerCount = Number.isFinite(desired) ? desired : 2;
        });
      });
    }
    
    if (playBtn) {
      playBtn.addEventListener('click', () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          document.getElementById('lobby').style.display = 'block';
          // Hide both buttons when entering lobby
          if (buttonContainer) {
            buttonContainer.style.display = 'none';
          }
          ws.send(JSON.stringify({ 
            type: 'joinLobby',
            token: localStorage.getItem('token') || null,
            autoExpand: persistentAutoExpand,
            speedLevel: persistentSpeedLevel,
            playerCount: selectedPlayerCount
          }));
        }
      });
    }
    
    if (playBotBtn) {
      playBotBtn.addEventListener('click', () => {
        // Toggle difficulty dropdown
        if (difficultyDropdown) {
          const isVisible = difficultyDropdown.style.display === 'block';
          difficultyDropdown.style.display = isVisible ? 'none' : 'block';
        }
      });
    }
    
    // Handle dropdown option clicks
    if (difficultyDropdown) {
      const options = difficultyDropdown.querySelectorAll('.dropdown-option');
      options.forEach(option => {
        option.addEventListener('click', () => {
          const selectedDifficulty = option.getAttribute('data-difficulty');
          if (selectedDifficulty && ws && ws.readyState === WebSocket.OPEN) {
            console.log('Starting bot game with difficulty:', selectedDifficulty);
            document.getElementById('lobby').style.display = 'block';
            document.getElementById('lobby').textContent = `Starting ${selectedDifficulty} bot game...`;
            // Hide buttons and dropdown when starting bot game
            if (buttonContainer) {
              buttonContainer.style.display = 'none';
            }
            difficultyDropdown.style.display = 'none'; // Hide dropdown
            ws.send(JSON.stringify({ 
              type: 'startBotGame',
              difficulty: selectedDifficulty,
              autoExpand: persistentAutoExpand,
              speedLevel: persistentSpeedLevel
            }));
          }
        });
      });
    }
    
    // Close dropdown when clicking outside
    document.addEventListener('click', (event) => {
      const dropdownContainer = document.querySelector('.dropdown-container');
      if (dropdownContainer && !dropdownContainer.contains(event.target)) {
        if (difficultyDropdown) {
          difficultyDropdown.style.display = 'none';
        }
      }
    });
    // Quit overlay button
    const quitBtn = document.createElement('button');
    quitBtn.textContent = 'Forfeit';
    Object.assign(quitBtn.style, { position: 'absolute', left: '10px', top: '10px', zIndex: 10, padding: '8px 14px', borderRadius: '8px', border: 'none', background: '#ff5555', color: '#111', cursor: 'pointer', display: 'none' });
    document.body.appendChild(quitBtn);
    quitBtn.addEventListener('click', () => {
      const token = localStorage.getItem('token');
      if (!gameEnded && !myEliminated && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'quitGame', token }));
        myEliminated = true;
        updateQuitButtonLabel();
        if (overlayMsg) {
          overlayMsg.textContent = 'Eliminated';
          overlayMsg.style.display = 'block';
        }
        return;
      }

      if (gameEnded || myEliminated) {
        returnToMenu();
      }
    });
    quitButton = quitBtn;
    // Toggle quit button visibility based on menu
    const observer = new MutationObserver(() => {
      const menuVisible = !menu.classList.contains('hidden');
      quitBtn.style.display = menuVisible ? 'none' : 'block';
    });
    observer.observe(menu, { attributes: true, attributeFilter: ['class'] });
    // Also hide win/lose overlay and auto-expand toggle when menu is visible
    const menuObserver = new MutationObserver(() => {
      const menuVisible = !menu.classList.contains('hidden');
      if (menuVisible && overlayMsg) overlayMsg.style.display = 'none';
      if (menuVisible && autoExpandToggle) autoExpandToggle.style.display = 'none';
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

    // Gold number display in bottom right
    const goldNumber = document.createElement('div');
    goldNumber.id = 'goldNumber';
    Object.assign(goldNumber.style, {
      position: 'absolute',
      right: '20px',
      bottom: '20px',
      fontSize: '120px',
      fontWeight: 'bold',
      color: '#ffd700',
      lineHeight: '1',
      textAlign: 'center',
      textShadow: '3px 3px 6px rgba(0,0,0,0.8)',
      zIndex: 8,
      display: 'none', // initially hidden
    });
    goldNumber.textContent = '$0';
    
    document.body.appendChild(goldNumber);
    goldDisplay = goldNumber;

    // Initialize progress UI
    progressBar = document.getElementById('progressBar');
    progressBarInner = document.getElementById('progressBarInner');
    progressMarkerLeft = document.getElementById('progressMarkerLeft');
    progressMarkerRight = document.getElementById('progressMarkerRight');
    if (progressMarkerLeft) progressMarkerLeft.style.display = 'none';
    if (progressMarkerRight) progressMarkerRight.style.display = 'none';
    
    // Initialize timer display
    timerDisplay = document.getElementById('timerDisplay');
    

  // Initialize auto-expand toggle
  autoExpandToggle = document.getElementById('autoExpandToggle');
  
  // Initialize speed display
  speedDisplay = document.getElementById('speedDisplay');
    if (autoExpandToggle) {
      const toggleSwitch = autoExpandToggle.querySelector('.toggle-switch');
      if (toggleSwitch) {
        toggleSwitch.addEventListener('click', () => {
          if (ws && ws.readyState === WebSocket.OPEN && !gameEnded) {
            const token = localStorage.getItem('token');
            ws.send(JSON.stringify({ type: 'toggleAutoExpand', token }));
          }
        });
      }
    }

    // Initialize home screen speed slider
    homeSpeedSlider = document.getElementById('homeSpeedSlider');
    homeSpeedRange = document.getElementById('homeSpeedRange');
    homeSpeedValue = document.getElementById('homeSpeedValue');
    if (homeSpeedRange && homeSpeedValue) {
      // Initialize with persistent value
      homeSpeedRange.value = persistentSpeedLevel;
      homeSpeedValue.textContent = formatSpeedDisplay(persistentSpeedLevel);
      mySpeedLevel = persistentSpeedLevel;
      
      homeSpeedRange.addEventListener('input', () => {
        const newSpeed = parseInt(homeSpeedRange.value);
        homeSpeedValue.textContent = formatSpeedDisplay(newSpeed);
        mySpeedLevel = newSpeed;
        savePersistentSpeedLevel(newSpeed);
      });
    }

    // Initialize home screen auto-expand toggle
    homeAutoExpandToggle = document.getElementById('homeAutoExpandToggle');
    if (homeAutoExpandToggle) {
      const toggleSwitch = homeAutoExpandToggle.querySelector('.toggle-switch');
      if (toggleSwitch) {
        toggleSwitch.addEventListener('click', () => {
          const newValue = !persistentAutoExpand;
          savePersistentAutoExpand(newValue);
          updateHomeAutoExpandToggle();
        });
      }
      // Initialize the toggle state
      updateHomeAutoExpandToggle();
    }


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
    
    // Update money indicators
    updateMoneyIndicators();
    
    // Update game timer
    const remainingTime = updateTimer();
    
    // Check if timer has expired
    if (remainingTime <= 0 && gameStartTime && !gameEnded) {
      // Timer expired - game should end
      // The backend will handle the actual game end logic
    }
    
    // Redraw if there are any flowing edges (for animation) or money indicators
    let hasFlowingEdges = false;
    for (const [id, edge] of edges.entries()) {
      if (edge.flowing) {
        hasFlowingEdges = true;
        break;
      }
    }
    
    if (hasFlowingEdges || moneyIndicators.length > 0) {
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
      else if (msg.type === 'nodeDestroyed') handleNodeDestroyed(msg);
      else if (msg.type === 'destroyError') handleDestroyError(msg);
      else if (msg.type === 'nodeCaptured') handleNodeCaptured(msg);
      else if (msg.type === 'lobbyTimeout') handleLobbyTimeout();
    };
  }

  function handleInit(msg) {
    gameEnded = false;
    myEliminated = false;
    updateQuitButtonLabel();

    screen = msg.screen || null;
    nodes.clear();
    edges.clear();
    players.clear();
    playerStats.clear();
    eliminatedPlayers.clear();
    playerOrder = [];

    activeAbility = null;
    bridgeFirstNode = null;
    hideBridgeCostDisplay();
    if (bridgeCostDisplay && app && app.stage) {
      app.stage.removeChild(bridgeCostDisplay);
      bridgeCostDisplay = null;
    }

    if (Array.isArray(msg.nodes)) {
      for (const arr of msg.nodes) {
        const [id, x, y, size, owner] = arr;
        nodes.set(id, { x, y, size, owner });
      }
    }

    if (Array.isArray(msg.edges)) {
      for (const arr of msg.edges) {
        const [id, s, t] = arr;
        edges.set(id, { source: s, target: t, on: false, flowing: false, flowStartTime: null });
      }
    }

    if (Array.isArray(msg.players)) {
      msg.players.forEach((info, index) => {
        let id;
        let color;
        let secondaryColors = [];

        if (Array.isArray(info)) {
          const [pid, col] = info;
          id = Number(pid);
          color = col;
        } else if (info && typeof info === 'object') {
          id = Number(info.id);
          color = info.color;
          if (Array.isArray(info.secondaryColors)) secondaryColors = info.secondaryColors;
        }

        if (!Number.isFinite(id)) return;
        players.set(id, {
          color: color || '#ffffff',
          secondaryColors,
        });
        playerStats.set(id, { nodes: 0, gold: 0 });
        playerOrder.push(id);
      });
    }

    if (Array.isArray(msg.eliminatedPlayers)) {
      msg.eliminatedPlayers.forEach((pid) => {
        const id = Number(pid);
        if (Number.isFinite(id)) eliminatedPlayers.add(id);
      });
    }

    if (msg.token) localStorage.setItem('token', msg.token);
    if (msg.myPlayerId != null) localStorage.setItem('myPlayerId', String(msg.myPlayerId));
    myPlayerId = (msg.myPlayerId != null)
      ? Number(msg.myPlayerId)
      : Number(localStorage.getItem('myPlayerId') || '0');

    if (msg.settings && typeof msg.settings.nodeMaxJuice === 'number') {
      nodeMaxJuice = msg.settings.nodeMaxJuice;
    }

    phase = typeof msg.phase === 'string' ? msg.phase : 'picking';
    myPicked = false;
    if (Array.isArray(msg.picked)) {
      msg.picked.forEach(([pid, picked]) => {
        if (Number(pid) === myPlayerId) myPicked = !!picked;
      });
    }

    if (Array.isArray(msg.gold)) {
      msg.gold.forEach(([pid, value]) => {
        const id = Number(pid);
        if (!Number.isFinite(id)) return;
        const stats = playerStats.get(id) || { nodes: 0, gold: 0 };
        stats.gold = Math.max(0, Number(value) || 0);
        playerStats.set(id, stats);
        if (id === myPlayerId) {
          goldValue = stats.gold;
        }
      });
    } else {
      goldValue = 0;
    }

    if (typeof msg.winThreshold === 'number') winThreshold = msg.winThreshold;
    if (typeof msg.totalNodes === 'number') totalNodes = msg.totalNodes;

    if (msg.counts) {
      Object.entries(msg.counts).forEach(([pid, count]) => {
        const id = Number(pid);
        if (!Number.isFinite(id)) return;
        const stats = playerStats.get(id) || { nodes: 0, gold: 0 };
        stats.nodes = Math.max(0, Number(count) || 0);
        playerStats.set(id, stats);
      });
    }

    myAutoExpand = persistentAutoExpand;
    if (Array.isArray(msg.autoExpand)) {
      msg.autoExpand.forEach(([pid, enabled]) => {
        if (Number(pid) === myPlayerId) {
          myAutoExpand = !!enabled;
        }
      });
    }
    updateAutoExpandToggle();

    myEliminated = eliminatedPlayers.has(myPlayerId);
    updateQuitButtonLabel();

    computeTransform(game.scale.gameSize.width, game.scale.gameSize.height);
    const menu = document.getElementById('menu');
    if (menu) menu.classList.add('hidden');
    if (overlayMsg) overlayMsg.style.display = 'none';

    redrawStatic();
    if (statusText) {
      statusText.setText('');
      statusText.setVisible(false);
    }

    updateGoldBar();
    updateProgressBar();
    updateSpeedDisplay();

    if (progressBar) {
      progressBar.style.display = players.size > 0 ? 'block' : 'none';
    }

    startGameTimer();
  }

  function handleLobby(msg) {
    const lobby = document.getElementById('lobby');
    if (lobby) {
      lobby.textContent = msg.status === 'waiting' ? 'Waiting for players to join...' : 'Starting...';
    }
    if (msg.token) localStorage.setItem('token', msg.token);
    // Hide the PLAY button while waiting
    const playBtn = document.getElementById('playBtn');
    if (playBtn) playBtn.style.display = 'none';
  }

  function handleLobbyTimeout() {
    returnToMenu();
    const lobby = document.getElementById('lobby');
    if (lobby) {
      lobby.textContent = 'Lobby timed out. Try again.';
      lobby.style.display = 'block';
      setTimeout(() => {
        lobby.textContent = '';
        lobby.style.display = 'none';
      }, 3000);
    }
  }

  function handleGameOver(msg) {
    const myId = Number(localStorage.getItem('myPlayerId') || '0');
    const text = (msg.winnerId === myId) ? 'You win' : 'You lose';
    if (overlayMsg) {
      overlayMsg.textContent = text;
      overlayMsg.style.display = 'block';
    }
    gameEnded = true;
    myEliminated = msg.winnerId !== myId;
    updateQuitButtonLabel();
    // Clean up money indicators when game ends
    clearMoneyIndicators();
    // Do not clear board; leave last state visible (stale, no updates)
    redrawStatic();
    // Ensure menu elements are ready for return
    const menu = document.getElementById('menu');
    const lobby = document.getElementById('lobby');
    const buttonContainer = document.querySelector('.button-container');
    const difficultyDropdown = document.getElementById('difficultyDropdown');
    
    if (lobby) {
      lobby.textContent = '';
      lobby.style.display = 'none';
    }
    if (buttonContainer) {
      buttonContainer.style.display = 'flex';
    }
    if (difficultyDropdown) {
      difficultyDropdown.style.display = 'none'; // Hide dropdown
    }
    // Don't show menu immediately - wait for user to click Quit button
  }

  function handleTick(msg) {
    if (Array.isArray(msg.nodes)) {
      msg.nodes.forEach(([id, size, owner]) => {
        const node = nodes.get(id);
        if (node) {
          node.size = size;
          node.owner = owner;
        }
      });
    }

    if (Array.isArray(msg.edges)) {
      msg.edges.forEach(([id, on, flowing]) => {
        const edge = edges.get(id);
        if (!edge) return;
        const wasFlowing = edge.flowing;
        edge.on = !!on;
        edge.flowing = !!flowing;
        if (!wasFlowing && edge.flowing) {
          edge.flowStartTime = animationTime;
        } else if (!edge.flowing) {
          edge.flowStartTime = null;
        }
      });
    }

    if (typeof msg.phase === 'string') phase = msg.phase;
    if (Array.isArray(msg.picked)) {
      msg.picked.forEach(([pid, picked]) => {
        if (Number(pid) === myPlayerId) myPicked = !!picked;
      });
    }

    if (Array.isArray(msg.gold)) {
      msg.gold.forEach(([pid, value]) => {
        const id = Number(pid);
        if (!Number.isFinite(id)) return;
        const stats = ensurePlayerStats(id);
        stats.gold = Math.max(0, Number(value) || 0);
        if (id === myPlayerId) {
          goldValue = stats.gold;
        }
      });
    }

    if (msg.counts) {
      Object.entries(msg.counts).forEach(([pid, count]) => {
        const id = Number(pid);
        if (!Number.isFinite(id)) return;
        const stats = ensurePlayerStats(id);
        stats.nodes = Math.max(0, Number(count) || 0);
      });
    }

    if (typeof msg.winThreshold === 'number') winThreshold = msg.winThreshold;
    if (typeof msg.totalNodes === 'number') totalNodes = msg.totalNodes;

    if (Array.isArray(msg.autoExpand)) {
      msg.autoExpand.forEach(([pid, enabled]) => {
        if (Number(pid) === myPlayerId) {
          myAutoExpand = !!enabled;
          savePersistentAutoExpand(myAutoExpand);
          updateHomeAutoExpandToggle();
        }
      });
    }
    updateAutoExpandToggle();

    if (Array.isArray(msg.eliminatedPlayers)) {
      eliminatedPlayers.clear();
      msg.eliminatedPlayers.forEach((pid) => {
        const id = Number(pid);
        if (Number.isFinite(id)) eliminatedPlayers.add(id);
      });
    }

    if (Array.isArray(msg.recentEliminations) && msg.recentEliminations.length > 0) {
      if (!gameEnded && overlayMsg && msg.recentEliminations.some((pid) => Number(pid) === myPlayerId)) {
        overlayMsg.textContent = 'Eliminated';
        overlayMsg.style.display = 'block';
      }
    }

    myEliminated = eliminatedPlayers.has(myPlayerId);
    updateQuitButtonLabel();
    if (!myEliminated && overlayMsg && overlayMsg.textContent === 'Eliminated') {
      overlayMsg.style.display = 'none';
    }
    if (myEliminated && activeAbility) {
      activeAbility = null;
      bridgeFirstNode = null;
      hideBridgeCostDisplay();
    }

    if (statusText) {
      statusText.setText('');
      statusText.setVisible(false);
    }

    updateGoldBar();
    updateProgressBar();
    updateSpeedDisplay();
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
      
      // Show cost indicator for bridge building
      if (activeAbility === 'bridge1way' && msg.cost) {
        // Position the indicator at the midpoint of the new bridge
        const sourceNode = nodes.get(edge.source);
        const targetNode = nodes.get(edge.target);
        if (sourceNode && targetNode) {
          const midX = (sourceNode.x + targetNode.x) / 2;
          const midY = (sourceNode.y + targetNode.y) / 2;
          const [screenX, screenY] = worldToScreen(midX, midY);
          
          createMoneyIndicator(
            midX, 
            midY, 
            `-$${msg.cost}`, 
            0xcd853f, // browner gold color (peru)
            2000 // 2 seconds
          );
        }
      }
      
      redrawStatic();
    }
    
    // Reset bridge building state on successful edge creation
    if (activeAbility === 'bridge1way') {
      activeAbility = null;
      bridgeFirstNode = null;
      // Remove cost display
      if (bridgeCostDisplay && app && app.stage) {
        app.stage.removeChild(bridgeCostDisplay);
        bridgeCostDisplay = null;
      }
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
        
        // Show cost indicator near the mouse position (but offset to the side)
        if (msg.cost) {
          // Position the indicator much closer to the mouse
          const offsetX = 5; // much smaller offset to the right of mouse
          const offsetY = -5; // much smaller offset upward from mouse
          createMoneyIndicator(
            mouseWorldX + offsetX, 
            mouseWorldY + offsetY, 
            `-$${msg.cost}`, 
            0xcd853f, // browner gold color (peru)
            2000 // 2 seconds
          );
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

  function handleReverseEdgeError(msg) {
    // Show error message to the player
    showErrorMessage(msg.message || "Can't reverse this pipe!");
  }

  function handleNodeDestroyed(msg) {
    // Remove the destroyed node from the frontend
    if (msg.nodeId) {
      nodes.delete(msg.nodeId);
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

  function handleNodeCaptured(msg) {
    // Show reward indicator when a node is captured
    if (msg.nodeId && msg.reward) {
      const node = nodes.get(msg.nodeId);
      if (node) {
        // Position the indicator much closer to the node - just slightly above and to the right
        const offsetX = 2; // much smaller offset to the right
        const offsetY = -2; // much smaller offset upward
        createMoneyIndicator(
          node.x + offsetX, 
          node.y + offsetY, 
          `+$${msg.reward}`, 
          0xffd700, // golden color
          2000 // 2 seconds
        );
      }
    }
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
      // Hide gold display when menu is visible (graph is not drawn)
      if (goldDisplay) goldDisplay.style.display = 'none';
      // Hide progress bar when menu is visible
      if (progressBar) progressBar.style.display = 'none';
      // Hide timer when menu is visible
      if (timerDisplay) timerDisplay.style.display = 'none';
      // Hide auto-expand toggle when menu is visible
      if (autoExpandToggle) autoExpandToggle.style.display = 'none';
      // Hide speed display when menu is visible
      if (speedDisplay) speedDisplay.style.display = 'none';
      return; // Do not draw game under menu
    }
    
    // Show gold display when graph is being drawn and we have nodes/game data
    if (goldDisplay && nodes.size > 0) {
      goldDisplay.style.display = 'block';
    }
    // Show progress bar when graph is being drawn and we have nodes/game data
    if (progressBar && nodes.size > 0) {
      progressBar.style.display = 'block';
    }
    // Show timer when graph is being drawn and we have nodes/game data
    if (timerDisplay && nodes.size > 0 && gameStartTime) {
      timerDisplay.style.display = 'block';
    }
    // Show auto-expand toggle when graph is being drawn and we have nodes/game data
    if (autoExpandToggle && nodes.size > 0) {
      autoExpandToggle.style.display = 'block';
    }
    // Show speed display when graph is being drawn and we have nodes/game data
    if (speedDisplay && nodes.size > 0) {
      speedDisplay.style.display = 'block';
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
      
      const radius = calculateNodeRadius(n, baseScale);
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
        const playerSecondaryColor = ownerToSecondaryColor(myPlayerId);
        graphicsNodes.lineStyle(4, playerSecondaryColor, 1); // secondary color
        graphicsNodes.strokeCircle(nx, ny, r + 4);
      }
      
      // Bridge building hover: show secondary color highlight for valid nodes
      if (hoveredNodeId === id && activeAbility === 'bridge1way') {
        if (bridgeFirstNode === null) {
          // Before selecting first node: highlight owned nodes
          if (n.owner === myPlayerId) {
            const playerSecondaryColor = ownerToSecondaryColor(myPlayerId);
            graphicsNodes.lineStyle(3, playerSecondaryColor, 0.8); // secondary color highlight
            graphicsNodes.strokeCircle(nx, ny, r + 3);
          }
          // AFTER
          } else if (bridgeFirstNode !== id) {
            const firstNode = nodes.get(bridgeFirstNode);

            // secondary color highlight on the target
            const playerSecondaryColor = ownerToSecondaryColor(myPlayerId);
            graphicsNodes.lineStyle(3, playerSecondaryColor, 0.8);
            graphicsNodes.strokeCircle(nx, ny, r + 3);

            // show static, live-updating midpoint label
            updateBridgeCostDisplay(firstNode, n);
          }

      }
      
      // Destroy mode hover: show black highlight for owned nodes
      if (hoveredNodeId === id && activeAbility === 'destroy' && n.owner === myPlayerId) {
        graphicsNodes.lineStyle(3, 0x000000, 0.8); // black highlight
        graphicsNodes.strokeCircle(nx, ny, r + 3);
      }
    }

    // After drawing nodes / previews:
    if (!(activeAbility === 'bridge1way' && bridgeFirstNode !== null && hoveredNodeId !== null)) {
      hideBridgeCostDisplay();
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
        // Use node center if hovering over a node, otherwise use mouse position
        let previewTargetNode = mouseNode;
        if (hoveredNodeId !== null && hoveredNodeId !== bridgeFirstNode) {
          const hoveredNode = nodes.get(hoveredNodeId);
          if (hoveredNode) {
            previewTargetNode = hoveredNode;
          }
        }
        drawBridgePreview(previewEdge, firstNode, previewTargetNode);
        
        // Update cost display - use node center if hovering over a node, otherwise use mouse position
        let targetNode = mouseNode;
        if (hoveredNodeId !== null && hoveredNodeId !== bridgeFirstNode) {
          const hoveredNode = nodes.get(hoveredNodeId);
          if (hoveredNode) {
            targetNode = hoveredNode;
          }
        }
        updateBridgeCostDisplay(firstNode, targetNode);
      }
    }
    
    // Draw money indicators
    drawMoneyIndicators();
  }

  function computeTransform(viewW, viewH) {
    if (nodes.size === 0) return;
    let minX = 0, minY = 0, maxX = 100, maxY = 100;
    // Nodes are already in 0..100 logical space; fit that rect to screen
    const topPadding = 100; // increased top padding to avoid progress bar
    const bottomPadding = 60; // bottom padding
    const rightReservedPx = 40; // space for gold number and margins
    const width = Math.max(1, maxX - minX);
    const height = Math.max(1, maxY - minY);
    const scaleX = (viewW - 60 * 2 - rightReservedPx) / width; // keep side padding same
    const scaleY = (viewH - topPadding - bottomPadding) / height;
    // Use independent scaling for x and y to fully utilize window aspect
    // Left edge can go to the edge; anchor left and center vertically with top offset
    const offsetX = 60 - scaleX * minX; // keep side padding same
    const offsetY = topPadding + (viewH - topPadding - bottomPadding - scaleY * height) / 2 - scaleY * minY;
    view = { minX, minY, maxX, maxY, scaleX, scaleY, offsetX, offsetY };
  }

  function worldToScreen(x, y) {
    if (!view) return [x, y];
    return [x * view.scaleX + view.offsetX, y * view.scaleY + view.offsetY];
  }

  function hexToInt(color) {
    if (!color) return 0xff00ff;
    const hex = color.startsWith('#') ? color.slice(1) : color;
    return parseInt(`0x${hex}`, 16);
  }

  function ownerToColor(ownerId) {
    if (ownerId == null) return 0x000000; // unowned nodes black
    const entry = players.get(ownerId);
    return hexToInt(entry?.color);
  }

  function ownerToSecondaryColor(ownerId) {
    if (ownerId == null) return 0x000000;
    const entry = players.get(ownerId);
    if (!entry) return 0xff00ff;
    const fallback = entry.color || '#ff00ff';
    const secondary = Array.isArray(entry.secondaryColors) && entry.secondaryColors.length
      ? entry.secondaryColors[0]
      : lightenColor(fallback, 0.3);
    return hexToInt(secondary);
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
          return true; // Found at least one edge I can flow through to this node
        }
      }
    }
    return false;
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
            bridgeFirstNode = nodeId;
            return true; // Handled
          }
        } else if (bridgeFirstNode !== nodeId) {
          // Complete bridge building - second node can be any node
          const firstNode = nodes.get(bridgeFirstNode);
          const cost = calculateBridgeCost(firstNode, node);
          
          if (goldValue >= cost && ws && ws.readyState === WebSocket.OPEN) {
            
            const token = localStorage.getItem('token');
            ws.send(JSON.stringify({
              type: 'buildBridge',
              fromNodeId: bridgeFirstNode,
              toNodeId: nodeId,
              cost: cost,
              token: token
            }));
            // Don't reset bridge building state here - wait for server response
            return true; // Handled
          } else if (goldValue < cost) {
            // Not enough gold to complete bridge building - don't show error, just ignore click
            // The visual feedback (red highlight) already shows this
            return true; // Handled
          }
        } else {
          // Clicked same node, cancel selection
          bridgeFirstNode = null;
          // Remove cost display
          if (bridgeCostDisplay) {
            app.stage.removeChild(bridgeCostDisplay);
            bridgeCostDisplay = null;
          }
          return true; // Handled
        }
      }
    }
    
    // If we get here, it means we clicked on empty space, an edge, or an invalid node
    // Cancel bridge building
    activeAbility = null;
    bridgeFirstNode = null;
    // Remove cost display
    if (bridgeCostDisplay && app && app.stage) {
      app.stage.removeChild(bridgeCostDisplay);
      bridgeCostDisplay = null;
    }
    return true; // Handled
  }

  function handleSingleClick(ev, wx, wy, baseScale) {
    if (myEliminated || gameEnded) return;
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
          const sourceNode = nodes.get(edge.source);
          const targetNode = nodes.get(edge.target);
          if (sourceNode && targetNode) {
            const cost = calculateBridgeCost(sourceNode, targetNode);
            if (goldValue >= cost && ws && ws.readyState === WebSocket.OPEN) {
              const token = localStorage.getItem('token');
              ws.send(JSON.stringify({
                type: 'reverseEdge',
                edgeId: candidateEdgeId,
                cost: cost,
                token: token
              }));
            }
          }
          
          // Don't reset reverse mode here - wait for server response
          // Reset will happen in handleEdgeReversed() on success or stay active on error
        }
      }
      return; // Don't handle normal clicks in reverse mode
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
          // Regular node click
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
        activeAbility = 'bridge1way';
        bridgeFirstNode = nodeId;
        return;
      }
    }
    
    // Fall back to edge reversal if not on a valid node or not in bridge building mode
    const edgeId = pickEdgeNear(wx, wy, 14 / baseScale);
    if (edgeId != null && ws && ws.readyState === WebSocket.OPEN) {
      ev.preventDefault();
      const edge = edges.get(edgeId);
      if (edge) {
        const sourceNode = nodes.get(edge.source);
        const targetNode = nodes.get(edge.target);
        if (sourceNode && targetNode) {
          const cost = calculateBridgeCost(sourceNode, targetNode);
          if (goldValue >= cost) {
            const token = localStorage.getItem('token');
            ws.send(JSON.stringify({ type: 'reverseEdge', edgeId, cost, token }));
          }
        }
      }
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
      // Remove cost display
      if (bridgeCostDisplay && app && app.stage) {
        app.stage.removeChild(bridgeCostDisplay);
        bridgeCostDisplay = null;
      }
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
    
    // In bridge building or destroy mode, only check for node hover, not edge hover
    if (activeAbility === 'bridge1way' || activeAbility === 'destroy') {
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
      
      // Always redraw during bridge mode to update the preview line (but not for destroy mode)
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
    if (myEliminated || gameEnded) return;
    // Allow abilities during playing phase
    
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
      // Remove cost display
      if (bridgeCostDisplay && app && app.stage) {
        app.stage.removeChild(bridgeCostDisplay);
        bridgeCostDisplay = null;
      }
    } else if (abilityName === 'bridge1way') {
      // Activate bridge building
      activeAbility = abilityName;
      bridgeFirstNode = null;
    } else if (abilityName === 'reverse') {
      // Activate reverse mode (click an edge to reverse)
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
    const val = Math.max(0, goldValue || 0);
    if (goldDisplay) {
      goldDisplay.textContent = `$${Math.floor(val)}`;
    }
  }

  function updateQuitButtonLabel() {
    if (!quitButton) return;
    quitButton.textContent = (gameEnded || myEliminated) ? 'Quit' : 'Forfeit';
  }

  function ensurePlayerStats(id) {
    if (!playerStats.has(id)) {
      playerStats.set(id, { nodes: 0, gold: 0 });
    }
    return playerStats.get(id);
  }

  function updateProgressBar() {
    if (!progressBarInner) return;

    const orderedIds = playerOrder.length ? playerOrder : Array.from(players.keys()).sort((a, b) => a - b);
    const activeIds = orderedIds.filter((id) => players.has(id) && !eliminatedPlayers.has(id));

    if (!activeIds.length || totalNodes <= 0) {
      progressBarInner.innerHTML = '';
      progressSegments.clear();
      if (progressBar) progressBar.style.display = 'none';
      if (progressMarkerLeft) progressMarkerLeft.style.display = 'none';
      if (progressMarkerRight) progressMarkerRight.style.display = 'none';
      return;
    }

    if (progressBar) progressBar.style.display = 'block';

    const denominator = Math.max(totalNodes, 1);
    const seen = new Set();

    progressBarInner.style.justifyContent = activeIds.length === 2 ? 'space-between' : 'flex-start';

    activeIds.forEach((id, index) => {
      const info = players.get(id);
      if (!info) return;
      seen.add(id);
      let segment = progressSegments.get(id);
      if (!segment) {
        segment = document.createElement('div');
        segment.className = 'progressSegment';
        const labelEl = document.createElement('span');
        labelEl.className = 'segmentLabel';
        segment.appendChild(labelEl);
        progressSegments.set(id, segment);
        progressBarInner.appendChild(segment);
      }

      const stats = ensurePlayerStats(id);
      const percent = Math.max(0, Math.min(100, (stats.nodes / denominator) * 100));
      const primary = info.color || '#ffffff';
      const secondary = (info.secondaryColors && info.secondaryColors[0]) || lightenColor(primary, 0.35);

      segment.style.order = index;
      segment.style.flex = `0 0 ${percent}%`;
      segment.style.background = `linear-gradient(to right, ${primary}, ${secondary})`;
      segment.style.opacity = '0.92';
      segment.dataset.playerId = String(id);

      const labelEl = segment.querySelector('.segmentLabel');
      if (labelEl) {
        labelEl.textContent = `$${Math.floor(stats.gold || 0)}`;
      }
    });

    progressSegments.forEach((segment, id) => {
      if (!seen.has(id)) {
        if (segment.parentElement === progressBarInner) {
          progressBarInner.removeChild(segment);
        }
        progressSegments.delete(id);
      }
    });

    const activeCount = activeIds.length;
    if (activeCount === 2 && progressMarkerLeft && progressMarkerRight) {
      const thresholdPct = Math.max(0, Math.min(100, (winThreshold / Math.max(totalNodes, 1)) * 100));
      progressMarkerLeft.style.display = 'block';
      progressMarkerLeft.style.left = `calc(${thresholdPct}% - 2px)`;
      progressMarkerRight.style.display = 'block';
      progressMarkerRight.style.left = `calc(${100 - thresholdPct}% - 2px)`;
    } else {
      if (progressMarkerLeft) progressMarkerLeft.style.display = 'none';
      if (progressMarkerRight) progressMarkerRight.style.display = 'none';
    }

  }

  function returnToMenu() {
    const menu = document.getElementById('menu');
    const lobby = document.getElementById('lobby');
    const difficultyDropdown = document.getElementById('difficultyDropdown');
    const homeButtons = document.querySelector('.button-container');
    const playBtnEl = document.getElementById('playBtn');

    if (lobby) {
      lobby.textContent = '';
      lobby.style.display = 'none';
    }
    if (menu) menu.classList.remove('hidden');
    if (difficultyDropdown) difficultyDropdown.style.display = 'none';
    if (homeButtons) homeButtons.style.display = 'flex';
    if (playBtnEl) playBtnEl.style.display = 'block';

    if (quitButton) quitButton.style.display = 'none';
    if (overlayMsg) overlayMsg.style.display = 'none';
    if (goldDisplay) goldDisplay.style.display = 'none';
    if (progressBar) progressBar.style.display = 'none';
    if (progressMarkerLeft) progressMarkerLeft.style.display = 'none';
    if (progressMarkerRight) progressMarkerRight.style.display = 'none';
    if (timerDisplay) timerDisplay.style.display = 'none';
    if (autoExpandToggle) autoExpandToggle.style.display = 'none';
    if (speedDisplay) speedDisplay.style.display = 'none';

    updateHomeAutoExpandToggle();

    nodes.clear();
    edges.clear();
    players.clear();
    playerOrder = [];
    playerStats.clear();
    eliminatedPlayers.clear();
    progressSegments.clear();
    if (progressBarInner) progressBarInner.innerHTML = '';
    if (progressBarInner) progressBarInner.style.justifyContent = 'flex-start';

    activeAbility = null;
    bridgeFirstNode = null;
    hideBridgeCostDisplay();
    clearMoneyIndicators();

    gameEnded = false;
    myEliminated = false;
    gameStartTime = null;
    updateQuitButtonLabel();
    redrawStatic();
  }

  function lightenColor(color, factor) {
    // Convert hex color to RGB, lighten it, and convert back to hex
    const hex = color.replace('#', '');
    const r = parseInt(hex.substr(0, 2), 16);
    const g = parseInt(hex.substr(2, 2), 16);
    const b = parseInt(hex.substr(4, 2), 16);
    
    const newR = Math.min(255, Math.floor(r + (255 - r) * factor));
    const newG = Math.min(255, Math.floor(g + (255 - g) * factor));
    const newB = Math.min(255, Math.floor(b + (255 - b) * factor));
    
    return `#${newR.toString(16).padStart(2, '0')}${newG.toString(16).padStart(2, '0')}${newB.toString(16).padStart(2, '0')}`;
  }

  function startGameTimer() {
    gameStartTime = Date.now();
    if (timerDisplay) {
      timerDisplay.style.display = 'block';
    }
  }

  function updateTimer() {
    if (!timerDisplay || !gameStartTime) return;
    
    const elapsed = (Date.now() - gameStartTime) / 1000; // elapsed time in seconds
    const remaining = Math.max(0, gameDuration - elapsed);
    
    const minutes = Math.floor(remaining / 60);
    const seconds = Math.floor(remaining % 60);
    
    const timeString = `${minutes}:${seconds.toString().padStart(2, '0')}`;
    timerDisplay.textContent = timeString;
    
    // Change color when time is running low
    if (remaining <= 30) {
      timerDisplay.style.color = '#ff6666'; // red when under 30 seconds
    } else if (remaining <= 60) {
      timerDisplay.style.color = '#ffaa66'; // orange when under 1 minute
    } else {
      timerDisplay.style.color = '#ffffff'; // white normally
    }
    
    return remaining;
  }

  function updateAutoExpandToggle() {
    if (!autoExpandToggle) return;
    
    const toggleSwitch = autoExpandToggle.querySelector('.toggle-switch');
    if (toggleSwitch) {
      if (myAutoExpand) {
        toggleSwitch.classList.add('enabled');
      } else {
        toggleSwitch.classList.remove('enabled');
      }
    }
  }

  function updateSpeedDisplay() {
    if (!speedDisplay) return;
    
    const speedValueElement = speedDisplay.querySelector('#speedValue');
    if (speedValueElement) {
      const speedText = formatSpeedDisplay(mySpeedLevel);
      speedValueElement.textContent = speedText;
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
    const fromR = Math.max(1, calculateNodeRadius(from, baseScale)) + 1;
    const toR = Math.max(1, calculateNodeRadius(to, baseScale)) + 1;
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

    // Check if the player can afford this bridge
    const cost = calculateBridgeCost(from, to);
    const canAfford = goldValue >= cost;
    
    // Use black when unaffordable, secondary color when affordable
    const previewColor = canAfford ? ownerToSecondaryColor(from.owner) : 0x000000;

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
    const fromR = Math.max(1, calculateNodeRadius(from, baseScale)) + 1;
    const toR = Math.max(1, calculateNodeRadius(to, baseScale)) + 1;
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
      // Animated juice flow effect - filled triangles
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
    } else if (e.on) {
      // Edge is on but not flowing - show hollow triangles with same animation pattern
      const animatedColor = getAnimatedJuiceColor(color, triangleIndex || 0, totalTriangles || 1, e.flowStartTime);
      
      if (animatedColor === null) {
        // Triangle not yet reached in animation - show grey outline
        graphicsEdges.lineStyle(2, 0x999999, 1);
        graphicsEdges.strokeTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
      } else {
        // Triangle is in animation cycle - show hollow triangle with animated color outline
        graphicsEdges.lineStyle(3, animatedColor.color, animatedColor.alpha);
        graphicsEdges.strokeTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
      }
    } else {
      // Edge is not on - show grey outline
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
    // If edge is on (flowing or not), use source node owner color; else grey
    if (!e.on) return 0x999999;
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
