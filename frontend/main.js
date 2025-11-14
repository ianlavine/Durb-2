(() => {
  const LEGACY_CONFIG = window.__DURB_LEGACY_CONFIG || {};
  const IS_LEGACY_CLIENT = Boolean(LEGACY_CONFIG.isLegacy);
  const IS_SANDBOX_CLIENT = Boolean(LEGACY_CONFIG.isSandbox);
  const LEGACY_DEFAULT_MODE = (typeof LEGACY_CONFIG.defaultMode === 'string' && LEGACY_CONFIG.defaultMode.trim())
    ? LEGACY_CONFIG.defaultMode.trim().toLowerCase()
    : (IS_LEGACY_CLIENT ? 'basic' : null);
  const MODE_QUEUE_KEY = (typeof LEGACY_CONFIG.queueKey === 'string' && LEGACY_CONFIG.queueKey.trim())
    ? LEGACY_CONFIG.queueKey.trim().toLowerCase()
    : (IS_LEGACY_CLIENT ? (LEGACY_DEFAULT_MODE || 'basic') : 'brass');

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

  const SHOW_PLAY_AREA_BORDER = false; // toggle to render the play-area outline
  const ENABLE_REPLAY_UPLOAD = false; // gate replay upload UI while retaining implementation
  const ENABLE_IDLE_EDGE_ANIMATION = false; // animate pipes that are on but not flowing

  const game = new Phaser.Game(config);

  let ws = null;
  let screen = null;
  let nodes = new Map(); // id -> {x,y,size,owner}
  let edges = new Map(); // id -> {source,target,on,flowing,flowStartTime}
  let tickIntervalSec = 0.2; // provided by backend init; used to show per-second edge flow
  let settingsOpen = false; // persisted visibility of settings/toggles panel

  let graphicsStartZones;
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
  let currentResourceMode = 'standard';
  let brassGemModeActive = false;
  let pendingBrassGemSpend = false;
  let rageGemModeActive = false;
  let pendingRageGemSpend = false;
  let reverseGemModeActive = false;
  let pendingReverseGemSpend = false;
  let warpGemModeActive = false;
  let pendingWarpGemSpend = false;
  let activePipeGemKey = null;
  let lastWarpGemErrorTime = 0;
  let myPicked = false;
  let hiddenStartActive = false;
  let hiddenStartRevealed = false;
  let hiddenStartSide = null;
  let hiddenStartBoundary = null;
  let hiddenStartBounds = null;
  let goldValue = 0; // no limit
  let nodeMaxJuice = 50;
  let hoveredNodeId = null;
  let hoveredEdgeId = null;
  
  // Abilities system
  let activeAbility = null; // null, 'bridge1way', 'reverse'
  let bridgeFirstNode = null; // first selected node for bridge building
  let bridgeIsBrass = false; // true when building a brass pipe in cross mode
  let brassPreviewIntersections = new Set(); // edges that would be removed by the current brass preview
  let brassActivationDenied = false;
  let bridgePreviewWillBeBrass = false; // dynamic flag for previewing brass outcome
  let xbPreviewBlockedByBrass = false;
  let mouseWorldX = 0; // current mouse position in world coordinates
  let mouseWorldY = 0;
  let bridgeCostDisplay = null; // current bridge cost display text object
  let crownHealthDisplays = new Map(); // crown health text displays for king mode, keyed by node ID
  let reversePipeButtons = new Map();
  const REVERSE_BUTTON_OFFSET_PX = 24;

  function destroyCrownHealthDisplay(nodeId) {
    if (nodeId == null) return;
    const display = crownHealthDisplays.get(nodeId);
    if (!display) return;
    if (display.flickerTween) {
      display.flickerTween.remove();
      display.flickerTween = null;
    }
    try {
      if (typeof display.destroy === 'function') {
        display.destroy();
      } else if (typeof display.setVisible === 'function') {
        display.setVisible(false);
      }
    } catch (err) {
      if (typeof display.setVisible === 'function') {
        display.setVisible(false);
      }
    }
    crownHealthDisplays.delete(nodeId);
  }

  const DOUBLE_CLICK_DELAY_MS = 220;
  const EDGE_REMOVAL_STEP_DURATION = 0.2; // seconds per triangle removal step
  const EDGE_REMOVAL_ANIMATION_MODE = 'explosion'; // 'explosion' keeps triangles, 'classic' restores the legacy fade
  const EDGE_REMOVAL_EXPLOSION_CONFIG = {
    driftPixelsMin: 24,
    driftPixelsMax: 56,
    driftDurationMin: 0.45,
    driftDurationMax: 0.9,
    spinRotationsMin: 3.0,
    spinRotationsMax: 7.0,
    greyDelay: 0,
    restDuration: 5.0,
    fadeDuration: 0.6,
    greyColor: 0xf0f0f0,
    alphaDuringDriftStart: 0.55,
    alphaDuringDriftEnd: 0.95,
    alphaAtRest: 0.65,
    driftLightenFactor: 0.25,
  };
  const NODE_MOVE_DURATION_SEC = 1.8;
  const NODE_MOVE_EPSILON = 1e-4;
  const PRE_PIPE_COLOR = 0xbec4cf;
  const PRE_PIPE_OUTLINE_COLOR = 0x4a5568;
  const PRE_PIPE_SHAKE_SPEED = 6.4;
  const PRE_PIPE_SHAKE_AMPLITUDE = 2.1;
  const RAGE_PIPE_OFFSET = 3; // pixels to offset each side of the double fire pipe
  const REVERSE_PIPE_SPACING_MULTIPLIER = 1.5;
  let pendingSingleClickTimeout = null;
  let pendingSingleClickData = null;

  // Bridge costs - dynamically loaded from backend settings
  let BRIDGE_BASE_COST = 0;
  let BRIDGE_COST_PER_UNIT = 1.5;

  // Progress bar for node count victory
  let progressBar = null;
  let progressBarInner = null;
  let progressMarkerLeft = null;
  let progressMarkerRight = null;
  let progressSegments = new Map();
  let progressNameContainer = null;
  let progressNameSegments = new Map();
  let winThreshold = 40; // default, will be updated from backend
  let totalNodes = 60; // default, will be updated from backend
  let winCondition = 'dominate';
  const kingNodesByPlayer = new Map();
  let kingSelectionActive = false;
  let kingSelectedNodeId = null;
  const kingMoveTargets = new Set();
  let kingMoveTargetsList = [];
  let kingMoveOptionsPending = false;
  const kingMoveTargetRenderInfo = new Map();
  let kingMoveTargetHoveredId = null;
  let kingMovePendingDestinationId = null;

  const KING_STANDARD_NODE_SIZE = 80;
  const KING_STANDARD_RADIUS_BASE = 0.15 * Math.pow(KING_STANDARD_NODE_SIZE, 0.6);
  const KING_CROWN_TO_NODE_RATIO = 0.6;
  const KING_CROWN_MIN_SCREEN_RADIUS = 14;
  const KING_OPTION_RADIUS_MULTIPLIER = 1.12;
  const KING_OPTION_BOUNCE_SCALE = 0.18;
  const KING_OPTION_VERTICAL_SCALE = 0.55;
  const KING_OPTION_VERTICAL_EXTRA = 6;
  const KING_CROWN_FILL_COLOR = 0xffd700;
  const KING_CROWN_DEFAULT_HEALTH = 300;
  let kingCrownDefaultMax = KING_CROWN_DEFAULT_HEALTH;

  function computeStandardKingNodeRadius(baseScale = 1) {
    const scale = Math.max(0.0001, Number(baseScale) || 1);
    return Math.max(1, KING_STANDARD_RADIUS_BASE * scale);
  }

  function computeStandardKingCrownRadius(baseScale = 1, multiplier = 1) {
    const nodeRadius = computeStandardKingNodeRadius(baseScale);
    const baseCrownRadius = Math.max(10, nodeRadius * KING_CROWN_TO_NODE_RATIO);
    const scale = Number.isFinite(multiplier) && multiplier > 0 ? multiplier : 1;
    return Math.max(KING_CROWN_MIN_SCREEN_RADIUS, baseCrownRadius * scale);
  }

  function darkenColor(hexColor, multiplier = 1) {
    const factor = Math.max(0, Math.min(1, Number(multiplier) || 0));
    const r = (hexColor >> 16) & 0xff;
    const g = (hexColor >> 8) & 0xff;
    const b = hexColor & 0xff;
    const dr = Math.round(r * factor);
    const dg = Math.round(g * factor);
    const db = Math.round(b * factor);
    return (dr << 16) | (dg << 8) | db;
  }
  
  // UI background bars
  let topUiBar = null;
  let bottomUiBar = null;
  let gemCountsDisplay = null;
  let gemCountsClickHandlerBound = false;
  const gemCountLabels = new Map();

  // Warp mode visuals & geometry (frontend prototype hooked to Warp mode)
  const WARP_MARGIN_RATIO_X = 0.06; // horizontal extra space relative to board width
  const WARP_MARGIN_RATIO_Y = 0.10; // vertical extra space relative to board height
  const WARP_BORDER_COLOR = 0x9b4dff; // purple border for warp space
  const VIRTUAL_CURSOR_COLOR = '#b675ff';
  let warpBoundsWorld = null; // { minX, minY, maxX, maxY, width, height }
  let warpBoundsScreen = null; // { minX, minY, maxX, maxY }

  // Virtual cursor (pointer-lock driven) to support warp wrap previews
  let pointerLockActive = false;
  let virtualCursorScreenX = window.innerWidth / 2;
  let virtualCursorScreenY = window.innerHeight / 2;
  let lastPointerClientX = virtualCursorScreenX;
  let lastPointerClientY = virtualCursorScreenY;
  let virtualCursorEl = null;
  let lastPointerDownButton = null;
  let pendingVirtualUiClickTarget = null;
  let warpWrapUsed = false;
  let lastDoubleWarpWarningTime = 0;
  let lastWarpAxis = null;
  let lastWarpDirection = null;

  // Timer system
  let timerDisplay = null;
  let gameStartTime = null;
  let gameDuration = 10 * 60; // default to 10 minutes in seconds; server can override
  
  // Auto-expand system
  let autoExpandToggle = null;
  let homeAutoExpandToggle = null;
  let myAutoExpand = false; // my player's auto-expand setting
  let persistentAutoExpand = false; // persistent setting stored in localStorage
  
  // Auto-attack system
  let autoAttackToggle = null;
  let homeAutoAttackToggle = null;
  let myAutoAttack = false; // my player's auto-attack setting
  let persistentAutoAttack = false; // persistent setting stored in localStorage
  
  // Numbers toggle system
  let numbersToggle = null;
  let persistentNumbers = true; // persistent setting stored in localStorage (default to true)
  
  // Edge Flow toggle system
  let edgeFlowToggle = null;
  let persistentEdgeFlow = true; // persistent setting stored in localStorage (default to true)
  let edgeFlowTexts = new Map(); // edgeId -> text object
  const edgeRemovalAnimations = new Map(); // edgeId -> removal animation state

  // Pre-move pipe system
  let preMoveToggle = null;
  let homePreMoveToggle = null;
  let persistentPreMove = false;
  const prePipes = new Map(); // id -> record
  const prePipeKeyIndex = new Map(); // "from->to" -> id
  let nextPrePipeId = 1;
  
  // Targeting overlay support (legacy feature locked off for now)
  let persistentTargeting = false;
  
  // Node juice display system
  let nodeJuiceTexts = new Map(); // nodeId -> text object
  let nodeResourceTexts = new Map(); // nodeId -> emoji text object
  // Targeting visual indicator system
  let currentTargetNodeId = null; // The node currently being targeted (for visual indicator)
  let currentTargetSetTime = null; // Animation time when target was last set
  
  const DEFAULT_MODE_SETTINGS = IS_LEGACY_CLIENT
    ? {
        screen: 'flat',
        brass: 'cross',
        brassStart: 'owned',
        bridgeCost: 0.9,
        gameStart: 'open',
        startingNodeJuice: 300,
        passiveIncome: 0,
        neutralCaptureGold: 10,
        ringJuiceToGoldRatio: 30,
        ringPayoutGold: 10,
        baseMode: LEGACY_DEFAULT_MODE || 'basic',
        derivedMode: LEGACY_DEFAULT_MODE || 'basic',
        winCondition: 'king',
        kingCrownHealth: KING_CROWN_DEFAULT_HEALTH,
        resources: 'standard',
      }
    : {
        screen: 'warp',
        brass: 'gem',
        brassStart: 'owned',
        bridgeCost: 1.0,
        gameStart: 'open',
        startingNodeJuice: 300,
        passiveIncome: 1,
        neutralCaptureGold: 0,
        ringJuiceToGoldRatio: 30,
        ringPayoutGold: 10,
        winCondition: 'king',
        kingCrownHealth: KING_CROWN_DEFAULT_HEALTH,
        resources: 'gems',
      };

  const INITIAL_MODE = LEGACY_DEFAULT_MODE || (IS_LEGACY_CLIENT ? 'basic' : 'flat');

  let selectedPlayerCount = 2;
  let selectedMode = INITIAL_MODE;
  let gameMode = INITIAL_MODE;
  let selectedSettings = { ...DEFAULT_MODE_SETTINGS };
  let modeOptionsButton = null;
  let modeOptionsPanel = null;
  let modeSelectorContainer = null;
  let modeOptionButtons = [];
  let modePanelOpen = false;
  let bridgeCostSlider = null;
  let bridgeCostValueLabel = null;
  let passiveIncomeSlider = null;
  let passiveIncomeValueLabel = null;
  let neutralCaptureSlider = null;
  let neutralCaptureValueLabel = null;
  let ringRatioSlider = null;
  let ringRatioValueLabel = null;
  let ringPayoutSlider = null;
  let ringPayoutValueLabel = null;
  let startingJuiceSlider = null;
  let startingJuiceValueLabel = null;
  let crownHealthSlider = null;
  let crownHealthValueLabel = null;
  const BRIDGE_COST_MIN = 0.5;
  const BRIDGE_COST_MAX = 1.0;
  const BRIDGE_COST_STEP = 0.1;
  const PASSIVE_INCOME_MIN = 0;
  const PASSIVE_INCOME_MAX = 1;
  const PASSIVE_INCOME_STEP = 0.05;
  const NEUTRAL_CAPTURE_MIN = 0;
  const NEUTRAL_CAPTURE_MAX = 20;
  const NEUTRAL_CAPTURE_STEP = 1;
  const RING_RATIO_MIN = 5;
  const RING_RATIO_MAX = 30;
  const RING_RATIO_STEP = 1;
  const RING_PAYOUT_MIN = 1;
  const RING_PAYOUT_MAX = 20;
  const RING_PAYOUT_STEP = 1;
  const STARTING_JUICE_MIN = 50;
  const STARTING_JUICE_MAX = 300;
  const STARTING_JUICE_STEP = 10;
  const CROWN_HEALTH_MIN = 1;
  const CROWN_HEALTH_MAX = 300;
  const CROWN_HEALTH_STEP = 1;
  const MODE_LABELS = {
    sparse: 'Sparse',
    basic: 'OG Durb',
    brass: 'Brass',
    go: 'Go',
    overflow: 'Overflow',
    nuke: 'Nuke',
    cross: 'Cross',
    warp: 'Warp',
    'warp-old': 'Warp (Old)',
    semi: 'Semi',
    'i-semi': 'I-Semi',
    'i-warp': 'I-Warp',
    'i-flat': 'I-Flat',
    'brass-old': 'Brass-Old',
    flat: 'Flat',
    sandbox: 'Sandbox',
  };
  const DEFAULT_OVERFLOW_PENDING_GOLD_THRESHOLD = DEFAULT_MODE_SETTINGS.ringPayoutGold;
  let OVERFLOW_PENDING_GOLD_THRESHOLD = DEFAULT_OVERFLOW_PENDING_GOLD_THRESHOLD;
  const BRASS_PIPE_COLOR = 0x8b6f14;
  const BRASS_PIPE_DIM_COLOR = 0x46320a;
  const BRASS_PIPE_OUTLINE_COLOR = 0x6f5410;
  const REVERSE_PIPE_COLOR = 0x4c6ef5;
  const REVERSE_PIPE_DIM_COLOR = 0x34487c;
  const PIPE_TRIANGLE_HEIGHT = 16;
  const PIPE_TRIANGLE_WIDTH = 12;
  const BRASS_TRIANGLE_OUTER_SCALE = 1.12; // tweak (>1) to adjust how much farther brass extends outward
  const BRASS_TRIANGLE_OUTER_HEIGHT_BONUS = PIPE_TRIANGLE_HEIGHT * (BRASS_TRIANGLE_OUTER_SCALE - 1);
  const BRASS_TRIANGLE_OUTER_WIDTH_BONUS = PIPE_TRIANGLE_WIDTH * (BRASS_TRIANGLE_OUTER_SCALE - 1);
  const BRASS_OUTER_OUTLINE_THICKNESS = 3;
  const BUILDING_TRIANGLE_INITIAL_SCALE = 1.8; // >1 means newly placed triangles start this many times larger
  const BUILDING_TRIANGLE_SHRINK_DURATION = 0.8; // seconds for construction triangles to shrink back to normal size
  const MONEY_SPEND_COLOR = '#b87333';
  const MONEY_SPEND_STROKE = '#4e2a10';
  const MONEY_GAIN_COLOR = '#ffd700';
  const RESOURCE_EMOJIS = {
    money: '',
    gem: {
      warp: '⭐',
      brass: '🟫',
      rage: '🔥',
      reverse: '🔄',
      default: '💎',
    },
  };

  const GEM_TYPE_ORDER = ['warp', 'brass', 'rage', 'reverse'];

  function normalizeGemKey(value) {
    if (typeof value !== 'string') return null;
    const normalized = value.trim().toLowerCase();
    if (!normalized) return null;
    return GEM_TYPE_ORDER.includes(normalized) ? normalized : null;
  }

  function normalizePipeType(value) {
    if (typeof value === 'string') {
      const normalized = value.trim().toLowerCase();
      if (normalized && ['normal', 'gold', 'rage', 'reverse'].includes(normalized)) {
        return normalized;
      }
    }
    if (value === 'gold') return 'gold';
    return 'normal';
  }

  function createEmptyGemCounts() {
    const counts = {};
    GEM_TYPE_ORDER.forEach((key) => {
      counts[key] = 0;
    });
    return counts;
  }

  function createDefaultPlayerStats() {
    return {
      nodes: 0,
      gold: 0,
      gems: createEmptyGemCounts(),
    };
  }

  function setModeSelectorVisibility(visible) {
    if (!modeSelectorContainer || !modeSelectorContainer.isConnected) {
      modeSelectorContainer = document.querySelector('.mode-selector');
    }
    if (!modeSelectorContainer) return;

    if (visible) {
      modeSelectorContainer.style.display = '';
      return;
    }

    modeSelectorContainer.style.display = 'none';

    if (!modePanelOpen) return;

    if (!modeOptionsPanel || !modeOptionsPanel.isConnected) {
      modeOptionsPanel = document.getElementById('modeOptionsPanel');
    }
    if (modeOptionsPanel) {
      modeOptionsPanel.style.display = 'none';
      modeOptionsPanel.setAttribute('aria-hidden', 'true');
    }
    if (!modeOptionsButton || !modeOptionsButton.isConnected) {
      modeOptionsButton = document.getElementById('modeOptionsButton');
    }
    if (modeOptionsButton) modeOptionsButton.setAttribute('aria-expanded', 'false');
    modePanelOpen = false;
  }

  function coerceBridgeCost(value) {
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 0) {
      const clamped = Math.min(BRIDGE_COST_MAX, Math.max(BRIDGE_COST_MIN, numeric));
      return Math.round(clamped / BRIDGE_COST_STEP) * BRIDGE_COST_STEP;
    }
    return DEFAULT_MODE_SETTINGS.bridgeCost;
  }

  function coercePassiveIncome(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return DEFAULT_MODE_SETTINGS.passiveIncome;
    const clamped = Math.min(PASSIVE_INCOME_MAX, Math.max(PASSIVE_INCOME_MIN, numeric));
    const stepped = Math.round(clamped / PASSIVE_INCOME_STEP) * PASSIVE_INCOME_STEP;
    return Number(stepped.toFixed(2));
  }

  function coerceNeutralCaptureReward(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return DEFAULT_MODE_SETTINGS.neutralCaptureGold;
    const clamped = Math.min(NEUTRAL_CAPTURE_MAX, Math.max(NEUTRAL_CAPTURE_MIN, numeric));
    return Math.round(clamped);
  }

  function coerceRingRatio(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return DEFAULT_MODE_SETTINGS.ringJuiceToGoldRatio;
    const clamped = Math.min(RING_RATIO_MAX, Math.max(RING_RATIO_MIN, numeric));
    return Math.round(clamped);
  }

  function coerceRingPayout(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return DEFAULT_MODE_SETTINGS.ringPayoutGold;
    const clamped = Math.min(RING_PAYOUT_MAX, Math.max(RING_PAYOUT_MIN, numeric));
    return Math.round(clamped);
  }

  function coerceStartingNodeJuice(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return DEFAULT_MODE_SETTINGS.startingNodeJuice;
    const clamped = Math.min(STARTING_JUICE_MAX, Math.max(STARTING_JUICE_MIN, numeric));
    const stepped = Math.round(clamped / STARTING_JUICE_STEP) * STARTING_JUICE_STEP;
    return Math.round(stepped);
  }

  function coerceKingCrownHealth(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return DEFAULT_MODE_SETTINGS.kingCrownHealth ?? KING_CROWN_DEFAULT_HEALTH;
    }
    const clamped = Math.min(CROWN_HEALTH_MAX, Math.max(CROWN_HEALTH_MIN, numeric));
    return Math.round(clamped);
  }

  function normalizeWinCondition(value) {
    if (typeof value !== 'string') return 'dominate';
    return value.trim().toLowerCase() === 'king' ? 'king' : 'dominate';
  }

  function normalizeResources(value) {
    if (typeof value !== 'string') return 'standard';
    return value.trim().toLowerCase() === 'gems' ? 'gems' : 'standard';
  }

  function isMagicResourceModeActive() {
    return normalizeResources(currentResourceMode) === 'gems';
  }

  function setCurrentResourceMode(mode) {
    currentResourceMode = normalizeResources(mode);
    bridgePreviewWillBeBrass = computeInitialBrassPreviewState();
    if (!isMagicResourceModeActive()) {
      pendingBrassGemSpend = false;
      setBrassGemModeActive(false);
      pendingWarpGemSpend = false;
      setWarpGemModeActive(false);
    } else {
      updateGemModeUi();
      if (activeAbility === 'bridge1way' && bridgeFirstNode != null) {
        const node = nodes.get(bridgeFirstNode);
        const nextPreference = determineBridgeBrassPreference(node, bridgeIsBrass);
        if (bridgeIsBrass !== nextPreference) {
          bridgeIsBrass = nextPreference;
        }
        bridgePreviewWillBeBrass = computeInitialBrassPreviewState();
        updateBrassPreviewIntersections();
        redrawStatic();
      }
    }
    updateModeOptionButtonStates();
  }

  function getMyGemCount(gemKey = 'brass') {
    const normalized = normalizeGemKey(gemKey);
    if (!normalized) return 0;
    let targetId = Number.isFinite(myPlayerId) ? myPlayerId : null;
    if (!Number.isFinite(targetId)) {
      const storedRaw = localStorage.getItem('myPlayerId');
      if (storedRaw != null) {
        const storedValue = Number(storedRaw);
        if (Number.isFinite(storedValue)) {
          targetId = storedValue;
        }
      }
    }
    if (!Number.isFinite(targetId)) return 0;
    const stats = ensurePlayerStats(targetId);
    if (!stats || !stats.gems) return 0;
    const value = Number(stats.gems[normalized]);
    return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
  }

  function canActivateBrassGemMode() {
    if (!isMagicResourceModeActive()) return false;
    return getMyGemCount('brass') > 0;
  }

  function canActivateWarpGemMode() {
    if (!isMagicResourceModeActive()) return false;
    return getMyGemCount('warp') > 0;
  }

  function canActivateRageGemMode() {
    if (!isMagicResourceModeActive()) return false;
    return getMyGemCount('rage') > 0;
  }

  function canActivateReverseGemMode() {
    if (!isMagicResourceModeActive()) return false;
    return getMyGemCount('reverse') > 0;
  }

  function isWarpWrapUnlocked() {
    if (!isMagicResourceModeActive()) return true;
    return warpGemModeActive && canActivateWarpGemMode();
  }

  function determineBridgeBrassPreference(startNode, useBrassHint = false) {
    if (isMagicResourceModeActive()) {
      return brassGemModeActive && canActivateBrassGemMode();
    }
    if (isBrassModeActive()) {
      return !!(startNode && startNode.isBrass);
    }
    if (isIntentionalBrassModeActive()) {
      return !!useBrassHint;
    }
    return false;
  }

  function computeInitialBrassPreviewState() {
    if (!bridgeIsBrass) return false;
    if (isXbModeActive()) return false;
    if (isMagicResourceModeActive()) return true;
    return isCrossLikeModeActive();
  }

  function determinePipeTypeForBridge(useBrassPipe = false) {
    if (isMagicResourceModeActive()) {
      if (rageGemModeActive && canActivateRageGemMode()) return 'rage';
      if (reverseGemModeActive && canActivateReverseGemMode()) return 'reverse';
      if (brassGemModeActive && canActivateBrassGemMode()) return 'gold';
    }
    return useBrassPipe ? 'gold' : 'normal';
  }

  function setBrassGemModeActive(enabled, options = {}) {
    const desired = Boolean(enabled) && isMagicResourceModeActive() && canActivateBrassGemMode();
    if (desired) {
      activePipeGemKey = 'brass';
      if (rageGemModeActive) {
        rageGemModeActive = false;
        pendingRageGemSpend = false;
      }
      if (reverseGemModeActive) {
        reverseGemModeActive = false;
        pendingReverseGemSpend = false;
      }
    } else if (activePipeGemKey === 'brass') {
      activePipeGemKey = null;
    }
    if (brassGemModeActive === desired) {
      if (isMagicResourceModeActive()) {
        bridgeIsBrass = brassGemModeActive && canActivateBrassGemMode();
      }
      updateGemModeUi();
      return;
    }
    brassGemModeActive = desired;
    const shouldClearPending = options.clearPending !== false;
    if (!desired && shouldClearPending) {
      pendingBrassGemSpend = false;
    }
    if (!desired && activePipeGemKey === 'brass') {
      activePipeGemKey = null;
    }
    updateGemModeUi();
    if (isMagicResourceModeActive()) {
      bridgeIsBrass = brassGemModeActive && canActivateBrassGemMode();
    }
    if (activeAbility === 'bridge1way' && bridgeFirstNode != null) {
      const node = nodes.get(bridgeFirstNode);
      const nextPreference = determineBridgeBrassPreference(node, bridgeIsBrass);
      if (bridgeIsBrass !== nextPreference) {
        bridgeIsBrass = nextPreference;
      }
      bridgePreviewWillBeBrass = computeInitialBrassPreviewState();
      updateBrassPreviewIntersections();
      redrawStatic();
    }
  }

  function setWarpGemModeActive(enabled, options = {}) {
    const desired = Boolean(enabled) && isMagicResourceModeActive() && canActivateWarpGemMode();
    if (warpGemModeActive === desired) {
      updateGemModeUi();
      return;
    }
    warpGemModeActive = desired;
    const shouldClearPending = options.clearPending !== false;
    if (!desired && shouldClearPending) {
      pendingWarpGemSpend = false;
    }
    if (!desired) {
      warpWrapUsed = false;
      lastWarpAxis = null;
      lastWarpDirection = null;
    }
    updateGemModeUi();
    if (activeAbility === 'bridge1way') {
      redrawStatic();
    }
  }

  function setRageGemModeActive(enabled, options = {}) {
    const desired = Boolean(enabled) && isMagicResourceModeActive() && canActivateRageGemMode();
    if (desired) {
      activePipeGemKey = 'rage';
      if (brassGemModeActive) {
        brassGemModeActive = false;
        pendingBrassGemSpend = false;
      }
      if (reverseGemModeActive) {
        reverseGemModeActive = false;
        pendingReverseGemSpend = false;
      }
    } else if (activePipeGemKey === 'rage') {
      activePipeGemKey = null;
    }
    if (rageGemModeActive === desired) {
      if (desired) {
        bridgeIsBrass = false;
      } else if (isMagicResourceModeActive()) {
        bridgeIsBrass = brassGemModeActive && canActivateBrassGemMode();
      }
      updateGemModeUi();
      return;
    }
    rageGemModeActive = desired;
    const shouldClearPending = options.clearPending !== false;
    if (!desired && shouldClearPending) {
      pendingRageGemSpend = false;
    }
    if (desired) {
      bridgeIsBrass = false;
    } else if (activePipeGemKey !== 'brass') {
      bridgeIsBrass = brassGemModeActive && canActivateBrassGemMode();
    }
    updateGemModeUi();
    if (activeAbility === 'bridge1way') {
      bridgePreviewWillBeBrass = computeInitialBrassPreviewState();
      updateBrassPreviewIntersections();
      redrawStatic();
    }
  }

  function setReverseGemModeActive(enabled, options = {}) {
    const desired = Boolean(enabled) && isMagicResourceModeActive() && canActivateReverseGemMode();
    if (desired) {
      activePipeGemKey = 'reverse';
      if (brassGemModeActive) {
        brassGemModeActive = false;
        pendingBrassGemSpend = false;
      }
      if (rageGemModeActive) {
        rageGemModeActive = false;
        pendingRageGemSpend = false;
      }
    } else if (activePipeGemKey === 'reverse') {
      activePipeGemKey = null;
    }
    if (reverseGemModeActive === desired) {
      if (desired) {
        bridgeIsBrass = false;
      } else if (isMagicResourceModeActive()) {
        bridgeIsBrass = brassGemModeActive && canActivateBrassGemMode();
      }
      updateGemModeUi();
      return;
    }
    reverseGemModeActive = desired;
    const shouldClearPending = options.clearPending !== false;
    if (!desired && shouldClearPending) {
      pendingReverseGemSpend = false;
    }
    if (desired) {
      bridgeIsBrass = false;
    } else if (activePipeGemKey !== 'brass') {
      bridgeIsBrass = brassGemModeActive && canActivateBrassGemMode();
    }
    updateGemModeUi();
    if (activeAbility === 'bridge1way') {
      bridgePreviewWillBeBrass = computeInitialBrassPreviewState();
      updateBrassPreviewIntersections();
      redrawStatic();
    }
  }

  function updateGemModeUi() {
    if (!gemCountsDisplay || !gemCountsDisplay.isConnected) return;
    const interactive = isMagicResourceModeActive();
    gemCountsDisplay.classList.toggle('interactive', interactive);

    const brassContainer = gemCountsDisplay.querySelector('[data-gem="brass"]');
    if (brassContainer) {
      const canUseBrass = canActivateBrassGemMode();
      brassContainer.classList.toggle('disabled', !canUseBrass);
      brassContainer.classList.toggle('active', canUseBrass && brassGemModeActive);
      brassContainer.setAttribute('aria-disabled', canUseBrass ? 'false' : 'true');
    }

    const rageContainer = gemCountsDisplay.querySelector('[data-gem="rage"]');
    if (rageContainer) {
      const canUseRage = canActivateRageGemMode();
      rageContainer.classList.toggle('disabled', !canUseRage);
      rageContainer.classList.toggle('active', canUseRage && rageGemModeActive);
      rageContainer.setAttribute('aria-disabled', canUseRage ? 'false' : 'true');
    }

    const reverseContainer = gemCountsDisplay.querySelector('[data-gem="reverse"]');
    if (reverseContainer) {
      const canUseReverse = canActivateReverseGemMode();
      reverseContainer.classList.toggle('disabled', !canUseReverse);
      reverseContainer.classList.toggle('active', canUseReverse && reverseGemModeActive);
      reverseContainer.setAttribute('aria-disabled', canUseReverse ? 'false' : 'true');
    }

    const warpContainer = gemCountsDisplay.querySelector('[data-gem="warp"]');
    if (warpContainer) {
      const canUseWarp = canActivateWarpGemMode();
      warpContainer.classList.toggle('disabled', !canUseWarp);
      warpContainer.classList.toggle('active', canUseWarp && warpGemModeActive);
      warpContainer.setAttribute('aria-disabled', canUseWarp ? 'false' : 'true');
    }
  }

  function handleGemCountsClick(ev) {
    if (!isMagicResourceModeActive()) return;
    const target = ev.target;
    const container = target && typeof target.closest === 'function'
      ? target.closest('.gem-count')
      : null;
    if (!container) return;
    const gemData = container.dataset ? container.dataset.gem : null;
    const gemKey = normalizeGemKey(gemData);
    if (gemKey === 'brass') {
      ev.preventDefault();
      ev.stopPropagation();
      if (!canActivateBrassGemMode()) {
        showErrorMessage('No brass gems available', 'error');
        return;
      }
      setBrassGemModeActive(!brassGemModeActive);
      return;
    }
    if (gemKey === 'rage') {
      ev.preventDefault();
      ev.stopPropagation();
      if (!canActivateRageGemMode()) {
        showErrorMessage('No rage gems available', 'error');
        return;
      }
      setRageGemModeActive(!rageGemModeActive);
      return;
    }
    if (gemKey === 'reverse') {
      ev.preventDefault();
      ev.stopPropagation();
      if (!canActivateReverseGemMode()) {
        showErrorMessage('No reverse gems available', 'error');
        return;
      }
      setReverseGemModeActive(!reverseGemModeActive);
      return;
    }
    if (gemKey === 'warp') {
      ev.preventDefault();
      ev.stopPropagation();
      if (!canActivateWarpGemMode()) {
        showErrorMessage('No warp gems available', 'error');
        return;
      }
      setWarpGemModeActive(!warpGemModeActive);
    }
  }

  function notifyWarpGemRequired() {
    const now = Date.now();
    if (now - lastWarpGemErrorTime < 600) return;
    lastWarpGemErrorTime = now;
    showErrorMessage('Warp gem required to warp pipes', 'error');
  }

  function normalizeNodeResourceType(value) {
    if (typeof value !== 'string') return 'money';
    return value.trim().toLowerCase() === 'gem' ? 'gem' : 'money';
  }

  function normalizeNodeResourceKey(value) {
    if (typeof value !== 'string') return null;
    const trimmed = value.trim().toLowerCase();
    return trimmed ? trimmed : null;
  }

  function getResourceEmoji(resourceType, resourceKey) {
    const normalizedType = normalizeNodeResourceType(resourceType);
    if (normalizedType === 'gem') {
      const normalizedKey = normalizeNodeResourceKey(resourceKey);
      if (normalizedKey && Object.prototype.hasOwnProperty.call(RESOURCE_EMOJIS.gem, normalizedKey)) {
        return RESOURCE_EMOJIS.gem[normalizedKey];
      }
      return RESOURCE_EMOJIS.gem.default;
    }
    return RESOURCE_EMOJIS.money;
  }

  function deriveModeFromSettings(settings = selectedSettings) {
    if (IS_LEGACY_CLIENT) {
      return LEGACY_DEFAULT_MODE || 'basic';
    }
    if (!settings || typeof settings !== 'object') return 'flat';
    const screen = typeof settings.screen === 'string' ? settings.screen.toLowerCase() : 'flat';
    const brass = typeof settings.brass === 'string' ? settings.brass.toLowerCase() : 'cross';
    if (brass === 'gem') {
      if (screen === 'warp') return 'warp';
      if (screen === 'semi') return 'semi';
      return 'flat';
    }
    if (screen === 'warp') {
      return brass.startsWith('right') ? 'i-warp' : 'warp';
    }
    if (screen === 'semi') {
      return brass.startsWith('right') ? 'i-semi' : 'semi';
    }
    return brass.startsWith('right') ? 'i-flat' : 'flat';
  }

  function formatModeSettingsSummary(settings = selectedSettings) {
    const screenValue = typeof settings.screen === 'string' ? settings.screen.toLowerCase() : 'flat';
    const screenLabel = screenValue === 'warp' ? 'Warp' : (screenValue === 'semi' ? 'Semi' : 'Flat');
    const brassValue = typeof settings.brass === 'string' ? settings.brass.toLowerCase() : 'cross';
    const brassLabel = brassValue === 'gem'
      ? 'Gem'
      : (brassValue === 'right-click' ? 'Right-Click' : 'Cross');
    const startLabel = (settings.brassStart === 'anywhere') ? 'Anywhere' : 'Owned';
    const startModeLabel = (settings.gameStart === 'hidden-split') ? 'Hidden' : 'Open';
    const costLabel = coerceBridgeCost(settings.bridgeCost).toFixed(1);
    const passiveLabel = `${coercePassiveIncome(settings.passiveIncome).toFixed(2)}/s`;
    const neutralLabel = coerceNeutralCaptureReward(settings.neutralCaptureGold);
    const ringRatioLabel = coerceRingRatio(settings.ringJuiceToGoldRatio);
    const ringPayoutLabel = coerceRingPayout(settings.ringPayoutGold);
    const startJuiceLabel = coerceStartingNodeJuice(settings.startingNodeJuice);
    const winConLabel = normalizeWinCondition(settings.winCondition) === 'king' ? 'King' : 'Dominate';
    const resourcesLabel = normalizeResources(settings.resources) === 'gems' ? 'Gems' : 'Standard';
    return `Resources ${resourcesLabel} · Win-Con ${winConLabel} · ${screenLabel} · ${brassLabel} · ${startLabel} · ${startModeLabel} · ${costLabel} · Passive ${passiveLabel} · Neutral ${neutralLabel} · Ring ${ringRatioLabel}:${ringPayoutLabel} · Start ${startJuiceLabel}`;
  }

  function updateModeOptionButtonStates() {
    if (!Array.isArray(modeOptionButtons)) return;
    const currentScreen = (selectedSettings.screen || 'flat').toLowerCase();
    const currentBrass = (selectedSettings.brass || 'cross').toLowerCase();
    const currentStart = (selectedSettings.brassStart || DEFAULT_MODE_SETTINGS.brassStart).toLowerCase();
    const currentCost = Number(coerceBridgeCost(selectedSettings.bridgeCost));
    const currentGameStart = (selectedSettings.gameStart || DEFAULT_MODE_SETTINGS.gameStart).toLowerCase();
    const currentResources = normalizeResources(selectedSettings.resources);
    const hiddenAllowed = isHiddenStartAllowed();
    const brassGroup = document.querySelector('.mode-option-group[data-setting-group="brass"]');
    if (brassGroup) {
      brassGroup.classList.toggle('gem-mode', currentResources === 'gems');
    }
    modeOptionButtons.forEach((btn) => {
      const setting = btn?.dataset?.setting;
      const value = btn?.dataset?.value;
      if (!setting || typeof value === 'undefined') {
        btn.classList.remove('active');
        return;
      }
      btn.disabled = false;
      btn.classList.remove('disabled');
      btn.title = '';
      let isActive = false;
      if (setting === 'screen') {
        isActive = value.toLowerCase() === currentScreen;
        if (currentResources === 'gems') {
          btn.disabled = true;
          btn.classList.add('disabled');
          btn.title = 'Gem mode requires the Warp screen';
        }
      } else if (setting === 'brass') {
        const normalized = value.toLowerCase();
        if (currentResources === 'gems') {
          btn.disabled = true;
          btn.classList.add('disabled');
          btn.classList.remove('active');
          btn.title = 'Brass selection handled by gems';
          isActive = false;
        } else {
          const target = currentBrass === 'right-click' ? 'right-click' : 'cross';
          isActive = normalized === target;
        }
      } else if (setting === 'brassStart') {
        const normalized = value.toLowerCase();
        if (currentResources === 'gems') {
          btn.disabled = true;
          btn.classList.add('disabled');
          btn.title = 'Gem mode requires starting from owned nodes';
          isActive = normalized === 'owned';
        } else {
          const target = currentStart === 'anywhere' ? 'anywhere' : 'owned';
          isActive = normalized === target;
        }
      } else if (setting === 'gameStart') {
        const normalized = value.toLowerCase();
        const target = normalized.startsWith('hidden') ? 'hidden-split' : 'open';
        const current = currentGameStart.startsWith('hidden') ? 'hidden-split' : 'open';
        const shouldDisable = target === 'hidden-split' && !hiddenAllowed;
        if (shouldDisable) {
          btn.disabled = true;
          btn.classList.add('disabled');
          isActive = false;
        } else {
          isActive = target === current;
        }
      } else if (setting === 'bridgeCost') {
        isActive = Number(value) === currentCost;
      } else if (setting === 'winCondition') {
        const normalized = normalizeWinCondition(value);
        isActive = normalized === normalizeWinCondition(selectedSettings.winCondition);
      } else if (setting === 'resources') {
        isActive = normalizeResources(value) === currentResources;
      }
      btn.classList.toggle('active', isActive);
    });
  }

  function isHiddenStartAllowed() {
    return selectedPlayerCount === 2;
  }

  function enforceGameStartAvailability() {
    if (!isHiddenStartAllowed() && selectedSettings.gameStart === 'hidden-split') {
      applySelectedSettings({ gameStart: 'open' });
    } else {
      updateModeOptionButtonStates();
    }
  }

  function applySelectedSettings(overrides = {}) {
    if (IS_LEGACY_CLIENT) {
      selectedSettings = { ...DEFAULT_MODE_SETTINGS };
      selectedMode = LEGACY_DEFAULT_MODE || 'basic';
      gameMode = selectedMode;
      return;
    }
    const next = { ...selectedSettings };
    if (Object.prototype.hasOwnProperty.call(overrides, 'screen')) {
      const screen = typeof overrides.screen === 'string' ? overrides.screen.toLowerCase() : '';
      if (screen === 'warp' || screen === 'semi') {
        next.screen = screen;
      } else {
        next.screen = 'flat';
      }
    } else {
      const currentScreen = typeof next.screen === 'string' ? next.screen.toLowerCase() : 'flat';
      next.screen = (currentScreen === 'warp' || currentScreen === 'semi') ? currentScreen : 'flat';
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'brass')) {
      const brass = typeof overrides.brass === 'string' ? overrides.brass.toLowerCase() : '';
      if (brass === 'gem') {
        next.brass = 'gem';
      } else {
        next.brass = brass.startsWith('right') ? 'right-click' : 'cross';
      }
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'brassStart')) {
      const start = typeof overrides.brassStart === 'string' ? overrides.brassStart.toLowerCase() : '';
      next.brassStart = start === 'anywhere' ? 'anywhere' : 'owned';
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'gameStart')) {
      const startMode = typeof overrides.gameStart === 'string' ? overrides.gameStart.toLowerCase() : '';
      next.gameStart = startMode.startsWith('hidden') ? 'hidden-split' : 'open';
    } else {
      next.gameStart = (next.gameStart === 'hidden-split') ? 'hidden-split' : 'open';
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'bridgeCost')) {
      next.bridgeCost = coerceBridgeCost(overrides.bridgeCost);
    } else {
      next.bridgeCost = coerceBridgeCost(next.bridgeCost);
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'passiveIncome')) {
      next.passiveIncome = coercePassiveIncome(overrides.passiveIncome);
    } else {
      next.passiveIncome = coercePassiveIncome(next.passiveIncome);
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'neutralCaptureGold')) {
      next.neutralCaptureGold = coerceNeutralCaptureReward(overrides.neutralCaptureGold);
    } else {
      next.neutralCaptureGold = coerceNeutralCaptureReward(next.neutralCaptureGold);
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'ringJuiceToGoldRatio')) {
      next.ringJuiceToGoldRatio = coerceRingRatio(overrides.ringJuiceToGoldRatio);
    } else {
      next.ringJuiceToGoldRatio = coerceRingRatio(next.ringJuiceToGoldRatio);
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'ringPayoutGold')) {
      next.ringPayoutGold = coerceRingPayout(overrides.ringPayoutGold);
    } else {
      next.ringPayoutGold = coerceRingPayout(next.ringPayoutGold);
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'startingNodeJuice')) {
      next.startingNodeJuice = coerceStartingNodeJuice(overrides.startingNodeJuice);
    } else {
      next.startingNodeJuice = coerceStartingNodeJuice(next.startingNodeJuice);
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'kingCrownHealth')) {
      next.kingCrownHealth = coerceKingCrownHealth(overrides.kingCrownHealth);
    } else {
      next.kingCrownHealth = coerceKingCrownHealth(next.kingCrownHealth);
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'winCondition')) {
      next.winCondition = normalizeWinCondition(overrides.winCondition);
    } else {
      next.winCondition = normalizeWinCondition(next.winCondition);
    }
    if (Object.prototype.hasOwnProperty.call(overrides, 'resources')) {
      next.resources = normalizeResources(overrides.resources);
    } else {
      next.resources = normalizeResources(next.resources);
    }

    if (next.resources === 'gems') {
      next.brass = 'gem';
      next.brassStart = 'owned';
      next.screen = 'warp';
    } else if (next.brass === 'gem') {
      next.brass = 'cross';
    } else {
      const currentBrass = typeof next.brass === 'string' ? next.brass.toLowerCase() : 'cross';
      next.brass = currentBrass.startsWith('right') ? 'right-click' : 'cross';
    }

    if (next.gameStart === 'hidden-split' && !isHiddenStartAllowed()) {
      next.gameStart = 'open';
    }

    selectedSettings = next;
    setCurrentResourceMode(selectedSettings.resources);
    kingCrownDefaultMax = coerceKingCrownHealth(selectedSettings.kingCrownHealth);
    selectedMode = deriveModeFromSettings(selectedSettings);
    updateModeOptionButtonStates();
    syncBridgeCostSlider();
    syncPassiveIncomeSlider();
    syncNeutralCaptureSlider();
    syncRingRatioSlider();
    syncRingPayoutSlider();
    syncStartingJuiceSlider();
    syncCrownHealthSlider();
    updatePlayBotAvailability(true);
  }

  function buildModeSettingsPayload() {
    return {
      screen: selectedSettings.screen,
      brass: selectedSettings.brass,
      brassStart: selectedSettings.brassStart,
      bridgeCost: Number(coerceBridgeCost(selectedSettings.bridgeCost).toFixed(1)),
      gameStart: selectedSettings.gameStart,
      startingNodeJuice: coerceStartingNodeJuice(selectedSettings.startingNodeJuice),
      passiveIncome: coercePassiveIncome(selectedSettings.passiveIncome),
      neutralCaptureGold: coerceNeutralCaptureReward(selectedSettings.neutralCaptureGold),
      ringJuiceToGoldRatio: coerceRingRatio(selectedSettings.ringJuiceToGoldRatio),
      ringPayoutGold: coerceRingPayout(selectedSettings.ringPayoutGold),
      kingCrownHealth: coerceKingCrownHealth(selectedSettings.kingCrownHealth),
      baseMode: selectedMode,
      derivedMode: selectedMode,
      winCondition: selectedSettings.winCondition || 'dominate',
      resources: normalizeResources(selectedSettings.resources),
    };
  }

  function syncSelectedSettingsFromPayload(payload) {
    if (!payload || typeof payload !== 'object') return;
    const overrides = {};
    if (typeof payload.screen === 'string') overrides.screen = payload.screen;
    if (typeof payload.brass === 'string') overrides.brass = payload.brass;
    if (typeof payload.pipeStart === 'string') overrides.brassStart = payload.pipeStart;
    else if (typeof payload.brassStart === 'string') overrides.brassStart = payload.brassStart;
    if (typeof payload.gameStart === 'string') overrides.gameStart = payload.gameStart;
    if (Object.prototype.hasOwnProperty.call(payload, 'startingNodeJuice')) overrides.startingNodeJuice = payload.startingNodeJuice;
    if (Object.prototype.hasOwnProperty.call(payload, 'bridgeCost')) overrides.bridgeCost = payload.bridgeCost;
    if (Object.prototype.hasOwnProperty.call(payload, 'passiveIncome')) overrides.passiveIncome = payload.passiveIncome;
    if (Object.prototype.hasOwnProperty.call(payload, 'neutralCaptureGold')) overrides.neutralCaptureGold = payload.neutralCaptureGold;
    if (Object.prototype.hasOwnProperty.call(payload, 'ringJuiceToGoldRatio')) overrides.ringJuiceToGoldRatio = payload.ringJuiceToGoldRatio;
    if (Object.prototype.hasOwnProperty.call(payload, 'ringPayoutGold')) overrides.ringPayoutGold = payload.ringPayoutGold;
    if (Object.prototype.hasOwnProperty.call(payload, 'kingCrownHealth')) overrides.kingCrownHealth = payload.kingCrownHealth;
    if (Object.prototype.hasOwnProperty.call(payload, 'winCondition')) overrides.winCondition = payload.winCondition;
    if (Object.prototype.hasOwnProperty.call(payload, 'resources')) overrides.resources = payload.resources;
    applySelectedSettings(overrides);
  }

  function syncBridgeCostSlider() {
    if (!bridgeCostSlider || !bridgeCostValueLabel) return;
    const value = Math.max(
      BRIDGE_COST_MIN,
      Math.min(BRIDGE_COST_MAX, coerceBridgeCost(selectedSettings.bridgeCost))
    );
    bridgeCostSlider.value = value.toFixed(1);
    bridgeCostValueLabel.textContent = value.toFixed(1);
  }

  function syncPassiveIncomeSlider() {
    if (!passiveIncomeSlider || !passiveIncomeValueLabel) return;
    const value = coercePassiveIncome(selectedSettings.passiveIncome);
    passiveIncomeSlider.value = value.toFixed(2);
    passiveIncomeValueLabel.textContent = `${value.toFixed(2)}/s`;
  }

  function syncNeutralCaptureSlider() {
    if (!neutralCaptureSlider || !neutralCaptureValueLabel) return;
    const value = coerceNeutralCaptureReward(selectedSettings.neutralCaptureGold);
    neutralCaptureSlider.value = String(value);
    neutralCaptureValueLabel.textContent = String(value);
  }

  function syncRingRatioSlider() {
    if (!ringRatioSlider || !ringRatioValueLabel) return;
    const value = coerceRingRatio(selectedSettings.ringJuiceToGoldRatio);
    ringRatioSlider.value = String(value);
    ringRatioValueLabel.textContent = String(value);
  }

  function syncRingPayoutSlider() {
    if (!ringPayoutSlider || !ringPayoutValueLabel) return;
    const value = coerceRingPayout(selectedSettings.ringPayoutGold);
    ringPayoutSlider.value = String(value);
    ringPayoutValueLabel.textContent = String(value);
  }

  function syncStartingJuiceSlider() {
    if (!startingJuiceSlider || !startingJuiceValueLabel) return;
    const value = coerceStartingNodeJuice(selectedSettings.startingNodeJuice);
    startingJuiceSlider.value = String(value);
    startingJuiceValueLabel.textContent = String(value);
  }

  function syncCrownHealthSlider() {
    if (!crownHealthSlider || !crownHealthValueLabel) return;
    const value = coerceKingCrownHealth(selectedSettings.kingCrownHealth);
    crownHealthSlider.value = String(value);
    crownHealthValueLabel.textContent = String(value);
  }

  function pipeStartRequiresOwnership() {
    if (sandboxModeEnabled()) {
      return false;
    }
    return (selectedSettings.brassStart || DEFAULT_MODE_SETTINGS.brassStart) !== 'anywhere';
  }

  function isWarpLike(mode) {
    const normalized = normalizeMode(mode);
    return ['warp-old', 'warp', 'i-warp', 'semi', 'i-semi', 'sparse', 'overflow', 'nuke', 'cross', 'brass-old', 'go'].includes(normalized);
  }

  function isSemiWarpMode(value) {
    const normalized = normalizeMode(value);
    return normalized === 'semi' || normalized === 'i-semi';
  }

  function isSemiWarpDisplayActive() {
    if (isSemiWarpMode(gameMode) && isWarpLike(gameMode)) {
      return true;
    }
    if (!isWarpLike(gameMode)) {
      return isSemiWarpMode(selectedMode);
    }
    return false;
  }

  function isSemiWarpGameplayActive() {
    return isSemiWarpMode(gameMode);
  }

  function getWarpAxisPermissionsForGameplay() {
    if (!isWarpLike(gameMode)) {
      return { horizontal: false, vertical: false };
    }
    return {
      horizontal: !isSemiWarpGameplayActive(),
      vertical: true,
    };
  }

  // Money transparency system
  let moneyIndicators = []; // Array of {x, y, text, color, startTime, duration}

  let sceneRef = null;
  let quitButton = null;
  let forfeitKeyListenerAttached = false;
  let sandboxResetButton = null;
  let sandboxClearButton = null;
  let rematchButton = null;
  let saveReplayWrapper = null;
  let saveReplayButton = null;
  let reviewDropdownButton = null;
  let postgameNotice = null;
  let lobbyBackButton = null;
  let playFriendsBtn = null;
  let playBotBtnEl = null;
  let currentPostgameGroupId = null;
  let opponentHasLeft = false;
  let iHaveRematched = false;
  
  // Replay playback state
  let replayMode = false;
  let replayStartPending = false;
  let replaySessionActive = false;
  let pendingReplayPayload = null;
  let activeReplayPayload = null;
  let replayWatchBtnEl = null;
  let replayFileLabelEl = null;
  let replayFileInputEl = null;
  let replaySpeedContainer = null;
  let replayControlsWrapper = null;
  let replayRestartButton = null;
  let replaySpeedInput = null;
  let replaySpeedValue = 1;
  let replaySpeedLabel = null;
  let replayPanelEl = null;
  let replayBodyEl = null;
  let replayHeaderEl = null;
  let replayToggleIconEl = null;
  let pendingReplayIntent = null; // 'download' | 'review'
  let pendingReplayRestart = null;
  let reviewReplayActive = false; // true when the active replay came from postgame review
  let reviewReplayDownloadButton = null;
  let reviewReplayLastData = null;
  let reviewReplayLastFilename = null;

  // Centralized sound helpers provided by sounds.js
  const noop = () => {};
  const soundApiFallback = {
    loadPersistentSound: () => false,
    savePersistentSound: () => false,
    ensureAudio: noop,
    playCaptureDing: noop,
    playEnemyCaptureDing: noop,
    playChaChing: noop,
    playLoseNodeWarning: noop,
    playBridgeHammerHit: noop,
    playBridgeExplosion: noop,
    playReverseShuffle: noop,
    isSoundEnabled: () => false,
  };
  const soundApi = window.DurbSounds
    ? Object.assign({}, soundApiFallback, window.DurbSounds)
    : soundApiFallback;
  const {
    loadPersistentSound,
    savePersistentSound,
    ensureAudio,
    playCaptureDing,
    playEnemyCaptureDing,
    playChaChing,
    playLoseNodeWarning,
    playBridgeHammerHit,
    playBridgeExplosion,
    playReverseShuffle,
    isSoundEnabled,
  } = soundApi;

  // Target spacing between hammer hits in seconds (consistent regardless of bridge size)
  const BRIDGE_HIT_SPACING_SEC = 0.25;

  function setReplayStatus() {}

  function cloneReplayPayload(payload) {
    if (!payload || typeof payload !== 'object') return null;
    try {
      return JSON.parse(JSON.stringify(payload));
    } catch (_) {
      return payload;
    }
  }

  function ensureReplayElements() {
    if (!replayWatchBtnEl) replayWatchBtnEl = document.getElementById('replayWatchBtn');
    if (!replayFileLabelEl) replayFileLabelEl = document.getElementById('replayFileLabel');
    if (!replayFileInputEl) replayFileInputEl = document.getElementById('replayFileInput');
    if (!replayPanelEl) replayPanelEl = document.getElementById('replayPanel');
    if (!replayBodyEl) replayBodyEl = document.getElementById('replayBody');
    if (!replayHeaderEl) replayHeaderEl = document.getElementById('replayHeader');
    if (!replayToggleIconEl) replayToggleIconEl = document.getElementById('replayToggleIcon');
    if (replayPanelEl) {
      replayPanelEl.style.display = ENABLE_REPLAY_UPLOAD ? 'flex' : 'none';
    }
    if (!ENABLE_REPLAY_UPLOAD) return;
    if (replayHeaderEl && !replayHeaderEl.dataset.toggleBound) {
      replayHeaderEl.addEventListener('click', () => toggleReplayPanel());
      replayHeaderEl.dataset.toggleBound = 'true';
    }
  }




  function setReplayControlsDisabled(disabled) {
    if (!ENABLE_REPLAY_UPLOAD) {
      updateReplayRestartButtonState();
      return;
    }
    ensureReplayElements();
    const disable = !!disabled;
    if (replayFileInputEl) {
      replayFileInputEl.disabled = disable;
    }
    if (replayFileLabelEl) {
      replayFileLabelEl.style.opacity = disable ? '0.55' : '1';
      replayFileLabelEl.style.pointerEvents = disable ? 'none' : 'auto';
      const wrapper = replayFileLabelEl.parentElement;
      if (wrapper) wrapper.style.pointerEvents = disable ? 'none' : 'auto';
    }
    if (replayWatchBtnEl) {
      replayWatchBtnEl.disabled = disable || !pendingReplayPayload;
    }
    updateReplayRestartButtonState();
  }

  function clearReplaySelection() {
    ensureReplayElements();
    pendingReplayPayload = null;
    if (!ENABLE_REPLAY_UPLOAD) return;
    toggleReplayPanel(false);
    if (replayFileInputEl) replayFileInputEl.value = '';
    if (replayFileLabelEl) replayFileLabelEl.textContent = 'Choose replay file…';
    if (replayWatchBtnEl) replayWatchBtnEl.disabled = true;
  }

  function toggleReplayPanel(forceExpand) {
    if (!ENABLE_REPLAY_UPLOAD) return;
    ensureReplayElements();
    if (!replayBodyEl || !replayToggleIconEl) return;
    const expand = forceExpand !== undefined ? forceExpand : (replayBodyEl.style.display !== 'flex');
    replayBodyEl.style.display = expand ? 'flex' : 'none';
    replayToggleIconEl.textContent = expand ? '▲' : '▼';
  }

  function ensureReplaySpeedElements() {
    if (replayControlsWrapper) return;

    replayControlsWrapper = document.createElement('div');
    Object.assign(replayControlsWrapper.style, {
      position: 'absolute',
      right: '24px',
      bottom: '24px',
      display: 'none',
      flexDirection: 'row',
      alignItems: 'flex-end',
      gap: '12px',
      zIndex: 15,
    });

    replayRestartButton = document.createElement('button');
    replayRestartButton.type = 'button';
    replayRestartButton.id = 'replayRestartButton';
    replayRestartButton.className = 'replay-restart-button';
    replayRestartButton.textContent = '↻';
    replayRestartButton.setAttribute('aria-label', 'Restart Replay');
    replayRestartButton.title = 'Restart Replay';
    replayRestartButton.disabled = true;
    replayRestartButton.addEventListener('click', handleReplayRestartClick);
    replayRestartButton.addEventListener('animationend', () => {
      replayRestartButton.classList.remove('replay-restart-button--spin');
    });
    replayControlsWrapper.appendChild(replayRestartButton);

    replaySpeedContainer = document.createElement('div');
    Object.assign(replaySpeedContainer.style, {
      display: 'flex',
      flexDirection: 'column',
      gap: '6px',
      padding: '12px 14px',
      borderRadius: '12px',
      background: 'rgba(17, 17, 17, 0.85)',
      border: '1px solid rgba(255,255,255,0.15)',
      boxShadow: '0 10px 30px rgba(0,0,0,0.4)',
      minWidth: '160px',
      backdropFilter: 'blur(6px)',
    });

    const label = document.createElement('div');
    label.textContent = 'Replay Speed';
    Object.assign(label.style, {
      fontFamily: 'monospace',
      fontSize: '14px',
      letterSpacing: '1px',
      color: '#f0f0f0',
      textTransform: 'uppercase',
    });
    replaySpeedContainer.appendChild(label);

    replaySpeedLabel = document.createElement('div');
    Object.assign(replaySpeedLabel.style, {
      fontFamily: 'monospace',
      fontSize: '16px',
      color: '#7ee49c',
    });
    replaySpeedContainer.appendChild(replaySpeedLabel);

    replaySpeedInput = document.createElement('input');
    replaySpeedInput.type = 'range';
    replaySpeedInput.min = '0.5';
    replaySpeedInput.max = '3';
    replaySpeedInput.step = '0.25';
    replaySpeedInput.value = '1';
    replaySpeedInput.style.width = '100%';
    replaySpeedInput.style.cursor = 'pointer';
    replaySpeedContainer.appendChild(replaySpeedInput);

    replaySpeedInput.addEventListener('input', () => {
      replaySpeedValue = parseFloat(replaySpeedInput.value);
      updateReplaySpeedLabel();
    });
    replaySpeedInput.addEventListener('change', () => {
      replaySpeedValue = parseFloat(replaySpeedInput.value);
      updateReplaySpeedLabel();
      if (replayMode && replaySessionActive && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'setReplaySpeed', multiplier: replaySpeedValue }));
      }
    });

    replayControlsWrapper.appendChild(replaySpeedContainer);
    document.body.appendChild(replayControlsWrapper);
    updateReplaySpeedLabel();
    updateReplayRestartButtonState();
  }

  function updateReplaySpeedLabel() {
    if (!replaySpeedLabel) return;
    const clamped = Math.round((replaySpeedValue || 1) * 100) / 100;
    replaySpeedLabel.textContent = `${clamped.toFixed(2)}x`;
  }

  function updateReplayRestartButtonState() {
    ensureReplaySpeedElements();
    if (!replayRestartButton) return;
    const hasPayload = !!activeReplayPayload;
    const disabled = !hasPayload || replayStartPending || !!pendingReplayRestart;
    replayRestartButton.disabled = disabled;
    replayRestartButton.setAttribute('aria-disabled', disabled ? 'true' : 'false');
  }

  function updateReplaySpeedUI() {
    ensureReplaySpeedElements();
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (!replayControlsWrapper) return;
    if (replayMode && !menuVisible) {
      replayControlsWrapper.style.display = 'flex';
    } else {
      replayControlsWrapper.style.display = 'none';
    }
    updateReplayRestartButtonState();
  }

  function isReplayActive() {
    return replayMode || replayStartPending || replaySessionActive;
  }

  function stopReplaySession() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (!isReplayActive()) return;
    try {
      ws.send(JSON.stringify({ type: 'stopReplay' }));
    } catch (_) {
      // Ignore network errors; reconnection handler will reset state.
    }
  }

  function handleReplayRestartClick() {
    if (!replayRestartButton || replayRestartButton.disabled) return;
    if (!activeReplayPayload) {
      updateReplayRestartButtonState();
      return;
    }
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setReplayStatus('Cannot restart replay while disconnected.', 'warn');
      return;
    }

    replayRestartButton.classList.remove('replay-restart-button--spin');
    void replayRestartButton.offsetWidth; // restart animation
    replayRestartButton.classList.add('replay-restart-button--spin');

    const intent = reviewReplayActive ? 'review' : null;
    const payload = cloneReplayPayload(activeReplayPayload) || activeReplayPayload;

    pendingReplayRestart = null;
    if (isReplayActive()) {
      pendingReplayRestart = { payload, intent };
      updateReplayRestartButtonState();
      stopReplaySession();
      return;
    }

    startReplayFromPayload(payload, intent, true);
  }

  async function handleReplayFileSelect(event) {
    if (!ENABLE_REPLAY_UPLOAD) return;
    ensureReplayElements();
    toggleReplayPanel(true);
    if (isReplayActive()) {
      if (replayFileInputEl) replayFileInputEl.value = '';
      return;
    }

    const input = event?.target;
    const file = input && input.files ? input.files[0] : null;
    if (!file) {
      clearReplaySelection();
      setReplayStatus('', 'info');
      return;
    }

    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== 'object' || !parsed.graph || !Array.isArray(parsed.events)) {
        throw new Error('Replay missing graph or events');
      }
      pendingReplayPayload = parsed;
      if (replayFileLabelEl) replayFileLabelEl.textContent = file.name;
      if (replayWatchBtnEl) replayWatchBtnEl.disabled = false;
    } catch (err) {
      console.error('Failed to read replay file', err);
      clearReplaySelection();
    }
  }

  function startReplayFromSelection() {
    if (!ENABLE_REPLAY_UPLOAD) return;
    ensureReplayElements();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }
    if (!pendingReplayPayload) {
      return;
    }
    if (isReplayActive()) {
      return;
    }

    const menuEl = document.getElementById('menu');
    const inMenu = menuEl ? !menuEl.classList.contains('hidden') : true;
    if (!inMenu) {
      return;
    }

    pendingReplayRestart = null;
    startReplayFromPayload(pendingReplayPayload, null, true);
  }

  function startReplayFromPayload(replayPayload, intent, skipStop) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return false;
    }
    if (!replayPayload || typeof replayPayload !== 'object') {
      return false;
    }
    if (!skipStop && isReplayActive()) {
      stopReplaySession();
    }

    pendingReplayRestart = null;
    replayStartPending = true;
    replaySessionActive = false;
    reviewReplayActive = intent === 'review';
    activeReplayPayload = cloneReplayPayload(replayPayload) || replayPayload;
    setReplayControlsDisabled(true);
    ensureReplaySpeedElements();
    replaySpeedValue = 1;
    if (replaySpeedInput) replaySpeedInput.value = '1';
    updateReplaySpeedLabel();
    updateReplaySpeedUI();
    updateReviewReplayDownloadButton();
    updateReplayRestartButtonState();

    try {
      ws.send(JSON.stringify({ type: 'startReplay', replay: replayPayload }));
      return true;
    } catch (err) {
      console.error('Failed to start replay from payload', err);
      replayStartPending = false;
      reviewReplayActive = false;
      setReplayControlsDisabled(false);
      updateReplaySpeedUI();
      updateReplayRestartButtonState();
      return false;
    }
  }

  function handleReplayPlaybackError(msg) {
    const message = msg && typeof msg.message === 'string' ? msg.message : 'Replay failed.';
    if (isReplayActive()) {
      stopReplaySession();
    }
    replayMode = false;
    replayStartPending = false;
    replaySessionActive = false;
    setReplayControlsDisabled(false);
    updateReplaySpeedUI();
    setReplayStatus(message, 'error');
    clearReplaySelection();
    returnToMenu();
    reviewReplayActive = false;
    reviewReplayLastData = null;
    reviewReplayLastFilename = null;
    updateReviewReplayDownloadButton();
    pendingReplayRestart = null;
    activeReplayPayload = null;
    updateReplayRestartButtonState();
  }

  function handleReplayInit(msg) {
    replayMode = true;
    replaySessionActive = true;
    replayStartPending = false;
    setReplayControlsDisabled(true);

    const meta = msg && msg.replayMeta;
    if (meta && meta.gameId) {
      const shortId = String(meta.gameId).slice(0, 8);
      setReplayStatus(`Watching replay ${shortId}…`, 'info');
    } else {
      setReplayStatus('Replay running.', 'info');
    }

    handleInit(msg);
    updateQuitButtonLabel();
    if (replayMode) updateReplaySpeedUI();
    updateReviewReplayDownloadButton();
    updateReplayRestartButtonState();
  }

  function handleReplayMessage(msg) {
    switch (msg.type) {
      case 'replayStarting':
        replayStartPending = true;
        setReplayStatus('Preparing replay...', 'info');
        updateReplaySpeedUI();
        updateReplayRestartButtonState();
        return;
      case 'replayData':
        handleReplayDownload(msg);
        return;
      case 'replayStopped':
        {
          const restartRequest = pendingReplayRestart;
          pendingReplayRestart = null;
          if (restartRequest) {
            replayMode = false;
            replayStartPending = false;
            replaySessionActive = false;
            setReplayControlsDisabled(true);
            setReplayStatus('Restarting replay...', 'info');
            updateReplaySpeedUI();
            startReplayFromPayload(restartRequest.payload, restartRequest.intent, true);
            return;
          }
        replayMode = false;
        replayStartPending = false;
        replaySessionActive = false;
        setReplayControlsDisabled(false);
        setReplayStatus('Replay stopped.', 'warn');
        updateQuitButtonLabel();
        returnToMenu();
        updateReplaySpeedUI();
        reviewReplayActive = false;
        reviewReplayLastData = null;
        reviewReplayLastFilename = null;
        updateReviewReplayDownloadButton();
        activeReplayPayload = null;
        updateReplayRestartButtonState();
        }
        return;
      case 'replayComplete':
        replaySessionActive = false;
        setReplayStatus('Replay complete. Use Quit to return to menu.', 'success');
        updateReplaySpeedUI();
        updateQuitButtonLabel();
        updateReviewReplayDownloadButton();
        updateReplayRestartButtonState();
        return;
      case 'replayError':
        handleReplayPlaybackError(msg);
        return;
      case 'init':
        handleReplayInit(msg);
        return;
      default:
        break;
    }

    if (!replayMode && !replayStartPending) {
      return;
    }

    switch (msg.type) {
      case 'tick':
        handleTick(msg);
        break;
      case 'gameOver':
        handleGameOver(msg);
        break;
      case 'newEdge':
        handleNewEdge(msg);
        break;
      case 'edgeUpdated':
        handleEdgeUpdated(msg);
        break;
      case 'edgeReversed':
        handleEdgeReversed(msg);
        break;
      case 'nodeDestroyed':
        handleNodeDestroyed(msg);
        break;
      case 'nodeCaptured':
        handleNodeCaptured(msg);
        break;
      case 'removeEdges':
        handleRemoveEdges(msg);
        break;
      default:
        break;
    }
  }
  // Animation system for juice flow
  let animationTime = 0; // Global animation timer
  const JUICE_ANIMATION_SPEED = 4.0; // Speed of juice animation (higher = faster)
  const JUICE_ANIMATION_PHASES = 3; // Number of distinct color phases for juice animation
  
  function calculateNodeRadius(node, baseScale) {
    const juiceVal = Math.max(0, node.size ?? node.juice ?? 0);
    const minRadius = node.owner === null ? 0.2 : 0.3;
    const radius = Math.max(minRadius, 0.15 * Math.pow(juiceVal, 0.6));
    return radius * baseScale;
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

  function loadPersistentAutoAttack() {
    const saved = localStorage.getItem('autoAttack');
    persistentAutoAttack = saved === 'true';
    return persistentAutoAttack;
  }

  function savePersistentAutoAttack(value) {
    persistentAutoAttack = value;
    localStorage.setItem('autoAttack', value.toString());
  }

  // Settings panel persistence
  function loadPersistentSettingsOpen() {
    const saved = localStorage.getItem('settingsOpen');
    settingsOpen = saved === 'true';
    return settingsOpen;
  }
  function savePersistentSettingsOpen(value) {
    settingsOpen = !!value;
    localStorage.setItem('settingsOpen', settingsOpen.toString());
  }

  // Numbers persistence functions
  function loadPersistentNumbers() {
    const saved = localStorage.getItem('numbers');
    persistentNumbers = saved !== 'false'; // Default to true if not set
    return persistentNumbers;
  }

  function savePersistentNumbers(value) {
    persistentNumbers = value;
    localStorage.setItem('numbers', value.toString());
  }

  // Edge flow persistence functions
  function loadPersistentEdgeFlow() {
    const saved = localStorage.getItem('edgeFlow');
    persistentEdgeFlow = saved !== 'false'; // Default to true if not set
    return persistentEdgeFlow;
  }

  function savePersistentEdgeFlow(value) {
    persistentEdgeFlow = value;
    localStorage.setItem('edgeFlow', value.toString());
  }

  function loadPersistentPreMove() {
    const saved = localStorage.getItem('preMovePipes');
    persistentPreMove = saved === 'true';
    return persistentPreMove;
  }

  function savePersistentPreMove(value) {
    persistentPreMove = !!value;
    localStorage.setItem('preMovePipes', persistentPreMove.toString());
  }

  // Helper: convert 0xRRGGBB -> "#rrggbb"
  function toCssColor(hex) {
    if (typeof hex === 'string') return hex;
    return '#' + (hex >>> 0).toString(16).padStart(6, '0');
  }

  function isWarpFrontendActive() {
    return (isWarpLike(gameMode) || isWarpLike(selectedMode)) && warpBoundsWorld && Number.isFinite(warpBoundsWorld.width) && Number.isFinite(warpBoundsWorld.height);
  }

  function makeEndpoint(x, y, node = null, portalAxis = null) {
    return { x, y, node: node || null, portalAxis };
  }

  function getActiveWarpPreference() {
    if (!isWarpWrapUnlocked()) {
      return { allowWarp: false, preferredWrapAxis: null, preferredWrapDirection: null };
    }
    if (!warpWrapUsed) {
      return { allowWarp: false, preferredWrapAxis: null, preferredWrapDirection: null };
    }
    const axis = (lastWarpAxis === 'horizontal' || lastWarpAxis === 'vertical') ? lastWarpAxis : null;
    const permissions = getWarpAxisPermissionsForGameplay();
    const axisAllowed = axis === 'horizontal'
      ? permissions.horizontal
      : axis === 'vertical'
        ? permissions.vertical
        : false;
    if (!axis || !axisAllowed) {
      return { allowWarp: false, preferredWrapAxis: null, preferredWrapDirection: null };
    }
    const direction = typeof lastWarpDirection === 'string' ? lastWarpDirection : null;
    return {
      allowWarp: true,
      preferredWrapAxis: axis,
      preferredWrapDirection: direction,
    };
  }

  function computeWarpBridgeSegments(fromNode, toNode, options = {}) {
    if (!fromNode || !toNode) {
      return null;
    }

    const allowWarp = options && Object.prototype.hasOwnProperty.call(options, 'allowWarp')
      ? !!options.allowWarp
      : true;
    const preferredAxis = options && typeof options.preferredWrapAxis === 'string' ? options.preferredWrapAxis : null;
    const forcedAxis = allowWarp && (preferredAxis === 'horizontal' || preferredAxis === 'vertical') ? preferredAxis : null;
    const preferredDirection = options && typeof options.preferredWrapDirection === 'string' ? options.preferredWrapDirection : null;
    const forcedDirection = forcedAxis ? preferredDirection : null;
    const permissions = getWarpAxisPermissionsForGameplay();
    const horizontalAllowed = permissions.horizontal;
    const verticalAllowed = permissions.vertical;

    const baseSegment = {
      wrapAxis: 'none',
      segments: [
        {
          start: makeEndpoint(fromNode.x, fromNode.y, fromNode),
          end: makeEndpoint(toNode.x, toNode.y, toNode),
        },
      ],
      totalWorldDistance: Math.hypot(toNode.x - fromNode.x, toNode.y - fromNode.y),
    };

    const baseCopy = {
      wrapAxis: 'none',
      segments: baseSegment.segments.map((seg) => ({
        start: { ...seg.start },
        end: { ...seg.end },
      })),
      totalWorldDistance: baseSegment.totalWorldDistance,
    };

    if (!isWarpFrontendActive()) {
      return baseCopy;
    }

    const width = warpBoundsWorld.width;
    const height = warpBoundsWorld.height;
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
      return baseCopy;
    }

    const dx = toNode.x - fromNode.x;
    const dy = toNode.y - fromNode.y;

    let best = baseCopy;

    const EPS = 1e-6;

    if (!allowWarp || !isWarpWrapUnlocked()) {
      return best;
    }

    const forceHorizontal = horizontalAllowed && forcedAxis === 'horizontal';
    const forceVertical = verticalAllowed && forcedAxis === 'vertical';
    const allowHorizontalCandidate = horizontalAllowed && !forceVertical;
    const allowVerticalCandidate = verticalAllowed && !forceHorizontal;

    // Horizontal wrap candidate (left/right)
    if (forceHorizontal || (allowHorizontalCandidate && Math.abs(dx) > width / 2 + EPS)) {
      const adjust = forceHorizontal
        ? (forcedDirection === 'leftToRight' ? -width
          : forcedDirection === 'rightToLeft' ? width
          : (dx > 0 ? -width : width))
        : (dx > 0 ? -width : width);
      const adjustedTargetX = toNode.x + adjust;
      const dxWrap = adjustedTargetX - fromNode.x;
      if (Math.abs(dxWrap) > EPS) {
        const boundaryX = dxWrap > 0 ? warpBoundsWorld.maxX : warpBoundsWorld.minX;
        const t = (boundaryX - fromNode.x) / dxWrap;
        if (t > EPS && t < 1 - EPS) {
          const exitY = fromNode.y + t * dy;
          if (exitY >= warpBoundsWorld.minY - EPS && exitY <= warpBoundsWorld.maxY + EPS) {
            const entryX = dxWrap > 0 ? warpBoundsWorld.minX : warpBoundsWorld.maxX;
            const dist1 = Math.hypot(boundaryX - fromNode.x, exitY - fromNode.y);
            const dist2 = Math.hypot(toNode.x - entryX, toNode.y - exitY);
            const total = dist1 + dist2;
            if (forceHorizontal || total + EPS < best.totalWorldDistance || best.totalWorldDistance < EPS) {
              best = {
                wrapAxis: 'horizontal',
                segments: [
                  {
                    start: makeEndpoint(fromNode.x, fromNode.y, fromNode),
                    end: makeEndpoint(boundaryX, exitY, null, 'horizontal'),
                  },
                  {
                    start: makeEndpoint(entryX, exitY, null, 'horizontal'),
                    end: makeEndpoint(toNode.x, toNode.y, toNode),
                  },
                ],
                totalWorldDistance: total,
              };
            }
          }
        }
      }
    }

    // Vertical wrap candidate (top/bottom)
    if (forceVertical || (allowVerticalCandidate && Math.abs(dy) > height / 2 + EPS)) {
      const adjust = forceVertical
        ? (forcedDirection === 'topToBottom' ? -height
          : forcedDirection === 'bottomToTop' ? height
          : (dy > 0 ? -height : height))
        : (dy > 0 ? -height : height);
      const adjustedTargetY = toNode.y + adjust;
      const dyWrap = adjustedTargetY - fromNode.y;
      if (Math.abs(dyWrap) > EPS) {
        const boundaryY = dyWrap > 0 ? warpBoundsWorld.maxY : warpBoundsWorld.minY;
        const t = (boundaryY - fromNode.y) / dyWrap;
        if (t > EPS && t < 1 - EPS) {
          const exitX = fromNode.x + t * dx;
          if (exitX >= warpBoundsWorld.minX - EPS && exitX <= warpBoundsWorld.maxX + EPS) {
            const entryY = dyWrap > 0 ? warpBoundsWorld.minY : warpBoundsWorld.maxY;
            const dist1 = Math.hypot(exitX - fromNode.x, boundaryY - fromNode.y);
            const dist2 = Math.hypot(toNode.x - exitX, toNode.y - entryY);
            const total = dist1 + dist2;
            if (forceVertical || total + EPS < best.totalWorldDistance || best.totalWorldDistance < EPS) {
              best = {
                wrapAxis: 'vertical',
                segments: [
                  {
                    start: makeEndpoint(fromNode.x, fromNode.y, fromNode),
                    end: makeEndpoint(exitX, boundaryY, null, 'vertical'),
                  },
                  {
                    start: makeEndpoint(exitX, entryY, null, 'vertical'),
                    end: makeEndpoint(toNode.x, toNode.y, toNode),
                  },
                ],
                totalWorldDistance: total,
              };
            }
          }
        }
      }
    }

    return best;
  }

  // Bridge cost calculation
  function calculateBridgeCost(fromNode, toNode, isBrass = false, options = {}) {
    if (!fromNode || !toNode) return 0;

    let allowWarp = true;
    let preferredWrapAxis = null;
    let preferredWrapDirection = null;
    if (options && typeof options === 'object') {
      if (Object.prototype.hasOwnProperty.call(options, 'allowWarp')) {
        allowWarp = !!options.allowWarp;
      }
      if (typeof options.preferredWrapAxis === 'string') {
        preferredWrapAxis = options.preferredWrapAxis;
      }
      if (typeof options.preferredWrapDirection === 'string') {
        preferredWrapDirection = options.preferredWrapDirection;
      }
    }

    const baseWidth = screen && Number.isFinite(screen.width) ? screen.width : 275.0;
    const baseHeight = screen && Number.isFinite(screen.height) ? screen.height : 108.0;
    const largestSpan = Math.max(1, baseWidth, baseHeight);
    const scale = 100 / largestSpan;

    const path = computeWarpBridgeSegments(fromNode, toNode, { allowWarp, preferredWrapAxis, preferredWrapDirection });
    let normalizedDistance = 0;

    if (path && Array.isArray(path.segments) && path.segments.length) {
      for (const segment of path.segments) {
        const segDx = (segment.end.x - segment.start.x) * scale;
        const segDy = (segment.end.y - segment.start.y) * scale;
        normalizedDistance += Math.hypot(segDx, segDy);
      }
    } else {
      const dx = (toNode.x - fromNode.x) * scale;
      const dy = (toNode.y - fromNode.y) * scale;
      normalizedDistance = Math.hypot(dx, dy);
    }

    if (normalizedDistance === 0) return 0;

    const baseCost = Math.round(BRIDGE_BASE_COST + normalizedDistance * BRIDGE_COST_PER_UNIT);
    const doubleCost = isBrass && brassPipesDoubleCost();
    return doubleCost ? baseCost * 2 : baseCost;
  }

  function normalizeWarpSegmentList(rawSegments, sourceNode, targetNode) {
    const segments = [];
    if (Array.isArray(rawSegments)) {
      rawSegments.forEach((seg) => {
        let sx;
        let sy;
        let ex;
        let ey;
        if (Array.isArray(seg) && seg.length >= 4) {
          [sx, sy, ex, ey] = seg.map(Number);
        } else if (seg && typeof seg === 'object') {
          const start = seg.start || seg.from;
          const end = seg.end || seg.to;
          if (start && typeof start === 'object' && end && typeof end === 'object') {
            sx = Number(start.x);
            sy = Number(start.y);
            ex = Number(end.x);
            ey = Number(end.y);
          } else {
            sx = Number(seg.sx ?? seg.x1 ?? seg.xStart ?? seg.x0);
            sy = Number(seg.sy ?? seg.y1 ?? seg.yStart ?? seg.y0);
            ex = Number(seg.ex ?? seg.x2 ?? seg.xEnd ?? seg.x1);
            ey = Number(seg.ey ?? seg.y2 ?? seg.yEnd ?? seg.y1);
          }
        }
        if ([sx, sy, ex, ey].every((value) => Number.isFinite(value))) {
          segments.push({ sx, sy, ex, ey });
        }
      });
    }
    if (!segments.length && sourceNode && targetNode) {
      segments.push({ sx: sourceNode.x, sy: sourceNode.y, ex: targetNode.x, ey: targetNode.y });
    }
    return segments;
  }

  function normalizeEdgeWarpPayload(payload, sourceNode, targetNode) {
    let axis = 'none';
    if (payload && typeof payload.axis === 'string') {
      axis = payload.axis;
    } else if (payload && typeof payload.wrapAxis === 'string') {
      axis = payload.wrapAxis;
    } else if (payload && typeof payload.warpAxis === 'string') {
      axis = payload.warpAxis;
    }
    if (axis !== 'horizontal' && axis !== 'vertical') {
      axis = 'none';
    }
    let rawSegments = payload && payload.segments;
    if (!rawSegments && payload) {
      rawSegments = payload.warpSegments;
    }
    const segments = normalizeWarpSegmentList(rawSegments, sourceNode, targetNode);
    return { axis, segments };
  }

  function buildWarpInfoForBridge(fromNode, toNode, options = {}) {
    let allowWarp = true;
    let preferredWrapAxis = null;
    let preferredWrapDirection = null;
    if (options && typeof options === 'object') {
      if (Object.prototype.hasOwnProperty.call(options, 'allowWarp')) {
        allowWarp = !!options.allowWarp;
      }
      if (typeof options.preferredWrapAxis === 'string') {
        preferredWrapAxis = options.preferredWrapAxis;
      }
      if (typeof options.preferredWrapDirection === 'string') {
        preferredWrapDirection = options.preferredWrapDirection;
      }
    }

    const path = computeWarpBridgeSegments(fromNode, toNode, { allowWarp, preferredWrapAxis, preferredWrapDirection });
    let axis = 'none';
    let segments = [];
    if (path && typeof path.wrapAxis === 'string') {
      axis = path.wrapAxis;
    }
    if (path && Array.isArray(path.segments)) {
      segments = path.segments
        .map((segment) => {
          const start = segment && segment.start;
          const end = segment && segment.end;
          if (!start || !end) return null;
          const sx = Number(start.x);
          const sy = Number(start.y);
          const ex = Number(end.x);
          const ey = Number(end.y);
          if ([sx, sy, ex, ey].every((value) => Number.isFinite(value))) {
            return [sx, sy, ex, ey];
          }
          return null;
        })
        .filter(Boolean);
    }
    if (!segments.length) {
      segments = [[fromNode.x, fromNode.y, toNode.x, toNode.y]];
    }
    return { axis, segments };
  }

  function getEdgeWarpSegments(edge) {
    if (!edge) return [];
    if (edge && Array.isArray(edge.warpSegments) && edge.warpSegments.length) {
      const result = edge.warpSegments.map((seg) => ({
        sx: Number(seg.sx),
        sy: Number(seg.sy),
        ex: Number(seg.ex),
        ey: Number(seg.ey),
      })).filter((seg) => [seg.sx, seg.sy, seg.ex, seg.ey].every((value) => Number.isFinite(value)));

      if (result.length) {
        const sourceNode = nodes.get(edge.source);
        const targetNode = nodes.get(edge.target);
        if (sourceNode) {
          result[0] = {
            ...result[0],
            sx: Number(sourceNode.x),
            sy: Number(sourceNode.y),
          };
        }
        if (targetNode) {
          const lastIndex = result.length - 1;
          result[lastIndex] = {
            ...result[lastIndex],
            ex: Number(targetNode.x),
            ey: Number(targetNode.y),
          };
        }
      }

      return result;
    }
    const sourceNode = edge ? nodes.get(edge.source) : null;
    const targetNode = edge ? nodes.get(edge.target) : null;
    if (sourceNode && targetNode) {
      return [{ sx: sourceNode.x, sy: sourceNode.y, ex: targetNode.x, ey: targetNode.y }];
    }
    return [];
  }

  function toEdgeId(value) {
    if (value == null) return null;
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function getEdgeMidpointWorld(edge) {
    const segments = getEdgeWarpSegments(edge);
    if (!segments.length) return null;
    const lengths = segments.map((seg) => Math.hypot(seg.ex - seg.sx, seg.ey - seg.sy));
    const totalLength = lengths.reduce((sum, len) => sum + len, 0);
    if (totalLength <= 0) {
      const last = segments[segments.length - 1];
      return { x: last.ex, y: last.ey };
    }
    let remaining = totalLength / 2;
    for (let i = 0; i < segments.length; i++) {
      const len = lengths[i];
      if (len <= 0) continue;
      if (remaining <= len) {
        const seg = segments[i];
        const t = remaining / len;
        return {
          x: seg.sx + (seg.ex - seg.sx) * t,
          y: seg.sy + (seg.ey - seg.sy) * t,
        };
      }
      remaining -= len;
    }
    const last = segments[segments.length - 1];
    return { x: last.ex, y: last.ey };
  }

  function applyEdgeWarpData(edgeRecord, warpPayload, sourceNode, targetNode) {
    const { axis, segments } = normalizeEdgeWarpPayload(warpPayload, sourceNode, targetNode);
    edgeRecord.warpAxis = axis;
    edgeRecord.warpSegments = segments;
  }

  function buildEdgeScreenPath(edge, fromNode, toNode, fromRadius, toRadius) {
    const worldSegments = getEdgeWarpSegments(edge);
    if (!worldSegments.length) return [];

    const adjustedSegments = worldSegments.map((seg) => ({ ...seg }));
    if (fromNode && adjustedSegments.length > 0) {
      adjustedSegments[0].sx = fromNode.x;
      adjustedSegments[0].sy = fromNode.y;
    }
    if (toNode && adjustedSegments.length > 0) {
      const last = adjustedSegments[adjustedSegments.length - 1];
      last.ex = toNode.x;
      last.ey = toNode.y;
    }

    const screenSegments = adjustedSegments.map((seg) => {
      const [sx, sy] = worldToScreen(seg.sx, seg.sy);
      const [ex, ey] = worldToScreen(seg.ex, seg.ey);
      return { sx, sy, ex, ey };
    });

    if (!screenSegments.length) return [];

    const first = screenSegments[0];
    if (first) {
      const dx = first.ex - first.sx;
      const dy = first.ey - first.sy;
      const len = Math.hypot(dx, dy);
      if (len > 1e-6 && Number.isFinite(fromRadius)) {
        const maxTrim = Math.max(0, len - 0.5);
        const trim = Math.min(Math.max(fromRadius, 0), maxTrim);
        const ux = dx / len;
        const uy = dy / len;
        first.sx += ux * trim;
        first.sy += uy * trim;
      }
    }

    const last = screenSegments[screenSegments.length - 1];
    if (last) {
      const dx = last.ex - last.sx;
      const dy = last.ey - last.sy;
      const len = Math.hypot(dx, dy);
      if (len > 1e-6 && Number.isFinite(toRadius)) {
        const maxTrim = Math.max(0, len - 0.5);
        const trim = Math.min(Math.max(toRadius, 0), maxTrim);
        const ux = dx / len;
        const uy = dy / len;
        last.ex -= ux * trim;
        last.ey -= uy * trim;
      }
    }

    const path = [];
    for (const seg of screenSegments) {
      const dx = seg.ex - seg.sx;
      const dy = seg.ey - seg.sy;
      const len = Math.hypot(dx, dy);
      if (len <= 1e-6) continue;
      const ux = dx / len;
      const uy = dy / len;
      path.push({
        sx: seg.sx,
        sy: seg.sy,
        ex: seg.ex,
        ey: seg.ey,
        length: len,
        ux,
        uy,
        angle: Math.atan2(uy, ux),
      });
    }

    return path;
  }

  function buildEdgeWorldPath(edge, fromNode, toNode, fromRadiusWorld, toRadiusWorld) {
    const worldSegments = getEdgeWarpSegments(edge);
    if (!worldSegments.length) return [];

    const path = [];
    for (const seg of worldSegments) {
      const sx = Number(seg.sx);
      const sy = Number(seg.sy);
      const ex = Number(seg.ex);
      const ey = Number(seg.ey);
      if (![sx, sy, ex, ey].every((value) => Number.isFinite(value))) continue;
      const dx = ex - sx;
      const dy = ey - sy;
      const length = Math.hypot(dx, dy);
      if (length <= 1e-6) continue;
      const ux = dx / length;
      const uy = dy / length;
      path.push({ sx, sy, ex, ey, length, ux, uy, angle: Math.atan2(uy, ux) });
    }

    if (!path.length) return [];

    trimPathStart(path, fromRadiusWorld);
    trimPathEnd(path, toRadiusWorld);

    return path.filter((seg) => seg.length > 1e-6);
  }

  function trimPathStart(path, distance) {
    let remaining = Math.max(0, Number(distance) || 0);
    for (let i = 0; i < path.length && remaining > 0; ) {
      const seg = path[i];
      if (!seg || seg.length <= 0) {
        path.splice(i, 1);
        continue;
      }
      if (seg.length <= remaining + 1e-6) {
        remaining -= seg.length;
        path.splice(i, 1);
        continue;
      }
      seg.sx += seg.ux * remaining;
      seg.sy += seg.uy * remaining;
      seg.length -= remaining;
      remaining = 0;
      i += 1;
    }
  }

  function trimPathEnd(path, distance) {
    let remaining = Math.max(0, Number(distance) || 0);
    for (let i = path.length - 1; i >= 0 && remaining > 0; i--) {
      const seg = path[i];
      if (!seg || seg.length <= 0) {
        path.splice(i, 1);
        continue;
      }
      if (seg.length <= remaining + 1e-6) {
        remaining -= seg.length;
        path.splice(i, 1);
        continue;
      }
      seg.ex -= seg.ux * remaining;
      seg.ey -= seg.uy * remaining;
      seg.length -= remaining;
      remaining = 0;
    }
  }

  function samplePointOnPath(path, distance) {
    if (!Array.isArray(path) || !path.length) return null;
    let remaining = Math.max(0, Number(distance) || 0);
    for (let i = 0; i < path.length; i++) {
      const seg = path[i];
      if (!seg || seg.length <= 0) continue;
      if (remaining <= seg.length || i === path.length - 1) {
        const clamped = Math.min(seg.length, Math.max(0, remaining));
        const x = seg.sx + seg.ux * clamped;
        const y = seg.sy + seg.uy * clamped;
        return { x, y, angle: seg.angle, seg };
      }
      remaining -= seg.length;
    }
    const last = path[path.length - 1];
    return last ? { x: last.ex, y: last.ey, angle: last.angle, seg: last } : null;
  }

  function randomBetween(min, max) {
    const a = Number(min);
    const b = Number(max);
    if (!Number.isFinite(a) && !Number.isFinite(b)) return 0;
    if (!Number.isFinite(b)) return a;
    if (!Number.isFinite(a)) return b;
    if (b <= a) return a;
    return a + Math.random() * (b - a);
  }

  function formatCost(value) {
    if (!Number.isFinite(value)) return '0';
    const rounded = Math.round(value);
    return rounded.toString();
  }

  function normalizeMode(value) {
    const fallback = IS_LEGACY_CLIENT ? (LEGACY_DEFAULT_MODE || 'basic') : 'sparse';
    if (typeof value !== 'string') return fallback;
    let lowered = value.trim().toLowerCase();
    if (lowered === 'passive') {
      lowered = IS_LEGACY_CLIENT ? 'basic' : 'sparse';
    } else if (lowered === 'basic' && !IS_LEGACY_CLIENT) {
      lowered = 'sparse';
    } else if (lowered === 'pop') {
      lowered = 'warp-old';
    } else if (lowered === 'xb') {
      lowered = 'warp';
    }
    return Object.prototype.hasOwnProperty.call(MODE_LABELS, lowered) ? lowered : fallback;
  }

  function isRingModeActive() {
    const mode = normalizeMode(gameMode);
    return ['overflow', 'go', 'warp', 'i-warp', 'semi', 'i-semi', 'flat', 'i-flat'].includes(mode);
  }

  function isNukeModeActive() {
    return normalizeMode(gameMode) === 'nuke';
  }

  function isSandboxModeActive() {
    return normalizeMode(gameMode) === 'sandbox';
  }

  function sandboxModeEnabled() {
    return IS_SANDBOX_CLIENT || isSandboxModeActive();
  }

  function isBrassModeActive() {
    return normalizeMode(gameMode) === 'brass-old';
  }

  function isCrossModeActive() {
    return normalizeMode(gameMode) === 'cross';
  }

  function isIntentionalBrassMode(mode) {
    const normalized = normalizeMode(mode);
    return normalized === 'cross' || normalized === 'i-warp' || normalized === 'i-flat' || normalized === 'i-semi';
  }

  function isIntentionalBrassModeActive() {
    return isIntentionalBrassMode(gameMode);
  }

  function isCrossLikeModeActive() {
    const mode = normalizeMode(gameMode);
    return mode === 'cross' || mode === 'brass-old' || mode === 'warp' || mode === 'i-warp' || mode === 'semi' || mode === 'i-semi' || mode === 'flat' || mode === 'i-flat';
  }

  function isTrueCrossModeActive() {
    return normalizeMode(gameMode) === 'cross';
  }

  function isNukeLikeModeActive() {
    const mode = normalizeMode(gameMode);
    return ['nuke', 'cross', 'brass-old', 'warp', 'i-warp', 'semi', 'i-semi', 'flat', 'i-flat'].includes(mode);
  }

  function isXbModeActive() {
    const mode = normalizeMode(gameMode);
    return mode === 'warp' || mode === 'semi' || mode === 'flat';
  }

  function brassPipesDoubleCost() {
    if (isMagicResourceModeActive()) return false;
    if (isIntentionalBrassMode(gameMode)) return true;
    return isIntentionalBrassMode(selectedMode);
  }

  function formatModeText(mode) {
    return MODE_LABELS[normalizeMode(mode)] || MODE_LABELS.sparse;
  }

  function updatePlayBotAvailability(baseEnabled = true) {
    if (!playBotBtnEl) return;
    const normalized = normalizeMode(selectedMode);
    const modeAllowsBot = IS_LEGACY_CLIENT
      ? true
      : (['flat', 'i-flat'].includes(normalized) || isWarpLike(selectedMode));
    const enabled = Boolean(baseEnabled && modeAllowsBot);
    playBotBtnEl.disabled = !enabled;
    playBotBtnEl.title = modeAllowsBot ? '' : 'Bots are unavailable in this mode';
  }

  function setSelectedMode(nextMode, options = {}) {
    const normalized = normalizeMode(nextMode);
    selectedMode = normalized;
    updatePlayBotAvailability(true);
    return selectedMode;
  }

// Replace the whole function with this Phaser version:
function updateBridgeCostDisplay(fromNode, toNode, isBrass = false) {
  if (!sceneRef || !fromNode || !toNode) return;

  const warpPreference = (activeAbility === 'bridge1way' && bridgeFirstNode != null)
    ? getActiveWarpPreference()
    : { allowWarp: true, preferredWrapAxis: null, preferredWrapDirection: null };
  const path = computeWarpBridgeSegments(fromNode, toNode, warpPreference);
  let anchorX = (fromNode.x + toNode.x) / 2;
  let anchorY = (fromNode.y + toNode.y) / 2;

  if (path && Number.isFinite(path.totalWorldDistance) && path.totalWorldDistance > 0 && Array.isArray(path.segments)) {
    if (path.wrapAxis && path.wrapAxis !== 'none' && path.segments.length >= 2) {
      const postWrap = path.segments[path.segments.length - 1];
      const segDx = postWrap.end.x - postWrap.start.x;
      const segDy = postWrap.end.y - postWrap.start.y;
      anchorX = postWrap.start.x + segDx * 0.5;
      anchorY = postWrap.start.y + segDy * 0.5;
    } else {
      let halfDistance = path.totalWorldDistance / 2;
      for (const segment of path.segments) {
        const segDx = segment.end.x - segment.start.x;
        const segDy = segment.end.y - segment.start.y;
        const segLength = Math.hypot(segDx, segDy);
        if (segLength >= halfDistance) {
          const t = segLength > 0 ? halfDistance / segLength : 0;
          anchorX = segment.start.x + segDx * t;
          anchorY = segment.start.y + segDy * t;
          break;
        }
        halfDistance -= segLength;
      }
    }
  }

  const [sx, sy] = worldToScreen(anchorX, anchorY);

  const cost = calculateBridgeCost(fromNode, toNode, isBrass, warpPreference);
  const canAfford = goldValue >= cost;
  const text = `$${formatCost(cost)}`;
  const textColor = canAfford ? MONEY_SPEND_COLOR : '#222222';
  const strokeColor = canAfford ? MONEY_SPEND_STROKE : 'rgba(255,255,255,0.85)';

  if (!bridgeCostDisplay) {
    bridgeCostDisplay = sceneRef.add.text(sx, sy - 20, text, {
      fontFamily: 'monospace',
      fontSize: '20px',
      fontStyle: 'normal',
      color: textColor,
      stroke: strokeColor,
      strokeThickness: canAfford ? 1 : 0,
    })
    .setOrigin(0.5, 0.5)
    .setDepth(1000);
  } else {
    bridgeCostDisplay.setText(text);
    bridgeCostDisplay.setPosition(sx, sy - 20);
    bridgeCostDisplay.setColor(textColor);
    bridgeCostDisplay.setStroke(strokeColor, canAfford ? 1 : 0);
    bridgeCostDisplay.setVisible(true);
  }
}

function hideBridgeCostDisplay() {
  if (bridgeCostDisplay) {
    bridgeCostDisplay.destroy();
    bridgeCostDisplay = null;
  }
}


function clearBridgeSelection() {
  bridgeFirstNode = null;
  bridgeIsBrass = false;
  brassPreviewIntersections.clear();
  bridgePreviewWillBeBrass = false;
  xbPreviewBlockedByBrass = false;
  warpWrapUsed = false;
  lastDoubleWarpWarningTime = 0;
  lastWarpAxis = null;
  lastWarpDirection = null;
}


  // Money indicator functions
  // Create an animated text indicator that rises & fades out
  function createMoneyIndicator(x, y, text, color, duration = 2000, options = {}) {
    const cssColor = toCssColor(color);
    const strokeColor = options.strokeColor || (cssColor === MONEY_SPEND_COLOR ? MONEY_SPEND_STROKE : '#423200');
    const strokeThickness = Number.isFinite(options.strokeThickness) ? options.strokeThickness : 1;

    const indicator = {
      x,
      y,
      text,
      color: cssColor,
      startTime: Date.now(),
      duration,
      textObj: null,
      floatDistance: Number.isFinite(options.floatDistance) ? options.floatDistance : 30,
      worldOffset: Number.isFinite(options.worldOffset) ? options.worldOffset : 0,
      strokeColor,
      strokeThickness,
    };

    const [sx, sy] = worldToScreen(x, y + indicator.worldOffset);
    if (sceneRef) {
      indicator.textObj = sceneRef.add.text(sx, sy, text, {
        fontFamily: 'monospace',
        fontSize: '20px',
        fontStyle: 'normal',
        color: indicator.color,
        stroke: indicator.strokeColor,
        strokeThickness: indicator.strokeThickness
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
      const offsetY = progress * (indicator.floatDistance ?? 30);
      const [sx, sy] = worldToScreen(indicator.x, indicator.y + indicator.worldOffset - offsetY);

      if (indicator.textObj) {
        indicator.textObj.setPosition(sx, sy);
        indicator.textObj.setAlpha(alpha);
        indicator.textObj.setVisible(true);
      }
      return true;
    });
  }

  function removeReverseButton(edgeId) {
    const entry = reversePipeButtons.get(edgeId);
    if (!entry) return;
    const { text } = entry;
    if (text) {
      try {
        text.removeAllListeners?.();
        text.destroy();
      } catch (err) {
        text.setVisible(false);
      }
    }
    reversePipeButtons.delete(edgeId);
  }

  function getReverseButtonScreenPosition(edgeRecord, midpointScreenX, midpointScreenY) {
    if (!edgeRecord) {
      return { x: midpointScreenX, y: midpointScreenY };
    }

    const normal = getEdgeScreenNormalAtMidpoint(edgeRecord);
    if (!normal) {
      return { x: midpointScreenX, y: midpointScreenY };
    }

    const offset = Number.isFinite(REVERSE_BUTTON_OFFSET_PX) ? REVERSE_BUTTON_OFFSET_PX : 0;
    if (offset <= 0) {
      return { x: midpointScreenX, y: midpointScreenY };
    }

    return {
      x: midpointScreenX + normal.x * offset,
      y: midpointScreenY + normal.y * offset,
    };
  }

  function getEdgeScreenNormalAtMidpoint(edgeRecord) {
    if (!edgeRecord) return null;

    let segments = getEdgeWarpSegments(edgeRecord);
    if (!segments || !segments.length) {
      const sourceNode = nodes.get(edgeRecord.source);
      const targetNode = nodes.get(edgeRecord.target);
      if (!sourceNode || !targetNode) return null;
      segments = [{
        sx: Number(sourceNode.x),
        sy: Number(sourceNode.y),
        ex: Number(targetNode.x),
        ey: Number(targetNode.y),
      }];
    }

    const validSegments = segments
      .map((seg) => {
        const sx = Number(seg.sx);
        const sy = Number(seg.sy);
        const ex = Number(seg.ex);
        const ey = Number(seg.ey);
        const length = Math.hypot(ex - sx, ey - sy);
        return Number.isFinite(length) && length > 0.0001 ? { sx, sy, ex, ey, length } : null;
      })
      .filter(Boolean);

    if (!validSegments.length) return null;

    const totalLength = validSegments.reduce((sum, seg) => sum + seg.length, 0);
    if (!Number.isFinite(totalLength) || totalLength <= 0) return null;

    const targetDistance = totalLength / 2;
    let traversed = 0;
    let chosenSegment = validSegments[validSegments.length - 1];

    for (const seg of validSegments) {
      if (traversed + seg.length >= targetDistance) {
        chosenSegment = seg;
        break;
      }
      traversed += seg.length;
    }

    const [screenSx, screenSy] = worldToScreen(chosenSegment.sx, chosenSegment.sy);
    const [screenEx, screenEy] = worldToScreen(chosenSegment.ex, chosenSegment.ey);
    const dx = screenEx - screenSx;
    const dy = screenEy - screenSy;
    const screenLength = Math.hypot(dx, dy);
    if (!Number.isFinite(screenLength) || screenLength <= 0.0001) return null;

    let nx = -dy / screenLength;
    let ny = dx / screenLength;

    if (ny > 0) {
      nx = -nx;
      ny = -ny;
    }

    return { x: nx, y: ny };
  }

  function updateReverseButton(edgeId, edgeRecord, screenX, screenY) {
    if (!sceneRef) return;
    if (!edgeRecord || edgeRecord.pipeType !== 'reverse') {
      removeReverseButton(edgeId);
      return;
    }

    const targetPosition = getReverseButtonScreenPosition(edgeRecord, screenX, screenY);
    const symbolX = Number.isFinite(targetPosition.x) ? targetPosition.x : screenX;
    const symbolY = Number.isFinite(targetPosition.y) ? targetPosition.y : screenY;

    let entry = reversePipeButtons.get(edgeId);
    if (!entry) {
      const text = sceneRef.add.text(symbolX, symbolY, '⟳', {
        fontFamily: 'sans-serif',
        fontSize: '28px',
        fontStyle: 'bold',
        color: '#f4f4f4',
        stroke: '#000000',
        strokeThickness: 4,
      });
      text.setOrigin(0.5, 0.5);
      text.setDepth(6);
      text.setAlpha(0.9);
      text.setData('edgeId', edgeId);
      entry = { text, pulseStart: null };
      reversePipeButtons.set(edgeId, entry);
    }

    const { text } = entry;
    const canControl = canReverseEdge(edgeRecord) && !edgeRecord.building;
    const sourceNode = nodes.get(edgeRecord.source);
    const ownerColor = ownerToHexColor(sourceNode ? sourceNode.owner : null);

    text.setPosition(symbolX, symbolY);
    text.setColor(ownerColor);
    text.setAlpha(canControl ? 1 : 0.5);
    text.setVisible(true);

    applyReverseButtonPulse(entry, text);
  }

  function applyReverseButtonPulse(entry, text) {
    if (!entry || !text) return;
    const start = entry.pulseStart;
    if (start == null) {
      if (text.scaleX !== 1 || text.scaleY !== 1) text.setScale(1);
      if (text.rotation !== 0) text.setRotation(0);
      return;
    }

    const duration = Math.max(0.1, entry.pulseDuration || 0.45);
    const elapsed = Math.max(0, animationTime - start);
    if (elapsed >= duration) {
      entry.pulseStart = null;
      text.setScale(1);
      text.setRotation(0);
      return;
    }

    const progress = Math.min(1, elapsed / duration);
    const scaleBoost = entry.pulseScale ?? 0.35;
    const eased = 1 - easeOutCubic(progress);
    const scale = 1 + scaleBoost * eased;
    const turns = entry.pulseTurns ?? 1;
    text.setScale(scale);
    text.setRotation(progress * turns * Math.PI * 2);
  }

  function triggerReverseButtonPulse(edgeId, options = {}) {
    const entry = reversePipeButtons.get(edgeId);
    if (!entry) return;
    entry.pulseStart = animationTime;
    entry.pulseDuration = options.duration ?? 0.45;
    entry.pulseScale = options.scale ?? 0.35;
    entry.pulseTurns = options.turns ?? 1.15;
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

  // Keep function for backward-compat, but update the actual in-panel toggle instead
  function updateHomeAutoExpandToggle() {
    const autoToggle = document.querySelector('#autoExpandToggle .toggle-switch');
    if (!autoToggle) return;
    if (persistentAutoExpand) autoToggle.classList.add('enabled');
    else autoToggle.classList.remove('enabled');
  }

  function updateHomeAutoAttackToggle() {
    const toggleSwitch = document.querySelector('#autoAttackToggle .toggle-switch');
    if (!toggleSwitch) return;
    if (persistentAutoAttack) toggleSwitch.classList.add('enabled');
    else toggleSwitch.classList.remove('enabled');
  }

  function updateHomeNumbersToggle() {
    const toggleSwitch = document.querySelector('#numbersToggle .toggle-switch');
    if (!toggleSwitch) return;
    if (persistentNumbers) toggleSwitch.classList.add('enabled');
    else toggleSwitch.classList.remove('enabled');
  }


  function create() {
    sceneRef = this;
    graphicsStartZones = this.add.graphics();
    if (graphicsStartZones) graphicsStartZones.setDepth(-1);
    graphicsEdges = this.add.graphics();
    graphicsNodes = this.add.graphics();
    statusText = this.add.text(10, 10, 'Connect to start a game', { font: '16px monospace', color: '#cccccc' });

    ensureVirtualCursorElement();

    // Load persistent settings
    loadPersistentAutoExpand();
    loadPersistentAutoAttack();
    loadPersistentNumbers();
    loadPersistentEdgeFlow();
    loadPersistentPreMove();
    loadPersistentSound();

    tryConnectWS();
    const menu = document.getElementById('menu');
    // Ensure toggles panel follows persisted state in both menu and in-game
    const togglesPanelEl = document.getElementById('togglesPanel');
    loadPersistentSettingsOpen();
    if (togglesPanelEl) togglesPanelEl.style.display = settingsOpen ? 'grid' : 'none';
    // Ensure visual state matches persisted values on refresh while in menu
    updateHomeAutoExpandToggle();
    updateHomeAutoAttackToggle();
    updateHomeNumbersToggle();
    updateHomeEdgeFlowToggle();
    const settingsBtn = document.getElementById('settingsButton');
    if (settingsBtn && togglesPanelEl) {
      settingsBtn.addEventListener('click', () => {
        const isHidden = (togglesPanelEl.style.display === 'none');
        const next = isHidden ? 'grid' : 'none';
        togglesPanelEl.style.display = next;
        savePersistentSettingsOpen(next === 'grid');
      });
    }
    const soundBtn = document.getElementById('soundButton');
    if (soundBtn) {
      const updateIcon = () => {
        const enabled = isSoundEnabled();
        soundBtn.textContent = enabled ? '🔊' : '🔇';
        soundBtn.title = enabled ? 'Sound On' : 'Sound Off';
        soundBtn.setAttribute('aria-label', enabled ? 'Sound On' : 'Sound Off');
      };
      updateIcon();
      soundBtn.addEventListener('click', () => {
        const enabled = isSoundEnabled();
        savePersistentSound(!enabled);
        updateIcon();
        ensureAudio();
      });
      soundBtn.addEventListener('pointerdown', ensureAudio, { once: false });
    }
    playFriendsBtn = document.getElementById('playBtn');
    playBotBtnEl = document.getElementById('playBotBtn');
    const buttonContainer = document.querySelector('.button-container');
    const playerCountButtons = document.querySelectorAll('.player-count-option');
    if (playerCountButtons && playerCountButtons.length) {
      playerCountButtons.forEach((btn) => {
        btn.addEventListener('click', () => {
          playerCountButtons.forEach((b) => b.classList.remove('active'));
          btn.classList.add('active');
          const desired = parseInt(btn.getAttribute('data-count'), 10);
          selectedPlayerCount = Number.isFinite(desired) ? desired : 2;
          enforceGameStartAvailability();
        });
      });
    }
    modeOptionsButton = document.getElementById('modeOptionsButton');
    modeOptionsPanel = document.getElementById('modeOptionsPanel');
    modeSelectorContainer = document.querySelector('.mode-selector');
    modeOptionButtons = Array.from(document.querySelectorAll('.mode-option-button'));
    bridgeCostSlider = document.getElementById('bridgeCostSlider');
    bridgeCostValueLabel = document.getElementById('bridgeCostValue');
    passiveIncomeSlider = document.getElementById('passiveIncomeSlider');
    passiveIncomeValueLabel = document.getElementById('passiveIncomeValue');
    neutralCaptureSlider = document.getElementById('neutralCaptureSlider');
    neutralCaptureValueLabel = document.getElementById('neutralCaptureValue');
    ringRatioSlider = document.getElementById('ringRatioSlider');
    ringRatioValueLabel = document.getElementById('ringRatioValue');
    ringPayoutSlider = document.getElementById('ringPayoutSlider');
    ringPayoutValueLabel = document.getElementById('ringPayoutValue');
    startingJuiceSlider = document.getElementById('startingJuiceSlider');
    startingJuiceValueLabel = document.getElementById('startingJuiceValue');
    crownHealthSlider = document.getElementById('crownHealthSlider');
    crownHealthValueLabel = document.getElementById('crownHealthValue');

    if (modeOptionButtons.length) {
      modeOptionButtons.forEach((btn) => {
        btn.addEventListener('click', (event) => {
          event.stopPropagation();
          if (btn.disabled) return;
          const setting = btn.getAttribute('data-setting');
          const value = btn.getAttribute('data-value');
          if (!setting || typeof value === 'undefined') return;
          applySelectedSettings({ [setting]: value });
        });
      });
    }

    if (bridgeCostSlider) {
      bridgeCostSlider.min = String(BRIDGE_COST_MIN);
      bridgeCostSlider.max = String(BRIDGE_COST_MAX);
      bridgeCostSlider.step = String(BRIDGE_COST_STEP);
      bridgeCostSlider.addEventListener('input', (event) => {
        const sliderValue = Number(event.target.value);
        applySelectedSettings({ bridgeCost: sliderValue });
      });
      bridgeCostSlider.addEventListener('change', (event) => {
        const sliderValue = Number(event.target.value);
        applySelectedSettings({ bridgeCost: sliderValue });
      });
    }

    if (passiveIncomeSlider) {
      passiveIncomeSlider.min = String(PASSIVE_INCOME_MIN);
      passiveIncomeSlider.max = String(PASSIVE_INCOME_MAX);
      passiveIncomeSlider.step = String(PASSIVE_INCOME_STEP);
      const handler = (event) => {
        const sliderValue = Number(event.target.value);
        applySelectedSettings({ passiveIncome: sliderValue });
      };
      passiveIncomeSlider.addEventListener('input', handler);
      passiveIncomeSlider.addEventListener('change', handler);
    }

    if (neutralCaptureSlider) {
      neutralCaptureSlider.min = String(NEUTRAL_CAPTURE_MIN);
      neutralCaptureSlider.max = String(NEUTRAL_CAPTURE_MAX);
      neutralCaptureSlider.step = String(NEUTRAL_CAPTURE_STEP);
      const handler = (event) => {
        const sliderValue = Number(event.target.value);
        applySelectedSettings({ neutralCaptureGold: sliderValue });
      };
      neutralCaptureSlider.addEventListener('input', handler);
      neutralCaptureSlider.addEventListener('change', handler);
    }

    if (ringRatioSlider) {
      ringRatioSlider.min = String(RING_RATIO_MIN);
      ringRatioSlider.max = String(RING_RATIO_MAX);
      ringRatioSlider.step = String(RING_RATIO_STEP);
      const handler = (event) => {
        const sliderValue = Number(event.target.value);
        applySelectedSettings({ ringJuiceToGoldRatio: sliderValue });
      };
      ringRatioSlider.addEventListener('input', handler);
      ringRatioSlider.addEventListener('change', handler);
    }

    if (ringPayoutSlider) {
      ringPayoutSlider.min = String(RING_PAYOUT_MIN);
      ringPayoutSlider.max = String(RING_PAYOUT_MAX);
      ringPayoutSlider.step = String(RING_PAYOUT_STEP);
      const handler = (event) => {
        const sliderValue = Number(event.target.value);
        applySelectedSettings({ ringPayoutGold: sliderValue });
      };
      ringPayoutSlider.addEventListener('input', handler);
      ringPayoutSlider.addEventListener('change', handler);
    }

    if (startingJuiceSlider) {
      startingJuiceSlider.min = String(STARTING_JUICE_MIN);
      startingJuiceSlider.max = String(STARTING_JUICE_MAX);
      startingJuiceSlider.step = String(STARTING_JUICE_STEP);
      const handler = (event) => {
        const sliderValue = Number(event.target.value);
        applySelectedSettings({ startingNodeJuice: sliderValue });
      };
      startingJuiceSlider.addEventListener('input', handler);
      startingJuiceSlider.addEventListener('change', handler);
    }

    if (crownHealthSlider) {
      crownHealthSlider.min = String(CROWN_HEALTH_MIN);
      crownHealthSlider.max = String(CROWN_HEALTH_MAX);
      crownHealthSlider.step = String(CROWN_HEALTH_STEP);
      const handler = (event) => {
        const sliderValue = Number(event.target.value);
        applySelectedSettings({ kingCrownHealth: sliderValue });
      };
      crownHealthSlider.addEventListener('input', handler);
      crownHealthSlider.addEventListener('change', handler);
    }

    const closeModePanel = () => {
      if (!modeOptionsPanel) return;
      modeOptionsPanel.style.display = 'none';
      modeOptionsPanel.setAttribute('aria-hidden', 'true');
      if (modeOptionsButton) modeOptionsButton.setAttribute('aria-expanded', 'false');
      modePanelOpen = false;
    };

    const openModePanel = () => {
      if (!modeOptionsPanel) return;
      modeOptionsPanel.style.display = 'flex';
      modeOptionsPanel.setAttribute('aria-hidden', 'false');
      if (modeOptionsButton) modeOptionsButton.setAttribute('aria-expanded', 'true');
      modePanelOpen = true;
    };

    if (modeOptionsButton && modeOptionsPanel) {
      modeOptionsButton.addEventListener('click', (event) => {
        event.stopPropagation();
        if (modePanelOpen) closeModePanel();
        else openModePanel();
      });
    }

    if (modeOptionsPanel) {
      modeOptionsPanel.addEventListener('click', (event) => {
        event.stopPropagation();
      });
    }

    document.addEventListener('click', () => {
      if (modePanelOpen) closeModePanel();
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && modePanelOpen) {
        closeModePanel();
      }
    });

    applySelectedSettings({});
    
    if (playFriendsBtn) {
      playFriendsBtn.disabled = false;
      playFriendsBtn.addEventListener('click', () => {
        if (isReplayActive()) {
          setReplayStatus('Stop the current replay before joining a lobby.', 'warn');
          return;
        }
        if (ws && ws.readyState === WebSocket.OPEN) {
          showLobby();
          // Show selected player count immediately
          setLobbyStatus(`Waiting for players to join... (${selectedPlayerCount}-player Brass game · ${formatModeSettingsSummary()})`);
          // Hide both buttons when entering lobby
          if (buttonContainer) {
            buttonContainer.style.display = 'none';
          }
          setModeSelectorVisibility(false);
          // Show lobby back button
          if (lobbyBackButton) lobbyBackButton.style.display = 'block';
          ws.send(JSON.stringify({
            type: 'joinLobby',
            token: localStorage.getItem('token') || null,
            autoExpand: persistentAutoExpand,
            playerCount: selectedPlayerCount,
            mode: MODE_QUEUE_KEY,
            settings: buildModeSettingsPayload(),
          }));
        }
      });
    }

    if (playBotBtnEl) {
      updatePlayBotAvailability(true);
      playBotBtnEl.addEventListener('click', () => {
        if (isReplayActive()) {
          setReplayStatus('Stop the current replay before starting a bot match.', 'warn');
          return;
        }
        if (ws && ws.readyState === WebSocket.OPEN) {
          const launchingSandbox = MODE_QUEUE_KEY === 'sandbox';
          const modeSummary = formatModeSettingsSummary();
          if (launchingSandbox) {
            console.log('Launching sandbox session');
          } else {
            console.log(`Starting hard bot game with ${modeSummary} options`);
          }
          showLobby();
          const statusText = launchingSandbox
            ? 'Loading sandbox...'
            : `Starting hard bot game (${modeSummary} rules)...`;
          setLobbyStatus(statusText);
          // Hide buttons when starting bot game
          if (buttonContainer) {
            buttonContainer.style.display = 'none';
          }
          setModeSelectorVisibility(false);
          ws.send(JSON.stringify({
            type: 'startBotGame',
            difficulty: launchingSandbox ? 'sandbox' : 'hard',
            autoExpand: persistentAutoExpand,
            mode: MODE_QUEUE_KEY,
            settings: buildModeSettingsPayload(),
          }));
        }
      });
    }

    ensureReplayElements();
    if (ENABLE_REPLAY_UPLOAD) {
      if (replayFileInputEl && !replayFileInputEl.dataset.boundChange) {
        replayFileInputEl.addEventListener('change', handleReplayFileSelect);
        replayFileInputEl.dataset.boundChange = 'true';
      }
      if (replayWatchBtnEl && !replayWatchBtnEl.dataset.boundClick) {
        replayWatchBtnEl.addEventListener('click', startReplayFromSelection);
        replayWatchBtnEl.dataset.boundClick = 'true';
      }
    } else if (replayPanelEl) {
      replayPanelEl.style.display = 'none';
    }
    ensureReplaySpeedElements();
    replaySpeedValue = 1;
    if (replaySpeedInput) replaySpeedInput.value = '1';
    updateReplaySpeedLabel();
    updateReplaySpeedUI();
    clearReplaySelection();
    setReplayControlsDisabled(false);
    setReplayStatus('', 'info');

    // Quit overlay button (forfeit - bottom left)
    const quitBtn = document.createElement('button');
    quitBtn.textContent = 'Forfeit';
    Object.assign(quitBtn.style, { position: 'absolute', left: '10px', bottom: '10px', zIndex: 10, padding: '8px 14px', borderRadius: '8px', border: 'none', background: '#ff5555', color: '#111', cursor: 'pointer', display: 'none' });
    document.body.appendChild(quitBtn);
    quitBtn.addEventListener('click', () => {
      if (pointerLockActive) {
        releaseVirtualCursor();
      }
      if (isReplayActive() || replayMode) {
        stopReplaySession();
        replayMode = false;
        replayStartPending = false;
        replaySessionActive = false;
        setReplayControlsDisabled(false);
        setReplayStatus('', 'info');
        returnToMenu();
        return;
      }
      const token = localStorage.getItem('token');
    if (ws && ws.readyState === WebSocket.OPEN) {
      if (!gameEnded && !myEliminated) {
        ws.send(JSON.stringify({ type: 'quitGame', token }));
        myEliminated = true;
        updateQuitButtonLabel();
        if (overlayMsg) {
          overlayMsg.textContent = 'Eliminated';
          overlayMsg.style.display = 'block';
        }
        return;
      }
      // Postgame: leaving rematch flow
      if (currentPostgameGroupId && !opponentHasLeft) {
        ws.send(JSON.stringify({ type: 'postgameQuit', groupId: currentPostgameGroupId, token }));
      }
    }

    if (gameEnded || myEliminated) {
      returnToMenu();
    }
    });
    quitButton = quitBtn;

    sandboxResetButton = document.createElement('button');
    sandboxResetButton.textContent = 'Reset';
    Object.assign(sandboxResetButton.style, {
      position: 'absolute', left: '120px', bottom: '10px', zIndex: 10,
      padding: '8px 14px', borderRadius: '8px', border: 'none',
      background: '#8fd3ff', color: '#082034', cursor: 'pointer',
      boxShadow: '0 6px 18px rgba(0,0,0,0.25)', display: 'none',
      transition: 'transform 120ms ease, opacity 120ms ease'
    });
    sandboxResetButton.addEventListener('mouseenter', () => {
      sandboxResetButton.style.transform = 'scale(1.03)';
    });
    sandboxResetButton.addEventListener('mouseleave', () => {
      sandboxResetButton.style.transform = 'scale(1.0)';
    });
    sandboxResetButton.addEventListener('click', () => {
      requestSandboxReset();
    });
    document.body.appendChild(sandboxResetButton);

    sandboxClearButton = document.createElement('button');
    sandboxClearButton.textContent = 'Clear';
    Object.assign(sandboxClearButton.style, {
      position: 'absolute', left: '210px', bottom: '10px', zIndex: 10,
      padding: '8px 14px', borderRadius: '8px', border: 'none',
      background: '#ffd37e', color: '#3b2500', cursor: 'pointer',
      boxShadow: '0 6px 18px rgba(0,0,0,0.25)', display: 'none',
      transition: 'transform 120ms ease, opacity 120ms ease'
    });
    sandboxClearButton.addEventListener('mouseenter', () => {
      sandboxClearButton.style.transform = 'scale(1.03)';
    });
    sandboxClearButton.addEventListener('mouseleave', () => {
      sandboxClearButton.style.transform = 'scale(1.0)';
    });
    sandboxClearButton.addEventListener('click', () => {
      requestSandboxClear();
    });
    document.body.appendChild(sandboxClearButton);
    updateSandboxButtonVisibility();

    if (!forfeitKeyListenerAttached) {
      document.addEventListener('keydown', (event) => {
        if (!quitButton) return;
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        if (event.key !== 'f' && event.key !== 'F') return;
        const target = event.target;
        const tagName = target && target.tagName ? target.tagName.toLowerCase() : '';
        if (tagName === 'input' || tagName === 'textarea') return;
        if (target && typeof target.isContentEditable === 'boolean' && target.isContentEditable) return;
        event.preventDefault();
        quitButton.click();
      });
      forfeitKeyListenerAttached = true;
    }

    reviewReplayDownloadButton = document.createElement('button');
    reviewReplayDownloadButton.textContent = 'Download';
    reviewReplayDownloadButton.dataset.clicked = '';
    Object.assign(reviewReplayDownloadButton.style, {
      position: 'absolute', left: '70px', bottom: '56px', zIndex: 11,
      padding: '8px 14px', borderRadius: '8px', border: 'none',
      background: '#f3eaff', color: '#251638', cursor: 'pointer',
      boxShadow: '0 4px 12px rgba(0,0,0,0.25)', display: 'none',
      width: '80px',
      transition: 'transform 120ms ease, background 160ms ease, color 160ms ease, box-shadow 160ms ease, opacity 120ms ease'
    });
    reviewReplayDownloadButton.addEventListener('mouseenter', () => {
      if (reviewReplayDownloadButton.dataset.clicked === 'true') return;
      reviewReplayDownloadButton.style.transform = 'scale(1.03)';
      reviewReplayDownloadButton.style.background = '#ffffff';
    });
    reviewReplayDownloadButton.addEventListener('mouseleave', () => {
      if (reviewReplayDownloadButton.dataset.clicked === 'true') return;
      reviewReplayDownloadButton.style.transform = 'scale(1.0)';
      reviewReplayDownloadButton.style.background = '#f3eaff';
    });
    reviewReplayDownloadButton.addEventListener('click', () => {
      if (!reviewReplayActive || !reviewReplayLastData) {
        handleReplayError({ message: 'Replay unavailable.' });
        return;
      }
      const saved = saveReplayToDisk(reviewReplayLastData, reviewReplayLastFilename);
      if (!saved) {
        handleReplayError({ message: 'Could not save replay.' });
        return;
      }
      reviewReplayDownloadButton.dataset.clicked = 'true';
      reviewReplayDownloadButton.style.background = '#d8c6ff';
      reviewReplayDownloadButton.style.color = '#2c1242';
      reviewReplayDownloadButton.style.boxShadow = '0 2px 8px rgba(0,0,0,0.35) inset';
      reviewReplayDownloadButton.style.transform = 'scale(0.98)';
    });
    document.body.appendChild(reviewReplayDownloadButton);

    // Rematch UI elements (created once)
    rematchButton = document.createElement('button');
    rematchButton.textContent = 'Rematch';
    Object.assign(rematchButton.style, {
      position: 'absolute', left: '160px', bottom: '10px', zIndex: 10,
      padding: '8px 14px', borderRadius: '8px', border: 'none',
      background: '#7ee49c', color: '#0a2f18', cursor: 'pointer',
      boxShadow: '0 6px 18px rgba(0,0,0,0.35)', display: 'none',
      transition: 'transform 120ms ease, background 160ms ease, color 160ms ease, box-shadow 160ms ease'
    });
    rematchButton.addEventListener('mouseenter', () => {
      if (rematchButton.dataset.state !== 'selected') rematchButton.style.transform = 'scale(1.03)';
    });
    rematchButton.addEventListener('mouseleave', () => {
      rematchButton.style.transform = 'scale(1.0)';
    });
    document.body.appendChild(rematchButton);
    rematchButton.addEventListener('click', () => {
      if (isReplayActive()) return;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (!currentPostgameGroupId) return;
      if (iHaveRematched) return;
      const token = localStorage.getItem('token');
      ws.send(JSON.stringify({ type: 'postgameRematch', groupId: currentPostgameGroupId, token }));
      iHaveRematched = true;
      rematchButton.dataset.state = 'selected';
      rematchButton.style.background = '#3a8f56';
      rematchButton.style.color = '#cfe8d6';
      rematchButton.style.boxShadow = '0 2px 10px rgba(0,0,0,0.4) inset';
      rematchButton.style.transform = 'scale(0.98)';
    });

    saveReplayWrapper = document.createElement('div');
    Object.assign(saveReplayWrapper.style, {
      position: 'absolute', left: '70px', bottom: '10px', zIndex: 10,
      display: 'none', width: 'auto',
    });

    saveReplayButton = document.createElement('button');
    saveReplayButton.textContent = 'Review';
    saveReplayButton.dataset.clicked = '';
    Object.assign(saveReplayButton.style, {
      padding: '8px 14px', borderRadius: '8px', border: 'none',
      background: '#ff7ac7', color: '#3a123f', cursor: 'pointer',
      boxShadow: '0 6px 18px rgba(0,0,0,0.35)', display: 'block',
      width: '80px',
      transition: 'transform 120ms ease, background 160ms ease, color 160ms ease, box-shadow 160ms ease'
    });
    saveReplayButton.addEventListener('mouseenter', () => {
      if (saveReplayButton.dataset.clicked === 'true') return;
      saveReplayButton.style.transform = 'scale(1.03)';
      saveReplayButton.style.background = '#ff93d5';
    });
    saveReplayButton.addEventListener('mouseleave', () => {
      if (saveReplayButton.dataset.clicked === 'true') return;
      saveReplayButton.style.transform = 'scale(1.0)';
      saveReplayButton.style.background = '#ff7ac7';
    });
    saveReplayButton.addEventListener('click', () => {
      if (isReplayActive()) return;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const groupId = currentPostgameGroupId;
      if (!groupId) return;
      const token = localStorage.getItem('token');
      if (!token) return;

      pendingReplayIntent = 'review';
      reviewReplayActive = false;
      reviewReplayLastData = null;
      reviewReplayLastFilename = null;
      if (reviewDropdownButton) reviewDropdownButton.style.display = 'none';

      try {
        ws.send(JSON.stringify({ type: 'requestReplay', groupId, token }));
      } catch (err) {
        console.error('Failed to request replay for review', err);
        pendingReplayIntent = null;
        return;
      }

      if (rematchButton) rematchButton.style.display = 'none';
      if (saveReplayWrapper) saveReplayWrapper.style.display = 'none';
      if (postgameNotice) {
        postgameNotice.textContent = '';
        postgameNotice.style.display = 'none';
      }
      opponentHasLeft = true;
      currentPostgameGroupId = null;

      try {
        ws.send(JSON.stringify({ type: 'postgameQuit', groupId, token }));
      } catch (err) {
        console.error('Failed to notify postgame quit after review', err);
      }
    });

    reviewDropdownButton = document.createElement('button');
    reviewDropdownButton.textContent = 'Download';
    Object.assign(reviewDropdownButton.style, {
      position: 'absolute', bottom: '46px', left: '0px', borderRadius: '8px', border: 'none',
      padding: '8px 14px', background: '#f3eaff', color: '#251638',
      cursor: 'pointer', boxShadow: '0 4px 12px rgba(0,0,0,0.25)',
      display: 'none', transition: 'background 160ms ease, color 160ms ease, box-shadow 160ms ease, opacity 120ms ease', opacity: '0'
    });
    reviewDropdownButton.addEventListener('mouseenter', () => {
      reviewDropdownButton.style.background = '#ffffff';
    });
    reviewDropdownButton.addEventListener('mouseleave', () => {
      reviewDropdownButton.style.background = '#f3eaff';
    });
    reviewDropdownButton.addEventListener('click', (event) => {
      event.stopPropagation();
      if (isReplayActive()) return;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (!currentPostgameGroupId) return;
      const token = localStorage.getItem('token');
      if (!token) return;

      pendingReplayIntent = 'download';
      try {
        ws.send(JSON.stringify({ type: 'requestReplay', groupId: currentPostgameGroupId, token }));
        reviewDropdownButton.style.background = '#e5d7ff';
        reviewDropdownButton.style.boxShadow = '0 2px 8px rgba(0,0,0,0.25) inset';
      } catch (err) {
        console.error('Failed to request replay download', err);
        pendingReplayIntent = null;
      }
    });

    saveReplayWrapper.addEventListener('mouseenter', () => {
      if (reviewDropdownButton) {
        reviewDropdownButton.style.display = 'block';
        setTimeout(() => { reviewDropdownButton.style.opacity = '1'; }, 10);
      }
    });
    saveReplayWrapper.addEventListener('mouseleave', () => {
      if (reviewDropdownButton) {
        reviewDropdownButton.style.opacity = '0';
        setTimeout(() => { if (reviewDropdownButton.style.opacity === '0') reviewDropdownButton.style.display = 'none'; }, 120);
      }
    });

    saveReplayWrapper.appendChild(saveReplayButton);
    saveReplayWrapper.appendChild(reviewDropdownButton);
    document.body.appendChild(saveReplayWrapper);

    postgameNotice = document.createElement('div');
    postgameNotice.textContent = '';
    Object.assign(postgameNotice.style, {
      position: 'absolute', left: '160px', bottom: '10px', zIndex: 10,
      padding: '8px 14px', borderRadius: '8px', color: '#b22222',
      background: 'rgba(0,0,0,0.0)', display: 'none', font: '16px/1.2 monospace'
    });
    document.body.appendChild(postgameNotice);
    // Toggle quit button visibility based on menu
    const observer = new MutationObserver(() => {
      const menuVisible = !menu.classList.contains('hidden');
      quitBtn.style.display = menuVisible ? 'none' : 'block';
      // Rematch UI only visible when not in menu and after game ends
      const hasGroup = !!currentPostgameGroupId;
      const showRematchUI = !menuVisible && gameEnded && hasGroup && !opponentHasLeft;
      const showSaveReplay = !menuVisible && gameEnded && hasGroup;
      rematchButton.style.display = showRematchUI ? 'block' : 'none';
      if (saveReplayWrapper) {
        saveReplayWrapper.style.display = showSaveReplay ? 'block' : 'none';
      }
      postgameNotice.style.display = (!menuVisible && opponentHasLeft) ? 'block' : 'none';
      // Buttons now positioned horizontally at bottom with fixed positions
      updateReplaySpeedUI();
      updateReviewReplayDownloadButton();
      updateSandboxButtonVisibility();
    });
    observer.observe(menu, { attributes: true, attributeFilter: ['class'] });
    // Also hide win/lose overlay when menu is visible (keep settings panel state)
    const menuObserver = new MutationObserver(() => {
      const menuVisible = !menu.classList.contains('hidden');
      if (menuVisible && overlayMsg) overlayMsg.style.display = 'none';
      // Do not auto-hide the toggles panel so its state persists into menu
    });
    menuObserver.observe(menu, { attributes: true, attributeFilter: ['class'] });

    // Lobby "Go Back" button (visible only when lobby is shown in the menu)
    const backBtn = document.createElement('button');
    backBtn.textContent = 'Back';
    Object.assign(backBtn.style, {
      position: 'absolute',
      right: '10px',
      top: '10px',
      zIndex: 12,
      padding: '8px 14px',
      borderRadius: '8px',
      border: '2px solid #666',
      background: '#eeeeee',
      color: '#111',
      cursor: 'pointer',
      display: 'none'
    });
    document.body.appendChild(backBtn);
    backBtn.addEventListener('click', () => {
      if (isReplayActive()) {
        setReplayStatus('Stop the current replay before leaving the lobby.', 'warn');
        return;
      }
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'leaveLobby' }));
      }
      returnToMenu();
    });
    lobbyBackButton = backBtn;

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

    // Gold number display in top right corner
    const goldNumber = document.createElement('div');
    goldNumber.id = 'goldNumber';
    Object.assign(goldNumber.style, {
      position: 'absolute',
      right: '8px',
      top: '2px',
      fontSize: '58px',
      fontWeight: 'bold',
      color: MONEY_GAIN_COLOR,
      lineHeight: '1',
      textAlign: 'right',
      textShadow: '2px 2px 4px rgba(0,0,0,0.8)',
      zIndex: 12,
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
    progressNameContainer = document.getElementById('progressBarNames');
    if (progressMarkerLeft) progressMarkerLeft.style.display = 'none';
    if (progressMarkerRight) progressMarkerRight.style.display = 'none';
    if (progressNameContainer) progressNameContainer.style.display = 'none';
    
    // Initialize UI background bars
    topUiBar = document.getElementById('topUiBar');
    bottomUiBar = document.getElementById('bottomUiBar');
    gemCountsDisplay = document.getElementById('gemCountsDisplay');
    if (gemCountsDisplay && !gemCountsClickHandlerBound) {
      gemCountsDisplay.addEventListener('click', handleGemCountsClick);
      gemCountsClickHandlerBound = true;
    }
    gemCountLabels.clear();
    if (gemCountsDisplay) {
      GEM_TYPE_ORDER.forEach((key) => {
        const container = gemCountsDisplay.querySelector(`[data-gem="${key}"]`);
        if (!container) return;
        const numberEl = container.querySelector('.gem-number');
        if (numberEl) {
          gemCountLabels.set(key, numberEl);
        }
      });
    }
    updateGemModeUi();
    updateGemCountsDisplay();
    
    // Initialize timer display
    timerDisplay = document.getElementById('timerDisplay');
    

  // Initialize auto-expand toggle (works in menu and in-game)
  autoExpandToggle = document.getElementById('autoExpandToggle');
  if (autoExpandToggle) {
    const toggleSwitch = autoExpandToggle.querySelector('.toggle-switch');
    if (toggleSwitch) {
      toggleSwitch.addEventListener('click', () => {
        // Always flip local persistent state and visuals immediately
        const newValue = !persistentAutoExpand;
        savePersistentAutoExpand(newValue);
        // Keep in-panel and in-game views in sync instantly while waiting for server
        myAutoExpand = newValue;
        updateHomeAutoExpandToggle();
        updateAutoExpandToggle();

        // Notify server whenever possible (menu or in-game)
        if (ws && ws.readyState === WebSocket.OPEN && !gameEnded && !replayMode) {
          const token = localStorage.getItem('token');
          ws.send(JSON.stringify({ type: 'toggleAutoExpand', token }));
        }
      });
    }
  }

  // Initialize auto-attack toggle (works in menu and in-game)
  autoAttackToggle = document.getElementById('autoAttackToggle');
  if (autoAttackToggle) {
    const toggleSwitch = autoAttackToggle.querySelector('.toggle-switch');
    if (toggleSwitch) {
      toggleSwitch.addEventListener('click', () => {
        const newValue = !persistentAutoAttack;
        savePersistentAutoAttack(newValue);
        myAutoAttack = newValue;
        updateHomeAutoAttackToggle();
        updateAutoAttackToggle();

        if (ws && ws.readyState === WebSocket.OPEN && !gameEnded && !replayMode) {
          const token = localStorage.getItem('token');
          ws.send(JSON.stringify({ type: 'toggleAutoAttack', token }));
        }
      });
    }
  }

  // Initialize numbers toggle
  numbersToggle = document.getElementById('numbersToggle');
  if (numbersToggle) {
    const toggleSwitch = numbersToggle.querySelector('.toggle-switch');
    if (toggleSwitch) {
      toggleSwitch.addEventListener('click', () => {
        const newValue = !persistentNumbers;
        savePersistentNumbers(newValue);
        updateNumbersToggle();
        updateHomeNumbersToggle();
      });
    }
  }

  // Initialize edge flow toggle
  edgeFlowToggle = document.getElementById('edgeFlowToggle');
  if (edgeFlowToggle) {
    const toggleSwitch = edgeFlowToggle.querySelector('.toggle-switch');
    if (toggleSwitch) {
      toggleSwitch.addEventListener('click', () => {
        const newValue = !persistentEdgeFlow;
        savePersistentEdgeFlow(newValue);
        updateEdgeFlowToggle();
        updateHomeEdgeFlowToggle();
        redrawStatic();
      });
    }
  }

  // Initialize pre-move toggle
  preMoveToggle = document.getElementById('preMoveToggle');
  if (preMoveToggle) {
    const toggleSwitch = preMoveToggle.querySelector('.toggle-switch');
    if (toggleSwitch) {
      toggleSwitch.addEventListener('click', () => {
        const newValue = !persistentPreMove;
        savePersistentPreMove(newValue);
        updatePreMoveToggle();
        updateHomePreMoveToggle();
        if (!newValue) {
          clearAllPrePipes('toggleOff');
        }
        redrawStatic();
      });
    }
  }
  updatePreMoveToggle();

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

    // Initialize home screen auto-attack toggle
    homeAutoAttackToggle = document.getElementById('homeAutoAttackToggle');
    if (homeAutoAttackToggle) {
      const toggleSwitch = homeAutoAttackToggle.querySelector('.toggle-switch');
      if (toggleSwitch) {
        toggleSwitch.addEventListener('click', () => {
          const newValue = !persistentAutoAttack;
          savePersistentAutoAttack(newValue);
          updateHomeAutoAttackToggle();
        });
      }
      updateHomeAutoAttackToggle();
    }

    // Initialize home screen numbers toggle
    homeNumbersToggle = document.getElementById('homeNumbersToggle');
    if (homeNumbersToggle) {
      const toggleSwitch = homeNumbersToggle.querySelector('.toggle-switch');
      if (toggleSwitch) {
        toggleSwitch.addEventListener('click', () => {
          const newValue = !persistentNumbers;
          savePersistentNumbers(newValue);
          updateHomeNumbersToggle();
          updateNumbersToggle();
        });
      }
      // Initialize the toggle state
      updateHomeNumbersToggle();
    }

    // Initialize home screen edge flow toggle
    homeEdgeFlowToggle = document.getElementById('homeEdgeFlowToggle');
    if (homeEdgeFlowToggle) {
      const toggleSwitch = homeEdgeFlowToggle.querySelector('.toggle-switch');
      if (toggleSwitch) {
        toggleSwitch.addEventListener('click', () => {
          const newValue = !persistentEdgeFlow;
          savePersistentEdgeFlow(newValue);
          updateHomeEdgeFlowToggle();
          updateEdgeFlowToggle();
          redrawStatic();
        });
      }
      // Initialize the toggle state
      updateHomeEdgeFlowToggle();
    }

    // Initialize potential home pre-move toggle shell
    homePreMoveToggle = document.getElementById('homePreMoveToggle');
    if (homePreMoveToggle) {
      const toggleSwitch = homePreMoveToggle.querySelector('.toggle-switch');
      if (toggleSwitch) {
        toggleSwitch.addEventListener('click', () => {
          const newValue = !persistentPreMove;
          savePersistentPreMove(newValue);
          updateHomePreMoveToggle();
          updatePreMoveToggle();
          if (!newValue) clearAllPrePipes('toggleOff');
          redrawStatic();
        });
      }
      updateHomePreMoveToggle();
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

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  function updateNodeAnimations() {
    let needsRedraw = false;
    nodes.forEach((node) => {
      const startTime = node.moveStartTime;
      if (startTime == null) {
        return;
      }
      const duration = Math.max(NODE_MOVE_EPSILON, node.moveDuration || NODE_MOVE_DURATION_SEC);
      const elapsed = animationTime - startTime;
      const targetX = Number.isFinite(node.targetX) ? node.targetX : node.x;
      const targetY = Number.isFinite(node.targetY) ? node.targetY : node.y;
      const startX = Number.isFinite(node.startX) ? node.startX : node.x;
      const startY = Number.isFinite(node.startY) ? node.startY : node.y;
      if (elapsed >= duration) {
        const hadChange =
          !Number.isFinite(node.x) ||
          !Number.isFinite(node.y) ||
          Math.abs(node.x - targetX) > NODE_MOVE_EPSILON ||
          Math.abs(node.y - targetY) > NODE_MOVE_EPSILON;
        node.x = targetX;
        node.y = targetY;
        node.startX = targetX;
        node.startY = targetY;
        node.moveStartTime = null;
        node.moveDuration = null;
        if (hadChange) {
          needsRedraw = true;
        }
        return;
      }

      const t = Math.max(0, Math.min(1, elapsed / Math.max(duration, NODE_MOVE_EPSILON)));
      const eased = easeOutCubic(t);
      node.x = startX + (targetX - startX) * eased;
      node.y = startY + (targetY - startY) * eased;
      needsRedraw = true;
    });
    return needsRedraw;
  }

  function update() {
    // Update animation timer for juice flow
    animationTime += 1/60; // Assuming 60 FPS, increment by frame time
    const nodesAnimating = updateNodeAnimations();

    // Update money indicators
    updateMoneyIndicators();

    const prePipeStateChanged = updatePrePipesState();
    
    // Update game timer
    const remainingTime = updateTimer();
    
    // Check if timer has expired
    if (remainingTime <= 0 && gameStartTime && !gameEnded) {
      // Timer expired - game should end
      // The backend will handle the actual game end logic
    }

    // Redraw if there are any flowing edges (for animation), money indicators, targeting, or spin animations
    let hasFlowingEdges = false;
    for (const [id, edge] of edges.entries()) {
      if (edge.flowing) {
        hasFlowingEdges = true;
        break;
      }
    }
    const anySpinning = updateReverseSpinAnimations();
    const removalAnimating = updateEdgeRemovalAnimations();
    const prePipesAnimating = prePipes.size > 0;
    
    const kingTargetsAnimating = kingSelectionActive && kingMoveTargetsList.length > 0;
    const kingSelectionPending = kingSelectionActive && kingMoveOptionsPending;
    if (hasFlowingEdges || anySpinning || removalAnimating || moneyIndicators.length > 0 || (persistentTargeting && currentTargetNodeId !== null) || nodesAnimating || prePipesAnimating || prePipeStateChanged || kingTargetsAnimating || kingSelectionPending) {
      redrawStatic();
    }
  }

  // Edge reversal spin animation state helpers
  const EDGE_SPIN_PER_TRIANGLE_SEC = 0.08;
  function startEdgeReverseSpin(edge) {
    const s = nodes.get(edge.source);
    const t = nodes.get(edge.target);
    if (!s || !t) return;
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    const fromR = Math.max(1, calculateNodeRadius(s, baseScale)) + 1;
    const toR = Math.max(1, calculateNodeRadius(t, baseScale)) + 1;
    const path = buildEdgeScreenPath(edge, s, t, fromR, toR);
    const len = path.reduce((sum, seg) => sum + seg.length, 0);
    if (len <= 0) return;
    const triH = 16;
    const triCount = Math.max(1, Math.floor(len / triH));
    edge._spin = {
      spinStartTime: animationTime,
      spinCount: triCount,
      spinDuration: triCount * EDGE_SPIN_PER_TRIANGLE_SEC + 0.12
    };
  }
  function updateReverseSpinAnimations() {
    let any = false;
    for (const e of edges.values()) {
      if (e._spin) {
        any = true;
        const elapsed = animationTime - e._spin.spinStartTime;
        if (elapsed >= e._spin.spinDuration) {
          delete e._spin;
        }
      }
    }
    return any;
  }

  function tryConnectWS() {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      console.log('WS connected');
      if (statusText) statusText.setText('Connected. Checking for active game...');
      // Ask server to send init if a game exists
      const storedToken = localStorage.getItem('token');
      if (storedToken) ws.send(JSON.stringify({ type: 'requestInit', token: storedToken }));
      updateSandboxButtonVisibility();
    };
    ws.onclose = () => {
      console.log('WS disconnected, retrying in 2s');
      if (statusText) statusText.setText('Disconnected. Retrying...');
      clearKingSelection({ skipRedraw: true });
      if (replayMode || replayStartPending || replaySessionActive) {
        replayMode = false;
        replayStartPending = false;
        replaySessionActive = false;
        setReplayControlsDisabled(false);
        setReplayStatus('Connection lost. Replay stopped.', 'error');
        ensureReplaySpeedElements();
        updateReplaySpeedUI();
        replaySpeedValue = 1;
        if (replaySpeedInput) replaySpeedInput.value = '1';
        updateReplaySpeedLabel();
      }
      setTimeout(tryConnectWS, 2000);
      updateSandboxButtonVisibility();
    };
    ws.onerror = (e) => console.error('WS error', e);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg && msg.replay) {
        handleReplayMessage(msg);
        return;
      }
      if (msg.type === 'init') handleInit(msg);
      else if (msg.type === 'tick') handleTick(msg);
      else if (msg.type === 'lobbyJoined') handleLobby(msg);
      else if (msg.type === 'lobbyLeft') returnToMenu();
      else if (msg.type === 'gameOver') handleGameOver(msg);
      else if (msg.type === 'newEdge') handleNewEdge(msg);
      else if (msg.type === 'edgeReversed') handleEdgeReversed(msg);
      else if (msg.type === 'edgeUpdated') handleEdgeUpdated(msg);
      else if (msg.type === 'removeEdges') handleRemoveEdges(msg);
      else if (msg.type === 'bridgeError') handleBridgeError(msg);
      else if (msg.type === 'reverseEdgeError') handleReverseEdgeError(msg);
      else if (msg.type === 'nodeDestroyed') handleNodeDestroyed(msg);
      else if (msg.type === 'destroyError') handleDestroyError(msg);
      else if (msg.type === 'nukeError') handleNukeError(msg);
      else if (msg.type === 'nodeCaptured') handleNodeCaptured(msg);
      else if (msg.type === 'nodeOverflowPayout') handleNodeOverflowPayout(msg);
      else if (msg.type === 'kingMoveOptions') handleKingMoveOptions(msg);
      else if (msg.type === 'kingMoveError') handleKingMoveError(msg);
      else if (msg.type === 'kingMoved') handleKingMoved(msg);
      else if (msg.type === 'lobbyTimeout') handleLobbyTimeout();
      else if (msg.type === 'postgame') handlePostgame(msg);
      else if (msg.type === 'postgameRematchUpdate') handlePostgameRematchUpdate(msg);
      else if (msg.type === 'postgameOpponentLeft') handlePostgameOpponentLeft();
      else if (msg.type === 'replayData') handleReplayDownload(msg);
      else if (msg.type === 'replayError') handleReplayError(msg);
      else if (msg.type === 'sandboxNodeCreated') handleSandboxNodeCreated(msg);
      else if (msg.type === 'sandboxBoardCleared') handleSandboxBoardCleared(msg);
      else if (msg.type === 'sandboxError') handleSandboxError(msg);
    };
  }

  function handleInit(msg) {
    if (msg.replay) {
      replayMode = true;
      replaySessionActive = true;
      replayStartPending = false;
      setReplayControlsDisabled(true);
      ensureReplaySpeedElements();
      replaySpeedValue = 1;
      if (replaySpeedInput) replaySpeedInput.value = '1';
      updateReplaySpeedLabel();
      updateReplaySpeedUI();
    } else if (replayMode || replayStartPending || replaySessionActive) {
      replayMode = false;
      replayStartPending = false;
      replaySessionActive = false;
      setReplayControlsDisabled(false);
      setReplayStatus('', 'info');
      updateReplaySpeedUI();
    }

    // Clear any postgame UI on new init
    currentPostgameGroupId = null;
    opponentHasLeft = false;
    iHaveRematched = false;
    if (rematchButton) {
      rematchButton.style.display = 'none';
      rematchButton.dataset.state = '';
      rematchButton.style.background = '#7ee49c';
      rematchButton.style.color = '#0a2f18';
      rematchButton.style.boxShadow = '0 6px 18px rgba(0,0,0,0.35)';
      rematchButton.style.transform = 'scale(1.0)';
    }
    if (saveReplayWrapper) saveReplayWrapper.style.display = 'none';
    if (saveReplayButton) {
      saveReplayButton.dataset.clicked = '';
      saveReplayButton.style.background = '#ff7ac7';
      saveReplayButton.style.color = '#3a123f';
      saveReplayButton.style.boxShadow = '0 6px 18px rgba(0,0,0,0.35)';
      saveReplayButton.style.transform = 'scale(1.0)';
    }
    if (!msg.replay) {
      reviewReplayActive = false;
      reviewReplayLastData = null;
      reviewReplayLastFilename = null;
      resetReviewReplayDownloadButton();
      updateReviewReplayDownloadButton();
    }
    if (postgameNotice) {
      postgameNotice.textContent = '';
      postgameNotice.style.display = 'none';
    }
    gameEnded = false;
    myEliminated = false;
    updateQuitButtonLabel();
    currentTargetNodeId = null;
    currentTargetSetTime = null;
    resetHiddenStartState();

    if (typeof msg.gameDuration === 'number' && Number.isFinite(msg.gameDuration) && msg.gameDuration > 0) {
      gameDuration = msg.gameDuration;
    }
    hideTimerDisplay();

    screen = msg.screen || null;
    updateHiddenStartState(msg.hiddenStart);
    if (typeof msg.tickInterval === 'number' && Number.isFinite(msg.tickInterval) && msg.tickInterval > 0) {
      tickIntervalSec = msg.tickInterval;
    }
    nodes.clear();
    kingNodesByPlayer.clear();
    kingCrownDefaultMax = coerceKingCrownHealth(selectedSettings.kingCrownHealth);
    clearKingSelection({ skipRedraw: true });
    edges.clear();
    reversePipeButtons.forEach((entry, edgeId) => {
      removeReverseButton(edgeId);
    });
    reversePipeButtons.clear();
    players.clear();
    playerStats.clear();
    eliminatedPlayers.clear();
    playerOrder = [];
    hoveredNodeId = null;
    hoveredEdgeId = null;
    edgeRemovalAnimations.clear();
    clearAllPrePipes('init', { skipRedraw: true });
    progressNameSegments.clear();
    if (progressNameContainer) progressNameContainer.innerHTML = '';

    // Clear any lingering node number labels between games
    nodeJuiceTexts.forEach(text => {
      if (text) text.destroy();
    });
    nodeJuiceTexts.clear();
    nodeResourceTexts.forEach(text => {
      if (text) text.destroy();
    });
    nodeResourceTexts.clear();
    
    // Clear crown health displays
    crownHealthDisplays.forEach(display => {
      if (display) display.destroy();
    });
    crownHealthDisplays.clear();

    gameMode = normalizeMode(msg.mode || 'basic');
    const initWinCon = msg.winCondition ?? (msg.modeSettings && msg.modeSettings.winCondition);
    winCondition = normalizeWinCondition(initWinCon || 'dominate');
    if (msg.modeSettings) {
      syncSelectedSettingsFromPayload(msg.modeSettings);
      selectedMode = deriveModeFromSettings(selectedSettings);
      setSelectedMode(selectedMode, { force: true });
    } else {
      let inferredScreen = 'flat';
      if (gameMode === 'warp' || gameMode === 'i-warp') {
        inferredScreen = 'warp';
      } else if (gameMode === 'semi' || gameMode === 'i-semi') {
        inferredScreen = 'semi';
      }
      const inferred = {
        screen: inferredScreen,
        brass: (gameMode === 'i-warp' || gameMode === 'i-flat' || gameMode === 'i-semi') ? 'right-click' : 'cross',
      };
      if (msg.settings && typeof msg.settings.bridgeCostPerUnit === 'number') {
        inferred.bridgeCost = msg.settings.bridgeCostPerUnit;
      }
      if (msg.settings && typeof msg.settings.startingNodeJuice === 'number') {
        inferred.startingNodeJuice = msg.settings.startingNodeJuice;
      }
      applySelectedSettings(inferred);
      selectedMode = deriveModeFromSettings(selectedSettings);
      setSelectedMode(selectedMode, { force: true });
    }
    const initResourceMode = (msg.modeSettings && msg.modeSettings.resources) || selectedSettings.resources;
    setCurrentResourceMode(initResourceMode);

    // Clear any lingering edge flow labels between games
    edgeFlowTexts.forEach(text => {
      if (text) text.destroy();
    });
    edgeFlowTexts.clear();

    activeAbility = null;
    clearBridgeSelection();
    hideBridgeCostDisplay();

    if (Array.isArray(msg.nodes)) {
      for (const arr of msg.nodes) {
        const [
          id,
          x,
          y,
          size,
          owner,
          pendingGold = 0,
          brassFlag = 0,
          kingOwnerRaw = null,
          crownHealthRaw = null,
          crownMaxRaw = null,
          resourceTypeRaw = null,
          resourceKeyRaw = null,
        ] = arr;
        const isBrass = Number(brassFlag) === 1;
        const parsedKingOwner = kingOwnerRaw == null ? null : Number(kingOwnerRaw);
        const kingOwnerId = Number.isFinite(parsedKingOwner) ? parsedKingOwner : null;
        let crownHealth = Number(crownHealthRaw);
        if (!Number.isFinite(crownHealth)) crownHealth = null;
        let crownMax = Number(crownMaxRaw);
        if (!Number.isFinite(crownMax)) crownMax = null;
        if (kingOwnerId != null) {
          if (Number.isFinite(crownMax) && crownMax > 0) {
            kingCrownDefaultMax = Math.max(kingCrownDefaultMax, crownMax);
          } else if (Number.isFinite(crownHealth) && crownHealth > 0) {
            crownMax = crownHealth;
          } else if (kingCrownDefaultMax > 0) {
            crownMax = kingCrownDefaultMax;
          }
          if (!Number.isFinite(crownHealth) && Number.isFinite(crownMax)) {
            crownHealth = crownMax;
          }
        } else {
          crownHealth = 0;
          crownMax = 0;
        }
        if (kingOwnerId != null && Number.isFinite(crownMax) && crownMax > 0) {
          kingCrownDefaultMax = Math.max(kingCrownDefaultMax, crownMax);
        }
        const normalizedCrownMax = Number.isFinite(crownMax) ? Math.max(0, crownMax) : 0;
        const normalizedCrownHealth = Number.isFinite(crownHealth) ? Math.max(0, crownHealth) : 0;
        const resourceType = normalizeNodeResourceType(resourceTypeRaw);
        const resourceKey = resourceType === 'gem' ? normalizeNodeResourceKey(resourceKeyRaw) : null;
        nodes.set(id, {
          x,
          y,
          startX: x,
          startY: y,
          targetX: x,
          targetY: y,
          moveStartTime: null,
          moveDuration: null,
          size,
          owner,
          pendingGold: Number(pendingGold) || 0,
          isBrass,
          kingOwnerId,
          isKing: kingOwnerId != null,
          kingCrownHealth: normalizedCrownHealth,
          kingCrownMax: normalizedCrownMax,
          resourceType,
          resourceKey,
        });
      }
    }

    rebuildKingNodes();

    const edgeWarpMap = msg.edgeWarp || {};
    if (Array.isArray(msg.edges)) {
      for (const arr of msg.edges) {
        const [rawId, s, t, _forward, _always1, buildReq = 0, buildElap = 0, building = 0, goldFlag = 0, pipeTypeRaw = 'normal'] = arr;
        const edgeId = toEdgeId(rawId);
        if (edgeId == null) continue;
        const normalizedPipeType = normalizePipeType(typeof pipeTypeRaw === 'string' ? pipeTypeRaw : (Number(goldFlag) === 1 ? 'gold' : 'normal'));
        const record = {
          source: s,
          target: t,
          on: false,
          flowing: false,
          flowStartTime: null,
          building: !!building,
          buildTicksRequired: Number(buildReq || 0),
          buildTicksElapsed: Number(buildElap || 0),
          buildStartTime: animationTime,
          builtByMe: false,
          hammerAccumSec: 0,
          hammerHitIndex: 0,
          warpAxis: 'none',
          warpSegments: [],
          pipeType: normalizedPipeType,
        };
        const warpPayload = edgeWarpMap[edgeId] ?? edgeWarpMap[rawId] ?? edgeWarpMap[String(rawId)];
        applyEdgeWarpData(record, warpPayload, nodes.get(s), nodes.get(t));
        edges.set(edgeId, record);
      }
    }

    if (Array.isArray(msg.players)) {
      msg.players.forEach((info, index) => {
        let id;
        let color;
        let secondaryColors = [];
        let displayName = '';

        if (Array.isArray(info)) {
          const [pid, col] = info;
          id = Number(pid);
          color = col;
        } else if (info && typeof info === 'object') {
          id = Number(info.id);
          color = info.color;
          if (Array.isArray(info.secondaryColors)) secondaryColors = info.secondaryColors;
          if (typeof info.name === 'string') displayName = info.name;
        }

        if (!Number.isFinite(id)) return;
        players.set(id, {
          color: color || '#ffffff',
          secondaryColors,
          name: displayName,
        });
        playerStats.set(id, createDefaultPlayerStats());
        playerOrder.push(id);
      });
    }

    if (Array.isArray(msg.eliminatedPlayers)) {
      msg.eliminatedPlayers.forEach((pid) => {
        const id = Number(pid);
        if (Number.isFinite(id)) eliminatedPlayers.add(id);
      });
    }

    if (!msg.replay && msg.token) localStorage.setItem('token', msg.token);
    if (!msg.replay && msg.myPlayerId != null) localStorage.setItem('myPlayerId', String(msg.myPlayerId));
    if (msg.replay) {
      myPlayerId = Number.isFinite(msg.myPlayerId) ? Number(msg.myPlayerId) : 0;
    } else {
      myPlayerId = (msg.myPlayerId != null)
        ? Number(msg.myPlayerId)
        : Number(localStorage.getItem('myPlayerId') || '0');
    }

    OVERFLOW_PENDING_GOLD_THRESHOLD = DEFAULT_OVERFLOW_PENDING_GOLD_THRESHOLD;
    if (msg.settings) {
      if (typeof msg.settings.nodeMaxJuice === 'number') {
        nodeMaxJuice = msg.settings.nodeMaxJuice;
      }
      if (typeof msg.settings.bridgeBaseCost === 'number') {
        BRIDGE_BASE_COST = msg.settings.bridgeBaseCost;
      }
      if (typeof msg.settings.bridgeCostPerUnit === 'number') {
        BRIDGE_COST_PER_UNIT = msg.settings.bridgeCostPerUnit;
      }
      if (typeof msg.settings.overflowPendingGoldPayout === 'number') {
        OVERFLOW_PENDING_GOLD_THRESHOLD = msg.settings.overflowPendingGoldPayout;
      }
    }

    phase = typeof msg.phase === 'string' ? msg.phase : 'picking';
    myPicked = false;
    if (Array.isArray(msg.picked)) {
      msg.picked.forEach(([pid, picked]) => {
        if (Number(pid) === myPlayerId) myPicked = !!picked;
      });
    }
    if (sandboxModeEnabled()) {
      myPicked = true;
      phase = 'playing';
    }

    if (phase === 'playing') {
      if (typeof msg.timerRemaining === 'number') {
        syncTimerFromServer(msg.timerRemaining, msg.gameDuration);
      } else {
        syncTimerFromServer(gameDuration, msg.gameDuration);
      }
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
    } else {
      goldValue = 0;
    }

    if (typeof msg.winThreshold === 'number') winThreshold = msg.winThreshold;
    if (typeof msg.totalNodes === 'number') totalNodes = msg.totalNodes;

    if (msg.counts) {
      Object.entries(msg.counts).forEach(([pid, count]) => {
        const id = Number(pid);
        if (!Number.isFinite(id)) return;
        const stats = ensurePlayerStats(id);
        stats.nodes = Math.max(0, Number(count) || 0);
      });
    }

    syncGemCountsFromPayload(msg.gemCounts);
    updateGemCountsDisplay();

    myAutoExpand = persistentAutoExpand;
    if (Array.isArray(msg.autoExpand)) {
      msg.autoExpand.forEach(([pid, enabled]) => {
        if (Number(pid) === myPlayerId) {
          myAutoExpand = !!enabled;
        }
      });
    }

    myAutoAttack = persistentAutoAttack;
    if (Array.isArray(msg.autoAttack)) {
      msg.autoAttack.forEach(([pid, enabled]) => {
        if (Number(pid) === myPlayerId) {
          myAutoAttack = !!enabled;
        }
      });
    }

    updateAutoExpandToggle();
    updateAutoAttackToggle();
    updateNumbersToggle();
    updateEdgeFlowToggle();
    updatePreMoveToggle();
    updateHomePreMoveToggle();
    updatePreMoveToggle();
    updateHomePreMoveToggle();

    myEliminated = eliminatedPlayers.has(myPlayerId);
    updateQuitButtonLabel();

    computeTransform(game.scale.gameSize.width, game.scale.gameSize.height);
    const menu = document.getElementById('menu');
    if (menu) menu.classList.add('hidden');
    if (lobbyBackButton) lobbyBackButton.style.display = 'none';
    if (overlayMsg) overlayMsg.style.display = 'none';

    redrawStatic();
    if (statusText) {
      statusText.setText('');
      statusText.setVisible(false);
    }

    updateGoldBar();
    updateProgressBar();
    // Do not auto-show toggles; they remain behind Settings button

    if (progressBar) {
      progressBar.style.display = players.size > 0 ? 'block' : 'none';
    }

    updateSandboxButtonVisibility();
  }

  function handlePostgame(msg) {
    if (replayMode) return;
    clearKingSelection({ skipRedraw: true });
    currentPostgameGroupId = msg.groupId || null;
    opponentHasLeft = false;
    iHaveRematched = false;
    if (!currentPostgameGroupId) return;
    const menu = document.getElementById('menu');
    const menuVisible = menu ? !menu.classList.contains('hidden') : false;
    if (rematchButton && !menuVisible && gameEnded && !replayMode) {
      rematchButton.style.display = 'block';
      rematchButton.dataset.state = '';
      rematchButton.style.background = '#7ee49c';
      rematchButton.style.color = '#0a2f18';
      rematchButton.style.boxShadow = '0 6px 18px rgba(0,0,0,0.35)';
      rematchButton.style.transform = 'scale(1.0)';
    }
    if (saveReplayWrapper && !menuVisible && gameEnded && !replayMode) {
      saveReplayWrapper.style.display = 'block';
      if (reviewDropdownButton) reviewDropdownButton.style.display = 'none';
    }
    if (saveReplayButton) {
      saveReplayButton.dataset.clicked = '';
      saveReplayButton.style.background = '#ff7ac7';
      saveReplayButton.style.color = '#3a123f';
      saveReplayButton.style.boxShadow = '0 6px 18px rgba(0,0,0,0.35)';
      saveReplayButton.style.transform = 'scale(1.0)';
    }
    if (postgameNotice) {
      postgameNotice.textContent = '';
      postgameNotice.style.display = 'none';
    }
  }

  function handlePostgameRematchUpdate(msg) {
    // Currently we don't need to render counts; could add small ready indicator later
  }

  function handlePostgameOpponentLeft() {
    if (replayMode) return;
    opponentHasLeft = true;
    if (rematchButton) rematchButton.style.display = 'none';
    if (saveReplayWrapper && currentPostgameGroupId) {
      saveReplayWrapper.style.display = 'block';
      if (reviewDropdownButton) reviewDropdownButton.style.display = 'none';
    }
    if (postgameNotice) {
      postgameNotice.textContent = 'Opponent has left';
      postgameNotice.style.color = '#b22222';
      const menu = document.getElementById('menu');
      const menuVisible = menu ? !menu.classList.contains('hidden') : false;
      postgameNotice.style.display = menuVisible ? 'none' : 'block';
    }
  }

  function saveReplayToDisk(replayData, filenameHint) {
    try {
      const filename = (typeof filenameHint === 'string' && filenameHint.trim())
        ? filenameHint.trim()
        : (() => {
            const today = new Date();
            const iso = today.toISOString().slice(0, 10).replace(/-/g, '');
            return `durb-replay-${iso}.json`;
          })();
      const jsonString = JSON.stringify(replayData, null, 2);
      const blob = new Blob([jsonString], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(() => URL.revokeObjectURL(url), 0);
      return true;
    } catch (err) {
      console.error('Failed to save replay', err);
      return false;
    }
  }

  function resetReviewReplayDownloadButton() {
    if (!reviewReplayDownloadButton) return;
    reviewReplayDownloadButton.dataset.clicked = '';
    reviewReplayDownloadButton.style.background = '#f3eaff';
    reviewReplayDownloadButton.style.color = '#251638';
    reviewReplayDownloadButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.25)';
    reviewReplayDownloadButton.style.transform = 'scale(1.0)';
  }

  function updateReviewReplayDownloadButton() {
    if (!reviewReplayDownloadButton) return;
    const shouldShow = reviewReplayActive && replayMode;
    if (shouldShow) {
      reviewReplayDownloadButton.style.display = 'block';
      const left = quitButton && quitButton.style && quitButton.style.left ? quitButton.style.left : '10px';
      reviewReplayDownloadButton.style.left = left;
      const quitTopRaw = quitButton && quitButton.style ? quitButton.style.top : '';
      const quitTop = quitTopRaw ? parseInt(quitTopRaw.replace('px', ''), 10) : 10;
      reviewReplayDownloadButton.style.top = `${quitTop + 46}px`;
    } else {
      if (reviewReplayDownloadButton.style.display !== 'none') {
        resetReviewReplayDownloadButton();
      }
      reviewReplayDownloadButton.style.display = 'none';
    }
  }

  function handleReplayDownload(msg) {
    if (!msg || !msg.replay) return;
    const intent = pendingReplayIntent;
    pendingReplayIntent = null;

    if (intent === 'review') {
      reviewReplayLastData = msg.replay;
      reviewReplayLastFilename = typeof msg.filename === 'string' ? msg.filename : null;
      resetReviewReplayDownloadButton();
      updateReviewReplayDownloadButton();
      if (!startReplayFromPayload(msg.replay, 'review')) {
        reviewReplayActive = false;
        updateReviewReplayDownloadButton();
      }
      return;
    }

    const saved = saveReplayToDisk(msg.replay, msg.filename);
    if (!saved) {
      handleReplayError({ message: 'Could not save replay.' });
      return;
    }
    if (intent === 'download') {
      if (reviewDropdownButton) {
        reviewDropdownButton.style.background = '#d8c6ff';
        reviewDropdownButton.style.color = '#2c1242';
        reviewDropdownButton.style.boxShadow = '0 2px 8px rgba(0,0,0,0.35) inset';
      }
    } else if (saveReplayButton) {
      saveReplayButton.dataset.clicked = 'true';
      saveReplayButton.style.background = '#d8c6ff';
      saveReplayButton.style.color = '#2c1242';
      saveReplayButton.style.boxShadow = '0 2px 10px rgba(0,0,0,0.35) inset';
      saveReplayButton.style.transform = 'scale(0.98)';
    }
  }

  function handleReplayError(msg) {
    const message = msg && typeof msg.message === 'string' ? msg.message : 'Replay unavailable';
    pendingReplayIntent = null;
    if (postgameNotice) {
      postgameNotice.textContent = message;
      postgameNotice.style.color = '#b22222';
      const menu = document.getElementById('menu');
      const menuVisible = menu ? !menu.classList.contains('hidden') : false;
      postgameNotice.style.display = menuVisible ? 'none' : 'block';
    }
    if (reviewDropdownButton) {
      reviewDropdownButton.style.background = '#f3eaff';
      reviewDropdownButton.style.color = '#251638';
      reviewDropdownButton.style.boxShadow = '0 4px 12px rgba(0,0,0,0.25)';
    }
    if (saveReplayButton) {
      saveReplayButton.dataset.clicked = '';
      saveReplayButton.style.background = '#ff7ac7';
      saveReplayButton.style.color = '#3a123f';
      saveReplayButton.style.boxShadow = '0 6px 18px rgba(0,0,0,0.35)';
      saveReplayButton.style.transform = 'scale(1.0)';
    }
    reviewReplayActive = false;
    reviewReplayLastData = null;
    reviewReplayLastFilename = null;
    updateReviewReplayDownloadButton();
    pendingReplayRestart = null;
    activeReplayPayload = null;
    updateReplayRestartButtonState();
  }

  function handleLobby(msg) {
    showLobby();
    setModeSelectorVisibility(false);
    const count = Number.isFinite(msg.playerCount) ? msg.playerCount : selectedPlayerCount;
    const mode = normalizeMode(msg.mode || selectedMode);
    let summaryText = formatModeText(mode);
    if (msg.modeSettings) {
      syncSelectedSettingsFromPayload(msg.modeSettings);
      summaryText = formatModeSettingsSummary();
    } else {
      setSelectedMode(mode, { force: true });
    }
    const lobbyResourceMode = (msg.modeSettings && msg.modeSettings.resources) || selectedSettings.resources;
    setCurrentResourceMode(lobbyResourceMode);
    const isBrassQueue = msg.mode === MODE_QUEUE_KEY || Boolean(msg.modeSettings);
    const lobbyLabel = isBrassQueue ? `Brass game · ${summaryText}` : `${formatModeText(mode)} game`;
    if (msg.status === 'waiting') {
      setLobbyStatus(`Waiting for players to join... (${count}-player ${lobbyLabel})`);
    } else {
      setLobbyStatus(`Starting ${count}-player ${lobbyLabel}...`);
    }
    if (msg.token) localStorage.setItem('token', msg.token);
    // Hide the PLAY button while waiting
    const playBtn = document.getElementById('playBtn');
    if (playBtn) playBtn.style.display = 'none';
    // Ensure back button visible while in lobby
    if (lobbyBackButton) lobbyBackButton.style.display = 'block';
  }

  function handleLobbyTimeout() {
    returnToMenu();
    showLobby();
    setLobbyStatus('Lobby timed out. Try again.');
    setTimeout(() => {
      setLobbyStatus('');
      hideLobby();
    }, 3000);
  }

  function handleGameOver(msg) {
    const myId = Number(localStorage.getItem('myPlayerId') || '0');
    const viewingReplay = replayMode;
    clearKingSelection({ skipRedraw: true });
    if (overlayMsg) {
      if (viewingReplay) {
        overlayMsg.textContent = 'Replay finished';
      } else {
        overlayMsg.textContent = (msg.winnerId === myId) ? 'You win' : 'You lose';
      }
      overlayMsg.style.display = 'block';
    }
    gameEnded = true;
    releaseVirtualCursor();
    clearAllPrePipes('gameOver');
    if (!viewingReplay) {
      myEliminated = msg.winnerId !== myId;
    }
    updateQuitButtonLabel();
    // Clean up money indicators when game ends
    clearMoneyIndicators();
    // Do not clear board; leave last state visible (stale, no updates)
    redrawStatic();
    // Ensure menu elements are ready for return
    const menu = document.getElementById('menu');
    const buttonContainer = document.querySelector('.button-container');

    hideLobby();
    if (buttonContainer) {
      buttonContainer.style.display = 'flex';
    }
    // Postgame rematch UI is handled via websocket 'postgame' message
    // Don't show menu immediately - wait for user to click Quit button
  }

  function applyNodeMovements(movements) {
    if (!Array.isArray(movements) || movements.length === 0) return;
    movements.forEach((entry) => {
      let nodeId;
      let x;
      let y;
      if (Array.isArray(entry)) {
        [nodeId, x, y] = entry;
      } else if (entry && typeof entry === 'object') {
        nodeId = entry.nodeId ?? entry.id ?? entry[0];
        x = entry.x;
        y = entry.y;
      } else {
        return;
      }

      const id = Number(nodeId);
      if (!Number.isFinite(id)) return;
      const node = nodes.get(id);
      if (!node) return;

      const nx = Number(x);
      const ny = Number(y);
      if (!Number.isFinite(nx) || !Number.isFinite(ny)) return;

      const currentX = Number.isFinite(node.x) ? node.x : nx;
      const currentY = Number.isFinite(node.y) ? node.y : ny;
      const previousTargetX = Number.isFinite(node.targetX) ? node.targetX : currentX;
      const previousTargetY = Number.isFinite(node.targetY) ? node.targetY : currentY;

      const deltaX = nx - previousTargetX;
      const deltaY = ny - previousTargetY;
      if (Math.abs(deltaX) < NODE_MOVE_EPSILON && Math.abs(deltaY) < NODE_MOVE_EPSILON) {
        // No meaningful movement; ensure target is aligned and skip animation reset
        node.targetX = nx;
        node.targetY = ny;
        if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) {
          node.x = nx;
          node.y = ny;
        }
        return;
      }

      node.startX = currentX;
      node.startY = currentY;
      node.targetX = nx;
      node.targetY = ny;
      node.moveStartTime = animationTime;
      node.moveDuration = NODE_MOVE_DURATION_SEC;
    });
  }

  function handleTick(msg) {
    applyNodeMovements(msg.nodeMovements);
    updateHiddenStartState(msg.hiddenStart);
    const removalEvents = Array.isArray(msg.removedEdgeEvents) ? msg.removedEdgeEvents : null;
    if (Array.isArray(msg.removedEdges) && msg.removedEdges.length > 0) {
      removeEdges(msg.removedEdges);
    }
    if (removalEvents && removalEvents.some((event) => event && event.reason === 'bridgeCross')) {
      playBridgeExplosion();
    }

    if (typeof msg.mode === 'string') {
      gameMode = normalizeMode(msg.mode);
    }
    if (typeof msg.winCondition === 'string') {
      winCondition = normalizeWinCondition(msg.winCondition);
    }
    if (Array.isArray(msg.nodes)) {
      msg.nodes.forEach((entry) => {
        const [
          id,
          size,
          owner,
          pendingGold = 0,
          brassFlag = 0,
          kingOwnerRaw = null,
          crownHealthRaw = null,
          crownMaxRaw = null,
          resourceTypeRaw = null,
          resourceKeyRaw = null,
        ] = entry;
        const node = nodes.get(id);
        if (node) {
          const oldOwner = node.owner;
          node.size = size;
          node.owner = owner;
          node.pendingGold = Number(pendingGold) || 0;
          node.isBrass = Number(brassFlag) === 1;
          const resourceType = normalizeNodeResourceType(resourceTypeRaw);
          node.resourceType = resourceType;
          node.resourceKey = resourceType === 'gem' ? normalizeNodeResourceKey(resourceKeyRaw) : null;
          const parsedKingOwner = kingOwnerRaw == null ? null : Number(kingOwnerRaw);
          const kingOwnerId = Number.isFinite(parsedKingOwner) ? parsedKingOwner : null;
          node.kingOwnerId = kingOwnerId;
          node.isKing = kingOwnerId != null;
          let crownHealth = Number(crownHealthRaw);
          if (!Number.isFinite(crownHealth)) crownHealth = null;
          let crownMax = Number(crownMaxRaw);
          if (!Number.isFinite(crownMax)) crownMax = null;
          if (kingOwnerId != null) {
            if (Number.isFinite(crownMax) && crownMax > 0) {
              kingCrownDefaultMax = Math.max(kingCrownDefaultMax, crownMax);
            } else if (Number.isFinite(crownHealth) && crownHealth > 0) {
              crownMax = crownHealth;
            } else if (Number.isFinite(node.kingCrownMax) && node.kingCrownMax > 0) {
              crownMax = node.kingCrownMax;
            } else if (kingCrownDefaultMax > 0) {
              crownMax = kingCrownDefaultMax;
            }
            if (!Number.isFinite(crownHealth) && Number.isFinite(crownMax)) {
              crownHealth = crownMax;
            }
          } else {
            crownHealth = 0;
            crownMax = 0;
          }
          if (kingOwnerId != null && Number.isFinite(crownMax) && crownMax > 0) {
            kingCrownDefaultMax = Math.max(kingCrownDefaultMax, crownMax);
          }
          node.kingCrownMax = Number.isFinite(crownMax) ? Math.max(0, crownMax) : 0;
          node.kingCrownHealth = Number.isFinite(crownHealth) ? Math.max(0, crownHealth) : (node.kingCrownMax || 0);
          if (oldOwner !== owner) {
            // Enemy capture sound: you captured from someone else
            if (owner === myPlayerId && oldOwner != null && oldOwner !== myPlayerId) {
              playEnemyCaptureDing();
            }
            // Warning sound: you lost a node to someone else
            if (oldOwner === myPlayerId && owner != null && owner !== myPlayerId) {
              playLoseNodeWarning();
            }
          }
        }
      });
    }

    rebuildKingNodes();
    if (kingSelectionActive) {
      const currentKingNodeId = kingNodesByPlayer.get(myPlayerId);
      if (!Number.isFinite(currentKingNodeId) || currentKingNodeId !== kingSelectedNodeId) {
        clearKingSelection({ skipRedraw: true });
      }
    }

    if (Array.isArray(msg.edges)) {
      const seenEdgeIds = new Set();
      msg.edges.forEach((entry) => {
        const [rawId, on, flowing, _forward, lastTransfer, buildReq = 0, buildElap = 0, building = 0, goldFlag = 0, pipeTypeRaw = 'normal'] = entry;
        const edgeId = toEdgeId(rawId);
        if (edgeId == null) return;
        seenEdgeIds.add(edgeId);
        const edge = edges.get(edgeId);
        if (!edge) return;
        const wasFlowing = edge.flowing;
        edge.on = !!on;
        edge.flowing = !!flowing;
        edge.lastTransfer = Number(lastTransfer) || 0;
        edge.building = !!building;
        edge.buildTicksRequired = Number(buildReq || 0);
        const prevElapsed = Number(edge.buildTicksElapsed || 0);
        edge.buildTicksElapsed = Number(buildElap || 0);
        const normalizedPipeType = normalizePipeType(typeof pipeTypeRaw === 'string' ? pipeTypeRaw : (Number(goldFlag) === 1 ? 'gold' : edge.pipeType));
        edge.pipeType = normalizedPipeType;
        // Fixed-interval metronome: accumulate real time and play hits when threshold crossed
        edge.hammerAccumSec = edge.hammerAccumSec || 0;
        if (edge.building) {
          edge.hammerAccumSec += tickIntervalSec;
          while (edge.hammerAccumSec >= BRIDGE_HIT_SPACING_SEC) {
            if (edge.builtByMe) playBridgeHammerHit(edge.hammerHitIndex || 0);
            edge.hammerHitIndex = (edge.hammerHitIndex || 0) + 1;
            edge.hammerAccumSec -= BRIDGE_HIT_SPACING_SEC;
          }
        } else if (!edge.building && prevElapsed < edge.buildTicksRequired) {
          // Flush one last hit if the accumulator had built up enough (optional)
          if ((edge.hammerAccumSec || 0) >= BRIDGE_HIT_SPACING_SEC * 0.6) {
            if (edge.builtByMe) playBridgeHammerHit(edge.hammerHitIndex || 0);
            edge.hammerHitIndex = (edge.hammerHitIndex || 0) + 1;
          }
          edge.hammerAccumSec = 0;
        }
        if (!wasFlowing && edge.flowing) {
          edge.flowStartTime = animationTime;
        } else if (!edge.flowing) {
          edge.flowStartTime = null;
        }
      });

      const missingEdges = [];
      edges.forEach((edge, edgeId) => {
        if (!seenEdgeIds.has(edgeId) && edge) {
          if (edge.removing) return;
          missingEdges.push(edgeId);
        }
      });
      if (missingEdges.length) {
        missingEdges.forEach((edgeId) => forceRemoveEdge(edgeId));
      }
    }

    if (typeof msg.phase === 'string') phase = msg.phase;
    if (typeof msg.gameDuration === 'number' && Number.isFinite(msg.gameDuration) && msg.gameDuration > 0) {
      gameDuration = msg.gameDuration;
    }
    if (phase === 'playing') {
      if (typeof msg.timerRemaining === 'number') {
        syncTimerFromServer(msg.timerRemaining, msg.gameDuration);
      }
    } else if (gameStartTime) {
      hideTimerDisplay();
    }
    if (Array.isArray(msg.picked)) {
      msg.picked.forEach(([pid, picked]) => {
        if (Number(pid) === myPlayerId) myPicked = !!picked;
      });
    }
    if (sandboxModeEnabled()) {
      myPicked = true;
      phase = 'playing';
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

    syncGemCountsFromPayload(msg.gemCounts);
    updateGemCountsDisplay();

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
    if (Array.isArray(msg.autoAttack)) {
      msg.autoAttack.forEach(([pid, enabled]) => {
        if (Number(pid) === myPlayerId) {
          myAutoAttack = !!enabled;
          savePersistentAutoAttack(myAutoAttack);
          updateHomeAutoAttackToggle();
        }
      });
    }
    updateAutoExpandToggle();
    updateAutoAttackToggle();
    updateNumbersToggle();
    updateEdgeFlowToggle();

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
      clearBridgeSelection();
      hideBridgeCostDisplay();
    }

    if (statusText) {
      statusText.setText('');
      statusText.setVisible(false);
    }

    updateGoldBar();
    updateProgressBar();
    redrawStatic();
    updateSandboxButtonVisibility();
  }

  function rebuildKingNodes() {
    kingNodesByPlayer.clear();
    if (winCondition !== 'king') return;
    nodes.forEach((node, nodeId) => {
      if (node && Number.isFinite(node.kingOwnerId)) {
        kingNodesByPlayer.set(Number(node.kingOwnerId), nodeId);
      }
    });
  }

  function handleNewEdge(msg) {
    const builtByMe = Object.prototype.hasOwnProperty.call(msg, 'cost');
    const reportedCost = builtByMe
      ? (typeof msg.cost === 'number' ? msg.cost : Number(msg.cost || 0))
      : 0;
    // Add new edge to the frontend map
    applyNodeMovements(msg.nodeMovements);
    if (Array.isArray(msg.removedEdges) && msg.removedEdges.length > 0) {
      removeEdges(msg.removedEdges);
    }

    if (msg.edge) {
      const edge = msg.edge;
      const edgeId = toEdgeId(edge.id);
      if (edgeId == null) {
        redrawStatic();
        return;
      }
      const record = {
        source: edge.source,
        target: edge.target,
        on: edge.on,
        flowing: edge.flowing,
        flowStartTime: edge.flowing ? animationTime : null,
        building: !!edge.building,
        buildTicksRequired: Number(edge.buildTicksRequired || 0),
        buildTicksElapsed: Number(edge.buildTicksElapsed || 0),
        buildStartTime: animationTime,
        hammerAccumSec: 0,
        hammerHitIndex: 0,
        builtByMe,
        warpAxis: 'none',
        warpSegments: [],
        pipeType: normalizePipeType(edge.pipeType),
      };
      applyEdgeWarpData(record, edge.warp ?? edge, nodes.get(edge.source), nodes.get(edge.target));
      edges.set(edgeId, record);
      
      // Show cost indicator for bridge building
      const shouldShowCost = builtByMe && Math.abs(reportedCost) > 1e-6;
      if (activeAbility === 'bridge1way' && shouldShowCost) {
        // Position the indicator at the midpoint of the new bridge
        const sourceNode = nodes.get(edge.source);
        const targetNode = nodes.get(edge.target);
        if (sourceNode && targetNode) {
          const midPoint = getEdgeMidpointWorld(record);
          const midX = midPoint ? midPoint.x : (sourceNode.x + targetNode.x) / 2;
          const midY = midPoint ? midPoint.y : (sourceNode.y + targetNode.y) / 2;
          const [screenX, screenY] = worldToScreen(midX, midY);
          
          createMoneyIndicator(
            midX,
            midY,
            `-$${reportedCost}`,
            MONEY_SPEND_COLOR,
            2000 // 2 seconds
          );
        }
      }
      // Play bridge sound if this action was from me (server includes cost for actor)
      // No immediate sequence here; tick-driven hits will play only for builtByMe
      
      redrawStatic();
    }
    
    // Reset bridge building state only when this edge was created by me
    // Backend includes `cost` on the message only for the acting player
    if (activeAbility === 'bridge1way' && builtByMe) {
      activeAbility = null;
      clearBridgeSelection();
      hideBridgeCostDisplay();
      pendingBrassGemSpend = false;
      setBrassGemModeActive(false);
      pendingWarpGemSpend = false;
      setWarpGemModeActive(false);
      pendingRageGemSpend = false;
      setRageGemModeActive(false);
      pendingReverseGemSpend = false;
      setReverseGemModeActive(false);
    }
  }

  function handleEdgeReversed(msg) {
    // Update existing edge with new source/target after reversal
    if (msg.edge) {
      const edge = msg.edge;
      const edgeId = toEdgeId(edge.id);
      if (edgeId == null) {
        return;
      }
      const existingEdge = edges.get(edgeId);
      if (existingEdge) {
        // Update the source and target (they've been swapped)
        existingEdge.source = edge.source;
        existingEdge.target = edge.target;
        const wasFlowing = existingEdge.flowing;
        existingEdge.on = edge.on;
        existingEdge.flowing = edge.flowing;
        if (edge.pipeType) {
          existingEdge.pipeType = normalizePipeType(edge.pipeType);
        }
        if (edge.pipeType) {
          existingEdge.pipeType = normalizePipeType(edge.pipeType);
        }
        applyEdgeWarpData(existingEdge, edge.warp ?? edge, nodes.get(existingEdge.source), nodes.get(existingEdge.target));
        
        // Track flow start time for initialization animation
        if (!wasFlowing && existingEdge.flowing) {
          existingEdge.flowStartTime = animationTime;
        } else if (!existingEdge.flowing) {
          existingEdge.flowStartTime = null;
        }
        
        // Trigger reverse animation and sound (only if this action was mine)
        startEdgeReverseSpin(existingEdge);
        triggerReverseButtonPulse(edgeId);
        playReverseShuffle();
        redrawStatic();
      }
    }
  }

  function handleEdgeUpdated(msg) {
    // Update existing edge state (used for energy redirection)
    if (msg.edge) {
      const edge = msg.edge;
      const edgeId = toEdgeId(edge.id);
      if (edgeId == null) return;
      const existingEdge = edges.get(edgeId);
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
    const mapped = translateErrorMessage(msg.message, 'bridge');
    const variant = mapped.toLowerCase().includes('money') ? 'money' : 'error';
    showErrorMessage(mapped, variant);
    if (pendingWarpGemSpend) {
      pendingWarpGemSpend = false;
      if (isMagicResourceModeActive()) {
        setWarpGemModeActive(true);
      } else {
        setWarpGemModeActive(false);
      }
    }
    if (pendingBrassGemSpend) {
      pendingBrassGemSpend = false;
      if (isMagicResourceModeActive()) {
        setBrassGemModeActive(true);
      } else {
        setBrassGemModeActive(false);
      }
    }
    if (pendingRageGemSpend) {
      pendingRageGemSpend = false;
      if (isMagicResourceModeActive()) {
        setRageGemModeActive(true);
      } else {
        setRageGemModeActive(false);
      }
    }
    if (pendingReverseGemSpend) {
      pendingReverseGemSpend = false;
      if (isMagicResourceModeActive()) {
        setReverseGemModeActive(true);
      } else {
        setReverseGemModeActive(false);
      }
    }
  }

  function handleReverseEdgeError(msg) {
    // Show error message to the player
    const mapped = translateErrorMessage(msg.message, 'reverse');
    const variant = mapped.toLowerCase().includes('money') ? 'money' : 'error';
    showErrorMessage(mapped, variant);
  }

  function cleanupNodeVisuals(nodeId) {
    const juiceText = nodeJuiceTexts.get(nodeId);
    if (juiceText) {
      juiceText.destroy();
      nodeJuiceTexts.delete(nodeId);
    }
    const emojiText = nodeResourceTexts.get(nodeId);
    if (emojiText) {
      emojiText.destroy();
      nodeResourceTexts.delete(nodeId);
    }
  }

function forceRemoveEdge(edgeId) {
  const id = toEdgeId(edgeId);
  if (id == null) return;
  const existingEdge = edges.get(id);
  if (existingEdge && existingEdge.removing) {
    delete existingEdge.removing;
  }
  const label = edgeFlowTexts.get(id);
  if (label) {
    label.destroy();
    edgeFlowTexts.delete(id);
  }
  removeReverseButton(id);
  edges.delete(id);
  edgeRemovalAnimations.delete(id);
  if (hoveredEdgeId === id) {
    hoveredEdgeId = null;
  }
}

function buildRemovalSteps(count) {
  const steps = [];
  if (!Number.isFinite(count) || count <= 0) {
    return steps;
  }

  if (count % 2 === 1) {
    const center = Math.floor(count / 2);
    steps.push([center]);
    for (let offset = 1; offset <= center; offset++) {
      const left = center - offset;
      const right = center + offset;
      const step = [];
      if (left >= 0) step.push(left);
      if (right < count) step.push(right);
      if (step.length) steps.push(step);
    }
  } else {
    const leftCenter = count / 2 - 1;
    const rightCenter = count / 2;
    const firstStep = [];
    if (leftCenter >= 0) firstStep.push(leftCenter);
    if (rightCenter < count) firstStep.push(rightCenter);
    if (firstStep.length) steps.push(firstStep);
    const maxOffset = Math.max(leftCenter, count - rightCenter - 1);
    for (let offset = 1; offset <= maxOffset; offset++) {
      const step = [];
      const left = leftCenter - offset;
      const right = rightCenter + offset;
      if (left >= 0) step.push(left);
      if (right < count) step.push(right);
      if (step.length) steps.push(step);
    }
  }

  return steps;
}

function createClassicRemoval(triangleCount) {
  if (!Number.isFinite(triangleCount) || triangleCount <= 0) {
    return null;
  }
  const steps = buildRemovalSteps(triangleCount);
  if (!steps.length) {
    return null;
  }
  return {
    mode: 'classic',
    startTime: animationTime,
    stepDuration: EDGE_REMOVAL_STEP_DURATION,
    steps,
    hidden: new Array(triangleCount).fill(false),
    hiddenCount: 0,
    lastAppliedStep: -1,
    triangleCount,
    complete: false,
  };
}

function createExplosionRemoval(edge, triangleCount, options = {}) {
  if (!edge || !Number.isFinite(triangleCount) || triangleCount <= 0) {
    return null;
  }
  const worldPath = Array.isArray(options.worldPath) ? options.worldPath : [];
  if (!worldPath.length) {
    return null;
  }
  const baseScale = (Number(options.baseScale) > 0) ? Number(options.baseScale) : 1;
  const totalLength = Number(options.totalLength) || 0;
  if (!(totalLength > 0)) {
    return null;
  }

  const triWidth = Number(options.triWidth) || PIPE_TRIANGLE_WIDTH;
  const triHeight = Number(options.triHeight) || PIPE_TRIANGLE_HEIGHT;
  const actualSpacingScreen = totalLength / triangleCount;
  const actualSpacingWorld = actualSpacingScreen / baseScale;

  const settings = EDGE_REMOVAL_EXPLOSION_CONFIG || {};
  const driftPixelsMin = Number.isFinite(settings.driftPixelsMin) ? settings.driftPixelsMin : 24;
  const driftPixelsMax = Number.isFinite(settings.driftPixelsMax) ? settings.driftPixelsMax : driftPixelsMin;
  const driftDurationMin = Number.isFinite(settings.driftDurationMin) ? settings.driftDurationMin : 0.45;
  const driftDurationMax = Number.isFinite(settings.driftDurationMax) ? settings.driftDurationMax : driftDurationMin;
  const spinRotationsMin = Number.isFinite(settings.spinRotationsMin) ? settings.spinRotationsMin : 3;
  const spinRotationsMax = Number.isFinite(settings.spinRotationsMax) ? settings.spinRotationsMax : spinRotationsMin;
  const greyDelayDefault = Math.max(0, Number(settings.greyDelay) || 0);
  const restDuration = Math.max(0, Number(settings.restDuration) || 5);
  const fadeDuration = Math.max(0, Number(settings.fadeDuration) || 0.6);
  const greyColor = Number.isFinite(settings.greyColor) ? settings.greyColor : 0xf0f0f0;
  const driftAlphaStart = Number.isFinite(settings.alphaDuringDriftStart) ? settings.alphaDuringDriftStart : 0.55;
  const driftAlphaEnd = Number.isFinite(settings.alphaDuringDriftEnd) ? settings.alphaDuringDriftEnd : 0.95;
  const restAlpha = Number.isFinite(settings.alphaAtRest) ? settings.alphaAtRest : 0.65;
  const driftLighten = Number.isFinite(settings.driftLightenFactor) ? settings.driftLightenFactor : 0.25;

  const particles = [];
  let maxDriftPhaseDuration = 0;
  for (let i = 0; i < triangleCount; i++) {
    const distanceWorld = (i + 0.5) * actualSpacingWorld;
    const point = samplePointOnPath(worldPath, distanceWorld);
    if (!point) continue;
    const tangent = point.seg ? { x: point.seg.ux, y: point.seg.uy } : { x: Math.cos(point.angle), y: Math.sin(point.angle) };
    let nx = -tangent.y;
    let ny = tangent.x;
    const tangentLen = Math.hypot(tangent.x, tangent.y) || 1;
    const normalLen = Math.hypot(nx, ny) || 1;
    const ux = tangent.x / tangentLen;
    const uy = tangent.y / tangentLen;
    nx /= normalLen;
    ny /= normalLen;

    const randomAngle = Math.random() * Math.PI * 2;
    const randX = Math.cos(randomAngle);
    const randY = Math.sin(randomAngle);
    const normalSign = Math.random() < 0.5 ? -1 : 1;
    const normalStrength = 0.5 + Math.random() * 0.8;
    const tangentStrength = (Math.random() - 0.5) * 0.5;
    let dirX = nx * normalStrength * normalSign + ux * tangentStrength + randX * 0.25;
    let dirY = ny * normalStrength * normalSign + uy * tangentStrength + randY * 0.25;
    const dirLen = Math.hypot(dirX, dirY) || 1;
    dirX /= dirLen;
    dirY /= dirLen;

    const driftPixels = randomBetween(driftPixelsMin, driftPixelsMax);
    const driftWorldDistance = driftPixels / baseScale;
    const driftDuration = randomBetween(driftDurationMin, driftDurationMax);
    const greyDelay = greyDelayDefault;
    const driftPhaseDuration = driftDuration + greyDelay;
    if (driftPhaseDuration > maxDriftPhaseDuration) {
      maxDriftPhaseDuration = driftPhaseDuration;
    }

    const rotations = randomBetween(spinRotationsMin, spinRotationsMax);
    const direction = Math.random() < 0.5 ? -1 : 1;
    const spinAmount = direction * rotations * Math.PI * 2;

    particles.push({
      index: i,
      startX: point.x,
      startY: point.y,
      startAngle: point.angle,
      offsetX: dirX * driftWorldDistance,
      offsetY: dirY * driftWorldDistance,
      driftDuration: Math.max(0.05, driftDuration),
      spinAmount,
      greyDelay,
      triWidth,
      triHeight,
    });
  }

  if (!particles.length) {
    return null;
  }

  return {
    mode: 'explosion',
    startTime: animationTime,
    triangleCount,
    particles,
    triWidth,
    triHeight,
    driftMaxDuration: maxDriftPhaseDuration,
    restDuration,
    fadeDuration,
    greyColor,
    actualSpacing: actualSpacingScreen,
    totalLength,
    driftAlphaStart,
    driftAlphaEnd,
    restAlpha,
    driftLighten,
    complete: false,
  };
}

function beginEdgeRemoval(edgeId) {
  const id = toEdgeId(edgeId);
  if (id == null) return;
  if (edgeRemovalAnimations.has(id)) return;

  const edge = edges.get(id);
  if (!edge) {
    forceRemoveEdge(id);
    return;
  }

  const sourceNode = nodes.get(edge.source);
  const targetNode = nodes.get(edge.target);

  let baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
  if (!(baseScale > 0)) baseScale = 1;
  const fromRadiusScreen = sourceNode ? Math.max(1, calculateNodeRadius(sourceNode, baseScale)) + 1 : 0;
  const toRadiusScreen = targetNode ? Math.max(1, calculateNodeRadius(targetNode, baseScale)) + 1 : 0;
  const path = buildEdgeScreenPath(edge, sourceNode, targetNode, fromRadiusScreen, toRadiusScreen);

  const totalLength = path.reduce((sum, seg) => sum + seg.length, 0);
  const triH = PIPE_TRIANGLE_HEIGHT;
  const triangleCount = Math.max(1, Math.floor(totalLength / triH));

  if (!Number.isFinite(totalLength) || totalLength <= 0 || !Number.isFinite(triangleCount)) {
    forceRemoveEdge(id);
    return;
  }

  const removalMode = EDGE_REMOVAL_ANIMATION_MODE === 'classic' ? 'classic' : 'explosion';
  let removal = null;

  if (removalMode === 'explosion') {
    const fromRadiusWorld = fromRadiusScreen / baseScale;
    const toRadiusWorld = toRadiusScreen / baseScale;
    const worldPath = buildEdgeWorldPath(edge, sourceNode, targetNode, fromRadiusWorld, toRadiusWorld);
    removal = createExplosionRemoval(edge, triangleCount, {
      worldPath,
      baseScale,
      totalLength,
      triWidth: PIPE_TRIANGLE_WIDTH,
      triHeight: triH,
    });
  }

  if (!removal) {
    removal = createClassicRemoval(triangleCount);
  }

  if (!removal) {
    forceRemoveEdge(id);
    return;
  }

  if (removal.mode === 'explosion') {
    removal.baseColor = edgeColor(edge, sourceNode);
  }

  edge.on = false;
  edge.flowing = false;
  edge.lastTransfer = 0;
  edge.flowStartTime = null;
  edge.building = false;
  edge.buildTicksElapsed = 0;
  edge.buildTicksRequired = 0;
  if (edge._spin) delete edge._spin;
  edge.removing = removal;

  const label = edgeFlowTexts.get(id);
  if (label) {
    label.destroy();
    edgeFlowTexts.delete(id);
  }

  if (hoveredEdgeId === id) {
    hoveredEdgeId = null;
  }

  edgeRemovalAnimations.set(id, removal);
  redrawStatic();
}

function applyRemovalSteps(removal, triangleCount) {
  if (!removal || removal.mode !== 'classic') return null;

  if (!Number.isFinite(triangleCount) || triangleCount <= 0) {
    removal.complete = true;
    return removal.hidden;
  }

  if (removal.triangleCount !== triangleCount) {
    removal.triangleCount = triangleCount;
    removal.steps = buildRemovalSteps(triangleCount);
    removal.hidden = new Array(triangleCount).fill(false);
    removal.hiddenCount = 0;
    removal.lastAppliedStep = -1;
    if (!removal.steps.length) {
      removal.complete = true;
      return removal.hidden;
    }
  }

  const elapsed = Math.max(0, animationTime - removal.startTime);
  const stepsToApply = Math.min(removal.steps.length, Math.floor(elapsed / removal.stepDuration) + 1);
  const targetStepIndex = stepsToApply - 1;

  if (targetStepIndex > removal.lastAppliedStep) {
    for (let s = removal.lastAppliedStep + 1; s <= targetStepIndex; s++) {
      const indices = removal.steps[s];
      if (!indices) continue;
      for (const idx of indices) {
        if (idx >= 0 && idx < removal.hidden.length && !removal.hidden[idx]) {
          removal.hidden[idx] = true;
          removal.hiddenCount += 1;
        }
      }
    }
    removal.lastAppliedStep = targetStepIndex;
  }

  if (removal.hiddenCount >= removal.triangleCount) {
    removal.complete = true;
  }

  return removal.hidden;
}

function finalizeEdgeRemoval(edgeId) {
  const id = toEdgeId(edgeId);
  if (id == null) return;
  const edge = edges.get(id);
  if (edge && edge.removing) {
    delete edge.removing;
  }
  edgeRemovalAnimations.delete(id);
  forceRemoveEdge(id);
}

function removeEdges(edgeIds, options = {}) {
  if (!Array.isArray(edgeIds)) return;
  const seen = new Set();
  const immediate = Boolean(options.immediate);
  edgeIds.forEach((edgeId) => {
    const id = toEdgeId(edgeId);
    if (id == null || seen.has(id)) return;
    seen.add(id);
    if (immediate) {
      finalizeEdgeRemoval(id);
    } else {
      beginEdgeRemoval(id);
    }
  });
}

function updateEdgeRemovalAnimations() {
  if (edgeRemovalAnimations.size === 0) return false;
  const finalizeIds = [];
  let anyActive = false;
  edgeRemovalAnimations.forEach((removal, edgeId) => {
    if (removal && removal.mode === 'explosion') {
      const totalDuration = (removal.driftMaxDuration || 0) + (removal.restDuration || 0) + (removal.fadeDuration || 0);
      if (totalDuration > 0 && animationTime - removal.startTime >= totalDuration) {
        removal.complete = true;
      }
    }
    if (removal.complete) {
      finalizeIds.push(edgeId);
    } else {
      anyActive = true;
    }
  });
  for (const edgeId of finalizeIds) {
    finalizeEdgeRemoval(edgeId);
  }
  return anyActive || finalizeIds.length > 0;
}

function handleRemoveEdges(msg) {
  if (!msg) return;
  let ids = [];
  if (Array.isArray(msg.edgeIds)) {
    ids = msg.edgeIds;
  } else if (Array.isArray(msg.removedEdges)) {
    ids = msg.removedEdges;
  }
  if (!ids.length) return;
  removeEdges(ids);
  redrawStatic();
}

function fallbackRemoveEdgesForNode(nodeId) {
  const edgeIds = [];
  edges.forEach((edge, edgeId) => {
    if (edge.source === nodeId || edge.target === nodeId) {
      edgeIds.push(edgeId);
    }
  });
  removeEdges(edgeIds);
}

  function handleNodeDestroyed(msg) {
    const nodeId = Number(msg.nodeId);
    if (!Number.isFinite(nodeId)) return;

    const nodeSnapshot = msg.nodeSnapshot || nodes.get(nodeId);

    if (nodes.has(nodeId)) {
      nodes.delete(nodeId);
    }
    cleanupNodeVisuals(nodeId);

    if (Array.isArray(msg.removedEdges)) {
      removeEdges(msg.removedEdges);
    } else {
      fallbackRemoveEdgesForNode(nodeId);
    }

    if (msg.playerId === myPlayerId && Number.isFinite(msg.cost) && msg.cost > 0 && nodeSnapshot) {
      const scale = view ? Math.min(view.scaleX, view.scaleY) : 1;
      const radiusPx = calculateNodeRadius(nodeSnapshot, scale || 1);
      const radiusWorld = (scale && scale > 0) ? radiusPx / scale : 0;
      const worldOffset = persistentNumbers ? -(radiusWorld + 0.6) : 0;
      createMoneyIndicator(
        nodeSnapshot.x,
        nodeSnapshot.y,
        `-$${formatCost(msg.cost)}`,
        MONEY_SPEND_COLOR,
        1800,
        { worldOffset, floatDistance: 26 }
      );
    }

    rebuildKingNodes();
    redrawStatic();

    // Reset destroy mode on successful node destruction
    if (activeAbility === 'destroy') {
      activeAbility = null;
      clearBridgeSelection();
    }
  }

  function handleDestroyError(msg) {
    // Show error message to the player
    showErrorMessage(msg.message || "Can't destroy this node!");
  }

  function handleNukeError(msg) {
    showErrorMessage(msg.message || "Can't nuke this node!");
  }

  function handleSandboxNodeCreated(msg) {
    if (!msg || !msg.node) return;
    if (typeof msg.winCondition === 'string') {
      winCondition = normalizeWinCondition(msg.winCondition);
    }
    const data = msg.node;
    const id = Number(data.id);
    if (!Number.isFinite(id)) return;
    const x = Number(data.x) || 0;
    const y = Number(data.y) || 0;
    const parsedSize = Number(data.size);
    const size = Number.isFinite(parsedSize) ? parsedSize : 0;
    const pendingGold = Number(data.pendingGold) || 0;
    const owner = (data.owner == null) ? null : Number(data.owner);
    const isBrass = data.isBrass === true || Number(data.isBrass) === 1;
    const resourceType = normalizeNodeResourceType(data.resourceType);
    const resourceKey = resourceType === 'gem' ? normalizeNodeResourceKey(data.resourceKey) : null;

    const existingText = nodeJuiceTexts.get(id);
    if (existingText) {
      existingText.destroy();
      nodeJuiceTexts.delete(id);
    }
    const existingEmoji = nodeResourceTexts.get(id);
    if (existingEmoji) {
      existingEmoji.destroy();
      nodeResourceTexts.delete(id);
    }

    nodes.set(id, {
      x,
      y,
      startX: x,
      startY: y,
      targetX: x,
      targetY: y,
      moveStartTime: null,
      moveDuration: null,
      size,
      owner,
      pendingGold,
      isBrass,
      kingOwnerId: null,
      isKing: false,
      resourceType,
      resourceKey,
    });

    if (typeof msg.totalNodes === 'number') {
      totalNodes = Number(msg.totalNodes);
    }
    if (typeof msg.winThreshold === 'number') {
      winThreshold = Number(msg.winThreshold);
    }

    rebuildKingNodes();

    redrawStatic();
    updateProgressBar();
  }

  function handleSandboxBoardCleared(msg) {
    if (typeof msg?.winCondition === 'string') {
      winCondition = normalizeWinCondition(msg.winCondition);
    } else {
      winCondition = 'dominate';
    }
    clearKingSelection({ skipRedraw: true });
    nodeJuiceTexts.forEach((text) => {
      if (text) text.destroy();
    });
    nodeJuiceTexts.clear();
    nodeResourceTexts.forEach((text) => {
      if (text) text.destroy();
    });
    nodeResourceTexts.clear();
    edgeFlowTexts.forEach((text) => {
      if (text) text.destroy();
    });
    edgeFlowTexts.clear();
    edges.clear();
    nodes.clear();
    kingNodesByPlayer.clear();
    hoveredNodeId = null;
    hoveredEdgeId = null;
    currentTargetNodeId = null;
    currentTargetSetTime = null;
    activeAbility = null;
    clearBridgeSelection();
    hideBridgeCostDisplay();
    edgeRemovalAnimations.clear();
    brassPreviewIntersections.clear();
    clearAllPrePipes('sandbox', { skipRedraw: true });
    moneyIndicators = [];
    playerStats.forEach((stats) => {
      if (stats) stats.nodes = 0;
    });

    if (typeof msg.totalNodes === 'number') {
      totalNodes = Number(msg.totalNodes);
    } else {
      totalNodes = 0;
    }
    if (typeof msg.winThreshold === 'number') {
      winThreshold = Number(msg.winThreshold);
    } else {
      winThreshold = 0;
    }

    rebuildKingNodes();
    updateGoldBar();
    updateProgressBar();
    redrawStatic();
  }

  function handleSandboxError(msg) {
    const message = (msg && typeof msg.message === 'string') ? msg.message : 'Sandbox action failed';
    showErrorMessage(message);
  }

  function clearKingSelection(options = {}) {
    const { skipRedraw = false } = options || {};
    kingSelectionActive = false;
    kingSelectedNodeId = null;
    kingMoveTargets.clear();
    kingMoveTargetsList = [];
    kingMoveOptionsPending = false;
    kingMoveTargetRenderInfo.clear();
    kingMoveTargetHoveredId = null;
    kingMovePendingDestinationId = null;
    if (!skipRedraw) {
      redrawStatic();
    }
  }

  function startKingSelection(nodeId) {
    if (!Number.isFinite(nodeId)) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const token = localStorage.getItem('token');
    if (!token) return;

    kingSelectionActive = true;
    kingSelectedNodeId = nodeId;
    kingMoveTargets.clear();
    kingMoveTargetsList = [];
    kingMoveOptionsPending = true;
    kingMoveTargetRenderInfo.clear();
    kingMoveTargetHoveredId = null;
    kingMovePendingDestinationId = null;
    redrawStatic();

    ws.send(JSON.stringify({
      type: 'kingRequestMoves',
      originNodeId: nodeId,
      token,
    }));
  }

  function sendKingMoveRequest(nodeId) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    const token = localStorage.getItem('token');
    if (!token) return false;

    if (kingMovePendingDestinationId != null) return false;

    kingMovePendingDestinationId = nodeId;
    kingMoveOptionsPending = true;
    redrawStatic();

    ws.send(JSON.stringify({
      type: 'kingMove',
      destinationNodeId: nodeId,
      token,
    }));
    return true;
  }

  function handleKingMoveOptions(msg) {
    if (!kingSelectionActive) return;
    const originId = Number(msg?.originNodeId);
    if (!Number.isFinite(originId) || originId !== kingSelectedNodeId) {
      return;
    }

    const rawTargets = Array.isArray(msg?.targets) ? msg.targets : [];
    const targets = [];
    rawTargets.forEach((value) => {
      const id = Number(value);
      if (Number.isFinite(id) && id !== kingSelectedNodeId) {
        targets.push(id);
      }
    });

    kingMoveTargets.clear();
    kingMoveTargetsList = targets;
    targets.forEach((id) => kingMoveTargets.add(id));
    kingMoveOptionsPending = false;
    kingMovePendingDestinationId = null;
    kingMoveTargetHoveredId = null;
    redrawStatic();
  }

  function handleKingMoveError(msg) {
    const message = (msg && typeof msg.message === 'string') ? msg.message : 'Unable to move king';
    showErrorMessage(message);
    kingMovePendingDestinationId = null;
    clearKingSelection();
  }

  function handleKingMoved(msg) {
    const playerId = Number(msg?.playerId);
    const fromNodeId = Number(msg?.fromNodeId);
    const toNodeId = Number(msg?.toNodeId);
    const crownHealthRaw = msg?.crownHealth;
    const crownMaxRaw = msg?.crownMax;
    let crownHealth = Number(crownHealthRaw);
    let crownMax = Number(crownMaxRaw);
    if (!Number.isFinite(crownHealth)) crownHealth = null;
    if (!Number.isFinite(crownMax)) crownMax = null;
    if (!Number.isFinite(playerId) || !Number.isFinite(toNodeId)) {
      return;
    }

    if (Number.isFinite(fromNodeId)) {
      const sourceNode = nodes.get(fromNodeId);
      if (sourceNode) {
        sourceNode.kingOwnerId = null;
        sourceNode.isKing = false;
        sourceNode.kingCrownHealth = 0;
        sourceNode.kingCrownMax = 0;
      }
    }

    const targetNode = nodes.get(toNodeId);
    if (targetNode) {
      targetNode.kingOwnerId = playerId;
      targetNode.isKing = true;
      let resolvedCrownMax = crownMax;
      let resolvedCrownHealth = crownHealth;
      if (!Number.isFinite(resolvedCrownMax) || resolvedCrownMax <= 0) {
        if (Number.isFinite(targetNode.kingCrownMax) && targetNode.kingCrownMax > 0) {
          resolvedCrownMax = targetNode.kingCrownMax;
        } else if (kingCrownDefaultMax > 0) {
          resolvedCrownMax = kingCrownDefaultMax;
        } else if (Number.isFinite(resolvedCrownHealth) && resolvedCrownHealth > 0) {
          resolvedCrownMax = resolvedCrownHealth;
        }
      }
      if (!Number.isFinite(resolvedCrownHealth) || resolvedCrownHealth < 0) {
        if (Number.isFinite(crownHealth)) {
          resolvedCrownHealth = crownHealth;
        } else if (Number.isFinite(resolvedCrownMax)) {
          resolvedCrownHealth = resolvedCrownMax;
        } else if (Number.isFinite(targetNode.kingCrownHealth)) {
          resolvedCrownHealth = targetNode.kingCrownHealth;
        } else {
          resolvedCrownHealth = 0;
        }
      }
      if (Number.isFinite(resolvedCrownMax) && resolvedCrownMax > 0) {
        kingCrownDefaultMax = Math.max(kingCrownDefaultMax, resolvedCrownMax);
      }
      targetNode.kingCrownMax = Number.isFinite(resolvedCrownMax) ? Math.max(0, resolvedCrownMax) : 0;
      targetNode.kingCrownHealth = Number.isFinite(resolvedCrownHealth) ? Math.max(0, resolvedCrownHealth) : targetNode.kingCrownMax;
    }

    kingMovePendingDestinationId = null;
    kingNodesByPlayer.set(playerId, toNodeId);
    if (playerId === myPlayerId) {
      clearKingSelection({ skipRedraw: true });
    }

    rebuildKingNodes();
    redrawStatic();
  }

  function handleNodeCaptured(msg) {
    if (!msg || msg.nodeId == null) return;
    const node = nodes.get(msg.nodeId);
    if (!node) return;

    const offsetX = 2;
    const offsetY = -2;
    const rewardTypeRaw = typeof msg.rewardType === 'string' ? msg.rewardType.trim().toLowerCase() : null;
    const rewardKeyRaw = typeof msg.rewardKey === 'string' ? msg.rewardKey : null;
    const normalizedRewardType = normalizeNodeResourceType(rewardTypeRaw === 'gem' ? 'gem' : 'money');
    const neutralCaptureEnabled = coerceNeutralCaptureReward(selectedSettings.neutralCaptureGold) > 0;

    if (normalizedRewardType === 'gem') {
      const normalizedKey = normalizeNodeResourceKey(rewardKeyRaw);
      const emoji = getResourceEmoji('gem', normalizedKey);
      const keyLabel = normalizedKey
        ? normalizedKey.charAt(0).toUpperCase() + normalizedKey.slice(1)
        : 'Gem';
      const indicatorText = emoji ? `+${emoji} ${keyLabel}` : `+${keyLabel}`;
      createMoneyIndicator(
        node.x + offsetX,
        node.y + offsetY,
        indicatorText,
        MONEY_GAIN_COLOR,
        2200,
        { strokeColor: '#264a2a' }
      );
      playCaptureDing();
      return;
    }

    if (!neutralCaptureEnabled) {
      return;
    }

    const rewardValue = Number(msg.reward);
    if (!Number.isFinite(rewardValue) || rewardValue <= 0) {
      return;
    }

    createMoneyIndicator(
      node.x + offsetX,
      node.y + offsetY,
      `+$${formatCost(rewardValue)}`,
      MONEY_GAIN_COLOR,
      2000
    );

    if (rewardValue >= 10) {
      playCaptureDing();
    } else {
      playEnemyCaptureDing();
    }
  }

  function handleNodeOverflowPayout(msg) {
    if (!msg || typeof msg.nodeId === 'undefined') return;
    const nodeId = Number(msg.nodeId);
    const amount = Number(msg.amount);
    if (!Number.isFinite(nodeId) || !Number.isFinite(amount) || amount <= 0) return;
    const node = nodes.get(nodeId);
    if (!node) return;
    const offsetX = 2;
    const offsetY = -2;
    createMoneyIndicator(
      node.x + offsetX,
      node.y + offsetY,
      `+$${formatCost(amount)}`,
      MONEY_GAIN_COLOR,
      2000
    );
    playChaChing();
  }

  function showErrorMessage(message, variant = 'error') {
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
    if (variant === 'money') {
      errorMsg.style.background = MONEY_SPEND_COLOR;
      errorMsg.style.color = '#111111';
      errorMsg.style.border = `2px solid ${MONEY_SPEND_STROKE}`;
    } else {
      errorMsg.style.background = 'rgba(255, 0, 0, 0.9)';
      errorMsg.style.color = 'white';
      errorMsg.style.border = '2px solid #ff0000';
    }
    errorMsg.style.display = 'block';
    
    // Auto-hide after 2 seconds
    setTimeout(() => {
      errorMsg.style.display = 'none';
    }, 2000);
  }

  function translateErrorMessage(message, context) {
    const original = (message || '').toString();
    const lower = original.toLowerCase();
    if (lower.includes('not enough gold')) return 'Not enough money';
    if (lower.includes('intersect')) return 'No overlapping pipes';
    if (lower.includes('only golden pipes can cross')) return 'Only brass pipes can cross';
    if (lower.includes('cannot cross golden pipe')) return 'Brass pipes cannot be crossed';
    if (lower.includes('warp gem')) return 'Warp gem required to warp pipes';
    if (
      lower.includes('must control brass pipes') ||
      lower.includes('must control pipe start') ||
      lower.includes('pipes must start')
    ) {
      return 'Pipes must start from your nodes';
    }
    if (lower.includes('pipe controlled')) return 'Pipe controlled by Opponent';
    // Fallbacks per context
    if (context === 'bridge') return original || 'Invalid Pipe!';
    if (context === 'reverse') return original || "Can't reverse this pipe!";
    return original || 'Error';
  }

  function createKingCrownLayout(centerX, baseBottomY, radius) {
    const crownRadius = Math.max(1, radius);
    const bodyHeight = Math.max(38, crownRadius * 3.0);
    const stemHeight = Math.max(12, bodyHeight * 0.36);
    const bowlHeight = bodyHeight - stemHeight;
    const bottomWidth = Math.max(10, crownRadius * 1.2);
    const stemWidth = Math.max(bottomWidth + 8, crownRadius * 1.4);
    const topWidth = Math.max(stemWidth + 22, crownRadius * 1.75);
    const spikeHeight = Math.max(12, crownRadius * 0.75);
    const topBandY = baseBottomY - bodyHeight;
    const stemTopY = baseBottomY - stemHeight;
    const topPeakY = topBandY - spikeHeight;
    const paddingX = Math.max(12, crownRadius * 0.5);
    const paddingY = Math.max(12, bodyHeight * 0.2);

    const bottomLeft = { x: centerX - bottomWidth / 2, y: baseBottomY };
    const bottomRight = { x: centerX + bottomWidth / 2, y: baseBottomY };
    const stemRight = { x: centerX + stemWidth / 2, y: stemTopY };
    const stemLeft = { x: centerX - stemWidth / 2, y: stemTopY };
    const topRight = { x: centerX + topWidth / 2, y: topBandY };
    const topLeft = { x: centerX - topWidth / 2, y: topBandY };

    const outlinePoints = [bottomLeft, bottomRight, stemRight, topRight];
    const bodyOutlinePoints = [
      { x: bottomLeft.x, y: bottomLeft.y },
      { x: bottomRight.x, y: bottomRight.y },
      { x: stemRight.x, y: stemRight.y },
      { x: topRight.x, y: topRight.y },
      { x: topLeft.x, y: topLeft.y },
      { x: stemLeft.x, y: stemLeft.y },
    ];
    const spikeTriangles = [];

    const spikeCount = 3;
    const segmentWidth = topWidth / spikeCount;
    let currentRightX = topRight.x;
    for (let i = 0; i < spikeCount; i += 1) {
      const nextLeftX = currentRightX - segmentWidth;
      const tip = { x: currentRightX - segmentWidth / 2, y: topPeakY };
      const rightBase = { x: currentRightX, y: topBandY };
      const leftBase = { x: nextLeftX, y: topBandY };
      outlinePoints.push(tip, leftBase);
      spikeTriangles.push({
        tip,
        rightBase,
        leftBase,
      });
      currentRightX = nextLeftX;
    }
    outlinePoints.push(stemLeft);

    const widthAt = (y) => {
      if (y >= stemTopY) {
        const progress = (baseBottomY - y) / Math.max(stemHeight, 1e-6);
        return bottomWidth + (stemWidth - bottomWidth) * Math.max(0, Math.min(1, progress));
      }
      const progress = (stemTopY - y) / Math.max(bowlHeight, 1e-6);
      return stemWidth + (topWidth - stemWidth) * Math.max(0, Math.min(1, progress));
    };

    const bounds = {
      left: centerX - topWidth / 2 - paddingX,
      right: centerX + topWidth / 2 + paddingX,
      top: topPeakY - paddingY,
      bottom: baseBottomY + paddingY,
    };

    return {
      centerX,
      centerY: (topPeakY + baseBottomY) / 2,
      baseBottomY,
      stemTopY,
      topBandY,
      topPeakY,
      bottomWidth,
      stemWidth,
      topWidth,
      spikeHeight,
      outlinePoints,
      bodyOutlinePoints,
      spikes: spikeTriangles,
      paddingX,
      paddingY,
      fillableHeight: baseBottomY - topBandY,
      height: baseBottomY - topPeakY,
      widthAt,
      bounds,
      hitRadius: Math.max(topWidth / 2 + paddingX, (baseBottomY - topPeakY) / 2 + paddingY),
    };
  }

  function buildCrownFillPolygon(layout, ratio) {
    if (!layout) return null;
    const clamped = Math.max(0, Math.min(1, Number(ratio) || 0));
    if (clamped <= 0) return null;
    const {
      centerX,
      baseBottomY,
      stemTopY,
      topBandY,
      bottomWidth,
      stemWidth,
      widthAt,
      fillableHeight,
    } = layout;
    const effectiveHeight = Math.max(fillableHeight || (baseBottomY - topBandY), 1e-6);
    let fillTopY = baseBottomY - effectiveHeight * clamped;
    fillTopY = Math.min(baseBottomY, Math.max(topBandY, fillTopY));

    const points = [];
    points.push({ x: centerX - bottomWidth / 2, y: baseBottomY });
    points.push({ x: centerX + bottomWidth / 2, y: baseBottomY });

    if (fillTopY >= stemTopY) {
      const half = (widthAt ? widthAt(fillTopY) : stemWidth) / 2;
      points.push({ x: centerX + half, y: fillTopY });
      points.push({ x: centerX - half, y: fillTopY });
    } else {
      const stemHalf = stemWidth / 2;
      const upperHalf = (widthAt ? widthAt(fillTopY) : stemWidth) / 2;
      points.push({ x: centerX + stemHalf, y: stemTopY });
      points.push({ x: centerX + upperHalf, y: fillTopY });
      points.push({ x: centerX - upperHalf, y: fillTopY });
      points.push({ x: centerX - stemHalf, y: stemTopY });
    }

    points.push({ x: centerX - bottomWidth / 2, y: baseBottomY });
    return points;
  }

  function fillCrownPolygon(points, color, alpha) {
    if (!graphicsNodes || !points || points.length < 3) return;
    const useAlpha = Number.isFinite(alpha) ? alpha : 1;
    if (useAlpha <= 0) return;
    graphicsNodes.fillStyle(color, useAlpha);
    graphicsNodes.beginPath();
    graphicsNodes.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length; i += 1) {
      graphicsNodes.lineTo(points[i].x, points[i].y);
    }
    graphicsNodes.closePath();
    graphicsNodes.fillPath();
  }

  function strokeCrownPolygon(points, width, color, alpha) {
    if (!graphicsNodes || !points || points.length < 2) return;
    const useAlpha = Number.isFinite(alpha) ? alpha : 1;
    if (useAlpha <= 0) return;
    graphicsNodes.lineStyle(width, color, useAlpha);
    graphicsNodes.beginPath();
    graphicsNodes.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length; i += 1) {
      graphicsNodes.lineTo(points[i].x, points[i].y);
    }
    graphicsNodes.closePath();
    graphicsNodes.strokePath();
  }

  function computePlacedKingCrownLayout(screenX, screenY, crownRadius) {
    const radius = Math.max(1, crownRadius);
    const nodeRadiusEquivalent = Math.max(1, radius / KING_CROWN_TO_NODE_RATIO);
    const verticalOffset = Math.max(6, nodeRadiusEquivalent * 0.35);
    const baseBottomY = screenY - nodeRadiusEquivalent - verticalOffset;
    const layout = createKingCrownLayout(screenX, baseBottomY, radius);
    layout.nodeRadius = nodeRadiusEquivalent;
    return layout;
  }

  function isPointWithinRect(px, py, rect) {
    if (!rect) return false;
    return px >= rect.left && px <= rect.right && py >= rect.top && py <= rect.bottom;
  }

  function drawKingCrown(nx, ny, radius, ownerColor, options = {}) {
    if (!graphicsNodes) return null;
    const {
      highlighted = false,
      highlightColor: overrideHighlightColor,
      crownHealth: crownHealthOpt = null,
      crownMax: crownMaxOpt = null,
      nodeId = null,
    } = options;
    const layout = computePlacedKingCrownLayout(nx, ny, radius);
    if (!layout) return null;

    const strokeColor = Number.isFinite(ownerColor) ? ownerColor : 0x000000;
    const primaryColor = KING_CROWN_FILL_COLOR;
    const highlightColor = Number.isFinite(overrideHighlightColor) ? overrideHighlightColor : primaryColor;
    const fillColor = highlighted ? highlightColor : primaryColor;
    const baseHex = `#${primaryColor.toString(16).padStart(6, '0')}`;
    const emptyFillColor = hexToInt(lightenColor(baseHex, 0.35));
    const outlineWidth = highlighted ? 3.0 : 2.4;
    const outlineAlpha = highlighted ? 0.98 : 0.9;
    const emptyFillAlpha = highlighted ? 0.3 : 0.18;
    const liquidFillAlpha = highlighted ? 0.96 : 0.85;

    let crownMaxValue = Number.isFinite(crownMaxOpt) ? Math.max(0, crownMaxOpt) : 0;
    let crownHealthValue = Number.isFinite(crownHealthOpt) ? Math.max(0, crownHealthOpt) : NaN;
    if ((crownMaxValue <= 0 || !Number.isFinite(crownMaxValue)) && Number.isFinite(crownHealthValue) && crownHealthValue > 0) {
      crownMaxValue = crownHealthValue;
    }
    if (!Number.isFinite(crownHealthValue)) {
      crownHealthValue = crownMaxValue;
    }
    const coreFillRatio = crownMaxValue > 0 ? Math.max(0, Math.min(1, crownHealthValue / crownMaxValue)) : 0;
    const bodyHeight = Math.max(layout.fillableHeight || 0, 0);
    const spikeHeight = Math.max(layout.spikeHeight || 0, 0);
    const totalHeight = bodyHeight + spikeHeight;
    let bodyFillRatio = 0;
    let tipFillRatio = 0;
    if (totalHeight > 0) {
      const filledHeight = Math.max(0, Math.min(totalHeight, totalHeight * coreFillRatio));
      if (bodyHeight > 0) {
        bodyFillRatio = Math.min(1, filledHeight / bodyHeight);
      }
      if (filledHeight > bodyHeight && spikeHeight > 1e-6) {
        tipFillRatio = Math.min(1, (filledHeight - bodyHeight) / spikeHeight);
      }
    }

    const bodyOutline = layout.bodyOutlinePoints || layout.outlinePoints;
    if (bodyOutline) {
      fillCrownPolygon(bodyOutline, emptyFillColor, emptyFillAlpha);
    }

    if (bodyFillRatio > 0) {
      const fillPoints = buildCrownFillPolygon(layout, Math.min(1, bodyFillRatio));
      if (fillPoints) {
        fillCrownPolygon(fillPoints, fillColor, liquidFillAlpha);
      }
    }

    if (tipFillRatio > 0 && Array.isArray(layout.spikes)) {
      const clampedTip = Math.min(1, tipFillRatio);
      layout.spikes.forEach((spike) => {
        if (!spike || !spike.tip || !spike.leftBase || !spike.rightBase) return;
        const { tip, leftBase, rightBase } = spike;
        if (clampedTip >= 0.999) {
          fillCrownPolygon(
            [
              { x: leftBase.x, y: leftBase.y },
              { x: rightBase.x, y: rightBase.y },
              { x: tip.x, y: tip.y },
            ],
            fillColor,
            liquidFillAlpha
          );
          return;
        }
        const rightInterp = {
          x: rightBase.x + clampedTip * (tip.x - rightBase.x),
          y: rightBase.y + clampedTip * (tip.y - rightBase.y),
        };
        const leftInterp = {
          x: leftBase.x + clampedTip * (tip.x - leftBase.x),
          y: leftBase.y + clampedTip * (tip.y - leftBase.y),
        };
        fillCrownPolygon(
          [
            { x: leftBase.x, y: leftBase.y },
            { x: rightBase.x, y: rightBase.y },
            rightInterp,
            leftInterp,
          ],
          fillColor,
          liquidFillAlpha
        );
      });
    }

    strokeCrownPolygon(layout.outlinePoints, outlineWidth, strokeColor, outlineAlpha);

    if (highlighted && Number.isFinite(overrideHighlightColor)) {
      strokeCrownPolygon(
        layout.outlinePoints,
        Math.max(1, outlineWidth - 1),
        overrideHighlightColor,
        0.5
      );
    }

    // Display crown health text inside the crown near the top
    if (sceneRef && nodeId != null && Number.isFinite(crownHealthValue)) {
      const textX = layout.centerX || nx;
      const textY = (layout.bounds?.top || ny) + 28; // Position inside the crown near the top
      
      let crownHealthDisplay = crownHealthDisplays.get(nodeId);
      
      if (crownHealthValue > 0) {
        // Display health number
        const healthText = Math.ceil(crownHealthValue).toString();
        
        if (!crownHealthDisplay) {
          crownHealthDisplay = sceneRef.add.text(textX, textY, healthText, {
            fontFamily: 'monospace',
            fontSize: '14px',
            fontStyle: 'bold',
            color: '#000000',
          })
          .setOrigin(0.5, 0) // center horizontally, anchor at top
          .setDepth(1001);
          crownHealthDisplays.set(nodeId, crownHealthDisplay);
        } else {
          crownHealthDisplay.setText(healthText);
          crownHealthDisplay.setPosition(textX, textY);
          crownHealthDisplay.setVisible(true);
          crownHealthDisplay.setAlpha(1); // Reset alpha in case it was flickering
          // Stop any existing tween
          if (crownHealthDisplay.flickerTween) {
            crownHealthDisplay.flickerTween.remove();
            crownHealthDisplay.flickerTween = null;
          }
        }
      } else if (crownHealthValue === 0) {
        // Display flickering skull emoji when health is 0
        const skullText = '💀';
        
        if (!crownHealthDisplay) {
          crownHealthDisplay = sceneRef.add.text(textX, textY, skullText, {
            fontFamily: 'monospace',
            fontSize: '20px',
            fontStyle: 'bold',
            color: '#FF0000',
          })
          .setOrigin(0.5, 0) // center horizontally, anchor at top
          .setDepth(1001);
          crownHealthDisplays.set(nodeId, crownHealthDisplay);
        } else {
          crownHealthDisplay.setText(skullText);
          crownHealthDisplay.setPosition(textX, textY);
          crownHealthDisplay.setStyle({ fontSize: '20px', color: '#FF0000' });
          crownHealthDisplay.setVisible(true);
        }
        
        // Add flickering effect if not already present
        if (!crownHealthDisplay.flickerTween) {
          crownHealthDisplay.flickerTween = sceneRef.tweens.add({
            targets: crownHealthDisplay,
            alpha: 0.3,
            duration: 400,
            yoyo: true,
            repeat: -1,
            ease: 'Sine.easeInOut'
          });
        }
      }
    } else if (nodeId != null) {
      destroyCrownHealthDisplay(nodeId);
    }

    return layout;
  }

  function drawKingMovePreviewCrown(nx, baseBottomY, radius, options = {}) {
    if (!graphicsNodes) return null;
    const {
      highlighted = false,
      strokeColor: overrideStrokeColor,
      fillColor: overrideFillColor,
    } = options;
    const layout = createKingCrownLayout(nx, baseBottomY, radius);
    if (!layout) return null;
    const baseStrokeColor = Number.isFinite(overrideStrokeColor)
      ? overrideStrokeColor
      : 0x000000;
    const strokeColor = highlighted ? baseStrokeColor : darkenColor(baseStrokeColor, 0.9);
    const fillColor = Number.isFinite(overrideFillColor) ? overrideFillColor : KING_CROWN_FILL_COLOR;
    const strokeAlpha = highlighted ? 0.88 : 0.65;
    const strokeWidth = highlighted ? 2.4 : 2.0;
    const fillAlpha = highlighted ? 0.35 : 0.24;

    const bodyOutline = layout.bodyOutlinePoints || layout.outlinePoints;
    if (bodyOutline) {
      fillCrownPolygon(bodyOutline, fillColor, fillAlpha);
    }
    strokeCrownPolygon(layout.outlinePoints, strokeWidth, strokeColor, strokeAlpha);

    return {
      centerX: layout.centerX,
      centerY: layout.centerY,
      bounds: layout.bounds,
      hitRadius: layout.hitRadius,
    };
  }

  function drawKingMoveTargetsOverlay() {
    if (!graphicsNodes) return;
    kingMoveTargetRenderInfo.clear();
    if (!kingSelectionActive || kingMoveTargetsList.length === 0) return;
    if (!view) return;

    const baseScale = Math.min(view.scaleX, view.scaleY);

    kingMoveTargetsList.forEach((nodeId, index) => {
      const node = nodes.get(nodeId);
      if (!node) return;

      const [screenX, screenY] = worldToScreen(node.x, node.y);
      if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return;

      const nodeRadius = Math.max(1, calculateNodeRadius(node, baseScale));
      const crownRadius = computeStandardKingCrownRadius(baseScale, KING_OPTION_RADIUS_MULTIPLIER);
      const bounceMagnitude = Math.max(3, crownRadius * KING_OPTION_BOUNCE_SCALE);
      const bounce = Math.sin(animationTime * 2.6 + index * 0.8) * bounceMagnitude;

      const verticalGap = nodeRadius + crownRadius * KING_OPTION_VERTICAL_SCALE + KING_OPTION_VERTICAL_EXTRA;
      const crownOffset = -(verticalGap) - bounce;
      const crownAnchorY = screenY + crownOffset;
      const defaultCenterY = crownAnchorY - crownRadius * 0.7;

      const isHovered = kingMoveTargetHoveredId === nodeId;
      const isPending = kingMovePendingDestinationId === nodeId;
      const playerColor = ownerToColor(myPlayerId) || 0x000000;
      const accentColor = ownerToSecondaryColor(myPlayerId) || playerColor;
      const emphasized = isPending || isHovered;
      const strokeColor = emphasized ? playerColor : 0x000000;
      const fillColor = emphasized ? accentColor : 0xffd700;

      const layout = drawKingMovePreviewCrown(screenX, crownAnchorY, crownRadius, {
        highlighted: emphasized,
        strokeColor,
        fillColor,
      }) || null;

      const hitRadius = layout?.hitRadius ?? crownRadius + 10;
      const centerX = layout?.centerX ?? screenX;
      const centerY = layout?.centerY ?? defaultCenterY;

      kingMoveTargetRenderInfo.set(nodeId, {
        centerX,
        centerY,
        hitRadius,
        bounds: layout?.bounds ?? null,
        radius: crownRadius,
      });
    });
  }

  function pickKingMoveTargetFromScreen(screenX, screenY) {
    if (!kingSelectionActive || kingMoveTargetRenderInfo.size === 0) return null;
    let bestId = null;
    let bestDistSq = Infinity;
    kingMoveTargetRenderInfo.forEach((info, nodeId) => {
      if (!info) return;
      if (info.bounds && isPointWithinRect(screenX, screenY, info.bounds)) {
        bestId = nodeId;
        bestDistSq = 0;
        return;
      }
      const centerX = Number.isFinite(info.centerX) ? info.centerX : 0;
      const centerY = Number.isFinite(info.centerY) ? info.centerY : 0;
      const radius = Number.isFinite(info.hitRadius) ? info.hitRadius : 18;
      const dx = screenX - centerX;
      const dy = screenY - centerY;
      const distSq = dx * dx + dy * dy;
      if (distSq <= radius * radius && distSq < bestDistSq) {
        bestId = nodeId;
        bestDistSq = distSq;
      }
    });
    return bestId;
  }

  function redrawStatic() {
    // Draw edges first, then nodes
    if (graphicsStartZones) graphicsStartZones.clear();
    graphicsEdges.clear();
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) {
      graphicsNodes.clear();
      // Hide gold display when menu is visible (graph is not drawn)
      if (goldDisplay) goldDisplay.style.display = 'none';
      updateReplaySpeedUI();
      // Hide progress bar when menu is visible
      if (progressBar) progressBar.style.display = 'none';
      // Hide timer when menu is visible
      if (timerDisplay) timerDisplay.style.display = 'none';
      // Hide toggles grid when menu is visible
      const togglesGrid = document.getElementById('inGameToggles');
      if (togglesGrid) togglesGrid.style.display = 'none';
      // Hide UI bars when menu is visible
      if (topUiBar) topUiBar.style.display = 'none';
      if (bottomUiBar) bottomUiBar.style.display = 'none';
      nodeJuiceTexts.forEach((text) => {
        if (text) text.setVisible(false);
      });
      nodeResourceTexts.forEach((text) => {
        if (text) text.setVisible(false);
      });
      reversePipeButtons.forEach((entry) => {
        if (entry?.text) entry.text.setVisible(false);
      });
      return; // Do not draw game under menu
    }

    updateBrassPreviewIntersections();

    // Show UI bars when game is active
    if (topUiBar) topUiBar.style.display = 'block';
    if (bottomUiBar) bottomUiBar.style.display = 'flex';
    
    // Draw border box around play area (warp border handles inner toggle)
    drawPlayAreaBorder();
    drawHiddenStartOverlay();
    const overflowMode = ['overflow', 'nuke', 'cross', 'brass-old', 'go', 'warp', 'i-warp', 'semi', 'i-semi', 'flat', 'i-flat'].includes(normalizeMode(gameMode));
    const sandboxHoverEnabled = sandboxModeEnabled();
    
    // Show gold display when graph is being drawn and we have nodes/game data
    if (goldDisplay && nodes.size > 0) {
      goldDisplay.style.display = replayMode ? 'none' : 'block';
    }
    if (replayMode) {
      updateReplaySpeedUI();
    }
    // Show progress bar when graph is being drawn and we have nodes/game data
    if (progressBar && nodes.size > 0) {
      progressBar.style.display = 'block';
    }
    // Show timer when graph is being drawn and we have nodes/game data
    if (timerDisplay && nodes.size > 0 && gameStartTime) {
      timerDisplay.style.display = 'block';
    }
    // Do not auto-show toggles; they are behind Settings button
    for (const [id, e] of edges.entries()) {
      const s = nodes.get(e.source);
      const t = nodes.get(e.target);
      if (!s || !t) continue;
      drawEdge(e, s, t, id);

      // Edge flow labels at midpoint
      const midPoint = getEdgeMidpointWorld(e);
      const midX = midPoint ? midPoint.x : (s.x + t.x) / 2;
      const midY = midPoint ? midPoint.y : (s.y + t.y) / 2;
      const [sx, sy] = worldToScreen(midX, midY);
      updateReverseButton(id, e, sx, sy);
      let textObj = edgeFlowTexts.get(id);
      if (persistentEdgeFlow && (e.lastTransfer || 0) > 0) {
        const perSecond = (e.lastTransfer || 0) / Math.max(1e-6, tickIntervalSec);
        const label = Math.round(perSecond).toString();
        if (!textObj) {
          textObj = sceneRef.add.text(sx, sy, label, {
            font: '14px monospace',
            color: '#000000',
            align: 'center',
            stroke: '#ffffff',
            strokeThickness: 3
          });
          textObj.setOrigin(0.5, 0.5);
          edgeFlowTexts.set(id, textObj);
        } else {
          if (textObj.text !== label) textObj.setText(label);
          textObj.setPosition(sx, sy);
          if (textObj.style && textObj.style.fontSize !== '14px') textObj.setFontSize(14);
          textObj.setStroke('#ffffff', 3);
          if (!textObj.visible) textObj.setVisible(true);
        }
      } else {
        if (textObj) textObj.setVisible(false);
      }
    }

    reversePipeButtons.forEach((entry, edgeId) => {
      if (!edges.has(edgeId)) {
        removeReverseButton(edgeId);
      }
    });

    drawPrePipes();

    graphicsNodes.clear();
    for (const [id, n] of nodes.entries()) {
      const [nx, ny] = worldToScreen(n.x, n.y);
      const isBrassNode = Boolean(n.isBrass);
      const nodeOwned = n.owner != null;
      const fillColor = nodeOwned
        ? ownerToColor(n.owner)
        : (isBrassNode ? BRASS_PIPE_COLOR : ownerToColor(null));
      graphicsNodes.fillStyle(fillColor, 1);
      const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
      
      const radius = calculateNodeRadius(n, baseScale);
      const r = Math.max(1, radius);
      graphicsNodes.fillCircle(nx, ny, r);

      const resourceType = normalizeNodeResourceType(n.resourceType);
      const resourceKey = resourceType === 'gem' ? normalizeNodeResourceKey(n.resourceKey) : null;
      const resourceEmoji = getResourceEmoji(resourceType, resourceKey);
      const shouldShowEmoji = Boolean(resourceEmoji) && n.owner == null;
      let emojiText = nodeResourceTexts.get(id);
      const canShowEmoji = shouldShowEmoji && sceneRef;
      if (canShowEmoji) {
        const desiredFont = Math.round(Math.max(14, r * 1.25));
        if (!emojiText) {
          emojiText = sceneRef.add.text(nx, ny, resourceEmoji, {
            fontFamily: 'sans-serif',
            fontSize: `${desiredFont}px`,
            color: '#ffffff',
            align: 'center',
          });
          emojiText.setOrigin(0.5, 0.5);
          emojiText.setDepth(4);
          nodeResourceTexts.set(id, emojiText);
        }
        if (emojiText) {
          if (emojiText.text !== resourceEmoji) {
            emojiText.setText(resourceEmoji);
          }
          if (emojiText.style && emojiText.style.fontSize !== `${desiredFont}px`) {
            emojiText.setFontSize(desiredFont);
          }
          emojiText.setPosition(nx, ny);
          if (!emojiText.visible) emojiText.setVisible(true);
        }
      } else if (emojiText) {
        emojiText.setVisible(false);
      }

      const numberOffset = canShowEmoji ? -Math.min(r * 0.55, 12) : 0;

      const shouldShowJuiceText = persistentNumbers && n.owner != null;
      if (shouldShowJuiceText) {
        const juiceValue = Math.floor(n.size || 0); // No decimals
        let juiceText = nodeJuiceTexts.get(id);

        if (!juiceText) {
          // Create new text object (world-space; camera handles positioning)
          juiceText = sceneRef.add.text(nx, ny + numberOffset, juiceValue.toString(), {
            font: '12px monospace',
            color: '#000000',
            align: 'center'
          });
          juiceText.setOrigin(0.5, 0.5); // Center the text
          nodeJuiceTexts.set(id, juiceText);
          juiceText._lastOwner = n.owner;
        } else {
          // Always re-center text to the node's current screen position
          juiceText.setPosition(nx, ny + numberOffset);
          const newTextValue = juiceValue.toString();
          if (juiceText.text !== newTextValue) {
            juiceText.setText(newTextValue);
          }
          if (juiceText._lastOwner !== n.owner) {
            juiceText.setColor('#000000');
            juiceText._lastOwner = n.owner;
          }
          if (!juiceText.visible) juiceText.setVisible(true);
        }
      } else {
        const juiceText = nodeJuiceTexts.get(id);
        if (juiceText) {
          juiceText.setVisible(false);
        }
      }
      
      // Max-size thick black border
      const juiceVal = (n.size || 0);
      const isFull = juiceVal >= nodeMaxJuice - 1e-6;
      if (isFull) {
        if (overflowMode && n.owner != null) {
          const pendingGold = Math.max(0, Number(n.pendingGold) || 0);
          const progress = Math.min(1, pendingGold / OVERFLOW_PENDING_GOLD_THRESHOLD);
          if (progress > 0) {
            const ringRadius = r + 2;
            const startAngle = -Math.PI / 2;
            const endAngle = startAngle + progress * Math.PI * 2;
          graphicsNodes.lineStyle(4, 0xffd700, 1);
            graphicsNodes.beginPath();
            graphicsNodes.arc(nx, ny, ringRadius, startAngle, endAngle, false);
            graphicsNodes.strokePath();
          }
        } else if (!overflowMode) {
          graphicsNodes.lineStyle(4, 0x000000, 1);
          graphicsNodes.strokeCircle(nx, ny, r + 2);
        }
      }

      if (isBrassNode) {
        const triHeight = Math.max(6, r * 0.9);
        const halfBase = triHeight * 0.5;
        const topX = nx;
        const topY = ny - triHeight / 2;
        const leftX = nx - halfBase;
        const leftY = ny + triHeight / 2;
        const rightX = nx + halfBase;
        const rightY = ny + triHeight / 2;
        graphicsNodes.fillStyle(BRASS_PIPE_COLOR, 1);
        graphicsNodes.fillTriangle(topX, topY, leftX, leftY, rightX, rightY);
        if (nodeOwned) {
          graphicsNodes.lineStyle(1, 0x000000, 0.35);
          graphicsNodes.strokeTriangle(topX, topY, leftX, leftY, rightX, rightY);
        }
      }

      const kingOwnerId = Number.isFinite(n.kingOwnerId) ? Number(n.kingOwnerId) : null;
      if (winCondition === 'king') {
        if (kingOwnerId != null) {
          const crownColor = ownerToColor(kingOwnerId);
          const isSelectedKing = kingSelectionActive && kingSelectedNodeId === id;
          const selectionColor = ownerToSecondaryColor(myPlayerId) || ownerToColor(myPlayerId) || crownColor || 0x000000;
          const kingCrownRadius = computeStandardKingCrownRadius(baseScale);
          drawKingCrown(nx, ny, kingCrownRadius, crownColor, {
            highlighted: isSelectedKing,
            highlightColor: selectionColor,
            crownHealth: Number.isFinite(n.kingCrownHealth) ? n.kingCrownHealth : null,
            crownMax: Number.isFinite(n.kingCrownMax) ? n.kingCrownMax : null,
            nodeId: id,
          });
        } else if (crownHealthDisplays.has(id)) {
          destroyCrownHealthDisplay(id);
        }
      } else if (crownHealthDisplays.has(id)) {
        destroyCrownHealthDisplay(id);
      }

      // Hover effect: player's color border when eligible for starting node pick
      if (hoveredNodeId === id && !myPicked && (n.owner == null) && isNodeWithinStartZone(n)) {
        const myColor = ownerToColor(myPlayerId);
        graphicsNodes.lineStyle(3, myColor, 1);
        graphicsNodes.strokeCircle(nx, ny, r + 3);
      }
      
      // Hover effect: show if node can be targeted for flow
      // Only show this when no ability is active to avoid conflicting with ability-specific highlights
      if (hoveredNodeId === id && myPicked && !activeAbility) {
        if (sandboxHoverEnabled || canTargetNodeForFlow(id)) {
          const myColor = ownerToColor(myPlayerId);
          graphicsNodes.lineStyle(3, myColor, 0.8);
          graphicsNodes.strokeCircle(nx, ny, r + 3);
        } else if (n.owner !== myPlayerId) {
          const playerSecondaryColor = ownerToSecondaryColor(myPlayerId);
          graphicsNodes.lineStyle(3, playerSecondaryColor, 0.8);
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
          const playerSecondaryColor = ownerToSecondaryColor(myPlayerId);
          graphicsNodes.lineStyle(3, playerSecondaryColor, 0.8);
          graphicsNodes.strokeCircle(nx, ny, r + 3);
        } else if (bridgeFirstNode !== id) {
            const firstNode = nodes.get(bridgeFirstNode);

            // secondary color highlight on the target
            const playerSecondaryColor = ownerToSecondaryColor(myPlayerId);
            graphicsNodes.lineStyle(3, playerSecondaryColor, 0.8);
            graphicsNodes.strokeCircle(nx, ny, r + 3);

            // show static, live-updating midpoint label
            updateBridgeCostDisplay(firstNode, n, bridgePreviewWillBeBrass && brassPipesDoubleCost());
          }

      }
      
      // Destroy mode hover: show black highlight for owned nodes
      if (hoveredNodeId === id && activeAbility === 'destroy' && n.owner === myPlayerId) {
        graphicsNodes.lineStyle(3, 0x000000, 0.8); // black highlight
        graphicsNodes.strokeCircle(nx, ny, r + 3);
      }
      
      // Targeting visual indicator: pulsing ring (grows then snaps back), darkens as it grows
      if (persistentTargeting && currentTargetNodeId === id) {
        // Suppress showing the ring if this node has an outgoing edge by me that is on/flowing
        // Allow a small grace window immediately after setting the target to let outflow shut off
        const GRACE_SECONDS = 0.25;
        const withinGrace = currentTargetSetTime != null && (animationTime - currentTargetSetTime) < GRACE_SECONDS;
        if (withinGrace || !shouldSuppressTargetRingForNode(id)) {
          const myColor = ownerToColor(myPlayerId);
          const pulseDuration = 0.625; // seconds per grow cycle (twice as fast)
          const innerRadius = r + 6;   // inner edge stays fixed at this radius
          const minThickness = 3;
          const maxThickness = 10;
          const cycleT = (animationTime % pulseDuration) / pulseDuration; // 0..1
          const thickness = minThickness + (maxThickness - minThickness) * cycleT;
          const pathRadius = innerRadius + thickness / 2; // keep inner edge constant

          // Darken as it grows, but keep it relatively bright (1.0 -> 0.8)
          const baseR = (myColor >> 16) & 0xFF;
          const baseG = (myColor >> 8) & 0xFF;
          const baseB = myColor & 0xFF;
          const brightness = 1 - 0.2 * cycleT; // 1.0 at small -> 0.8 at largest
          const darkR = Math.max(0, Math.min(255, Math.floor(baseR * brightness)));
          const darkG = Math.max(0, Math.min(255, Math.floor(baseG * brightness)));
          const darkB = Math.max(0, Math.min(255, Math.floor(baseB * brightness)));
          const darkenedColor = (darkR << 16) | (darkG << 8) | darkB;

          graphicsNodes.lineStyle(thickness, darkenedColor, 1);
          graphicsNodes.strokeCircle(nx, ny, pathRadius);
        } else {
          // Outflow persists beyond grace, permanently clear target so it won't reappear
          currentTargetNodeId = null;
          currentTargetSetTime = null;
        }
      }
    }

    drawKingMoveTargetsOverlay();

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
          juice: 8.0, // mirror the neutral-node baseline for preview sizing
          size: 8.0,
          owner: null,
          pendingGold: 0,
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
        updateBridgeCostDisplay(firstNode, targetNode, bridgePreviewWillBeBrass && brassPipesDoubleCost());
      }
    }
    
    // Draw money indicators
    drawMoneyIndicators();
  }

  function computeTransform(viewW, viewH) {
    if (nodes.size === 0) return;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;

    nodes.forEach((node) => {
      minX = Math.min(minX, node.x);
      minY = Math.min(minY, node.y);
      maxX = Math.max(maxX, node.x);
      maxY = Math.max(maxY, node.y);
    });

    if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) {
      if (screen && Number.isFinite(screen.width) && Number.isFinite(screen.height)) {
        const screenMinX = Number.isFinite(screen.minX) ? screen.minX : 0;
        const screenMinY = Number.isFinite(screen.minY) ? screen.minY : 0;
        minX = screenMinX;
        minY = screenMinY;
        maxX = screenMinX + screen.width;
        maxY = screenMinY + screen.height;
      } else {
        minX = 0;
        minY = 0;
        maxX = 100;
        maxY = 100;
      }
    }

    const warpViewActive = (isWarpLike(gameMode) || isWarpLike(selectedMode));
    if (warpViewActive) {
      const padX = (Math.max(1, maxX - minX)) * WARP_MARGIN_RATIO_X;
      const padY = (Math.max(1, maxY - minY)) * WARP_MARGIN_RATIO_Y;
      minX -= padX;
      maxX += padX;
      minY -= padY;
      maxY += padY;
    }

    const topBarHeight = (topUiBar && topUiBar.style.display !== 'none') ? (topUiBar.offsetHeight || 0) : 0;
    const bottomBarHeight = (bottomUiBar && bottomUiBar.style.display !== 'none') ? (bottomUiBar.offsetHeight || 0) : 0;
    const baseTopPadding = warpViewActive ? 0 : 52;
    const baseBottomPadding = warpViewActive ? 0 : 32;
    const verticalMargin = warpViewActive ? 2 : 8;
    const topPadding = Math.max(baseTopPadding, topBarHeight) + verticalMargin;
    const bottomPadding = Math.max(baseBottomPadding, bottomBarHeight) + verticalMargin;
    const sidePadding = warpViewActive ? 12 : 40;
    const rightReservedPx = warpViewActive ? 12 : 24; // space for gold number and margins
    const extraSide = rightReservedPx / 2;
    const leftPadding = sidePadding + extraSide;
    const rightPadding = sidePadding + extraSide;
    const horizontalPlayable = Math.max(1, viewW - leftPadding - rightPadding);
    const verticalPlayable = Math.max(1, viewH - topPadding - bottomPadding);

    const width = Math.max(1, maxX - minX);
    const height = Math.max(1, maxY - minY);
    const scale = Math.min(horizontalPlayable / width, verticalPlayable / height);

    const offsetX = leftPadding + (horizontalPlayable - scale * width) / 2 - scale * minX;
    const offsetY = topPadding + (verticalPlayable - scale * height) / 2 - scale * minY;
    view = { minX, minY, maxX, maxY, scaleX: scale, scaleY: scale, offsetX, offsetY };
  }

  function worldToScreen(x, y) {
    if (!view) return [x, y];
    return [x * view.scaleX + view.offsetX, y * view.scaleY + view.offsetY];
  }

  function drawPlayAreaBorder() {
    if (!view || !screen) return;
    
    // Get world bounds from screen object
    const minX = Number.isFinite(screen.minX) ? screen.minX : 0;
    const minY = Number.isFinite(screen.minY) ? screen.minY : 0;
    const maxX = minX + (Number.isFinite(screen.width) ? screen.width : 100);
    const maxY = minY + (Number.isFinite(screen.height) ? screen.height : 100);
    const boardWidth = Math.max(1, maxX - minX);
    const boardHeight = Math.max(1, maxY - minY);
    
    // Convert corners to screen coordinates
    const [topLeftX, topLeftY] = worldToScreen(minX, minY);
    const [topRightX, topRightY] = worldToScreen(maxX, minY);
    const [bottomRightX, bottomRightY] = worldToScreen(maxX, maxY);
    const [bottomLeftX, bottomLeftY] = worldToScreen(minX, maxY);
    
    // Draw border rectangle (optional)
    if (SHOW_PLAY_AREA_BORDER) {
      graphicsEdges.lineStyle(4, 0x888888, 1); // Grey border, 4px thick
      graphicsEdges.beginPath();
      graphicsEdges.moveTo(topLeftX, topLeftY);
      graphicsEdges.lineTo(topRightX, topRightY);
      graphicsEdges.lineTo(bottomRightX, bottomRightY);
      graphicsEdges.lineTo(bottomLeftX, bottomLeftY);
      graphicsEdges.closePath();
      graphicsEdges.strokePath();
    }

    // Compute & draw warp border (purple) when active
    if (isWarpLike(gameMode) || isWarpLike(selectedMode)) {
      const marginX = boardWidth * WARP_MARGIN_RATIO_X;
      const marginY = boardHeight * WARP_MARGIN_RATIO_Y;
      const warpMinX = minX - marginX;
      const warpMaxX = maxX + marginX;
      const warpMinY = minY - marginY;
      const warpMaxY = maxY + marginY;

      warpBoundsWorld = {
        minX: warpMinX,
        minY: warpMinY,
        maxX: warpMaxX,
        maxY: warpMaxY,
        width: Math.max(1, warpMaxX - warpMinX),
        height: Math.max(1, warpMaxY - warpMinY),
      };

      const [warpTopLeftX, warpTopLeftY] = worldToScreen(warpMinX, warpMinY);
      const [warpTopRightX, warpTopRightY] = worldToScreen(warpMaxX, warpMinY);
      const [warpBottomRightX, warpBottomRightY] = worldToScreen(warpMaxX, warpMaxY);
      const [warpBottomLeftX, warpBottomLeftY] = worldToScreen(warpMinX, warpMaxY);

      warpBoundsScreen = {
        minX: Math.min(warpTopLeftX, warpBottomLeftX, warpTopRightX, warpBottomRightX),
        maxX: Math.max(warpTopLeftX, warpBottomLeftX, warpTopRightX, warpBottomRightX),
        minY: Math.min(warpTopLeftY, warpBottomLeftY, warpTopRightY, warpBottomRightY),
        maxY: Math.max(warpTopLeftY, warpBottomLeftY, warpTopRightY, warpBottomRightY),
      };

      graphicsEdges.lineStyle(5, WARP_BORDER_COLOR, 0.92);
      graphicsEdges.beginPath();
      if (isSemiWarpDisplayActive()) {
        graphicsEdges.moveTo(warpTopLeftX, warpTopLeftY);
        graphicsEdges.lineTo(warpTopRightX, warpTopRightY);
        graphicsEdges.moveTo(warpBottomLeftX, warpBottomLeftY);
        graphicsEdges.lineTo(warpBottomRightX, warpBottomRightY);
      } else {
        graphicsEdges.moveTo(warpTopLeftX, warpTopLeftY);
        graphicsEdges.lineTo(warpTopRightX, warpTopRightY);
        graphicsEdges.lineTo(warpBottomRightX, warpBottomRightY);
        graphicsEdges.lineTo(warpBottomLeftX, warpBottomLeftY);
        graphicsEdges.closePath();
      }
      graphicsEdges.strokePath();
    } else {
      warpBoundsWorld = null;
      warpBoundsScreen = null;
    }
  }

  function drawHiddenStartOverlay() {
    if (!graphicsStartZones) return;
    if (!hiddenStartActive || hiddenStartRevealed || phase !== 'picking') return;
    if (!view) return;

    const bounds = hiddenStartBounds || {};
    const fallbackMinX = Number.isFinite(screen?.minX) ? screen.minX : 0;
    const fallbackWidth = Number.isFinite(screen?.width) ? screen.width : 100;
    const fallbackMinY = Number.isFinite(screen?.minY) ? screen.minY : 0;
    const fallbackHeight = Number.isFinite(screen?.height) ? screen.height : 100;

    const boardMinX = Number.isFinite(bounds.minX) ? bounds.minX : fallbackMinX;
    const boardMaxX = Number.isFinite(bounds.maxX) ? bounds.maxX : fallbackMinX + fallbackWidth;

    const splitWorldX = Number.isFinite(hiddenStartBoundary)
      ? hiddenStartBoundary
      : (boardMinX + boardMaxX) / 2;
    const clampedSplitWorld = Math.min(Math.max(splitWorldX, boardMinX), boardMaxX);
    const [splitScreenX] = worldToScreen(clampedSplitWorld, fallbackMinY);

    const canvasWidth = game.scale.gameSize.width;
    const canvasHeight = game.scale.gameSize.height;

    const topBarEl = document.getElementById('topUiBar');
    const bottomBarEl = document.getElementById('bottomUiBar');
    const topOffset = Math.max(0, topBarEl?.offsetHeight ?? 0);
    const bottomOffset = Math.max(0, bottomBarEl?.offsetHeight ?? 0);

    let leftEdge;
    let rightEdge;
    if (hiddenStartSide === 'left') {
      leftEdge = 0;
      rightEdge = Math.min(canvasWidth, Math.max(0, splitScreenX));
    } else if (hiddenStartSide === 'right') {
      leftEdge = Math.max(0, Math.min(canvasWidth, splitScreenX));
      rightEdge = canvasWidth;
    } else {
      return;
    }

    const topEdge = topOffset;
    const bottomEdge = canvasHeight - bottomOffset;

    if (rightEdge <= leftEdge || bottomEdge <= topEdge) return;

    const rectX = Math.floor(leftEdge);
    const rectY = Math.floor(topEdge);
    const width = Math.ceil(rightEdge - leftEdge);
    const height = Math.ceil(bottomEdge - topEdge);

    const colorEntry = players.get(myPlayerId);
    const baseColor = colorEntry?.color || '#ffffff';
    const fillColor = hexToInt(lightenColor(baseColor, 0.2));
    const outlineColor = hexToInt(baseColor);

    if (!myPicked) {
      graphicsStartZones.fillStyle(fillColor, 0.26);
      graphicsStartZones.fillRect(rectX, rectY, width, height);
    }
    graphicsStartZones.lineStyle(7, outlineColor, 0.95);
    graphicsStartZones.strokeRect(rectX, rectY, width, height);
  }

  function resetHiddenStartState() {
    hiddenStartActive = false;
    hiddenStartRevealed = false;
    hiddenStartSide = null;
    hiddenStartBoundary = null;
    hiddenStartBounds = null;
  }

  function updateHiddenStartState(payload) {
    if (!payload || !payload.active) {
      resetHiddenStartState();
      return;
    }

    hiddenStartActive = true;
    hiddenStartRevealed = !!payload.revealed;
    hiddenStartSide = typeof payload.side === 'string' ? payload.side : null;
    hiddenStartBoundary = Number.isFinite(payload.boundary) ? Number(payload.boundary) : null;

    hiddenStartBounds = null;
    const bounds = payload.bounds;
    if (bounds && Number.isFinite(bounds.minX) && Number.isFinite(bounds.maxX)) {
      hiddenStartBounds = {
        minX: Number(bounds.minX),
        maxX: Number(bounds.maxX),
        minY: Number.isFinite(bounds.minY) ? Number(bounds.minY) : (Number.isFinite(screen?.minY) ? screen.minY : 0),
        maxY: Number.isFinite(bounds.maxY)
          ? Number(bounds.maxY)
          : (Number.isFinite(screen?.minY) ? screen.minY : 0) + (Number.isFinite(screen?.height) ? screen.height : 100),
      };
    }
  }

  function isNodeWithinStartZone(node) {
    if (!hiddenStartActive || hiddenStartRevealed || phase !== 'picking') return true;
    if (!node || !Number.isFinite(node.x)) return true;
    if (!hiddenStartSide || !Number.isFinite(hiddenStartBoundary)) return true;
    const tolerance = 1e-4;
    if (hiddenStartSide === 'left') {
      return node.x <= hiddenStartBoundary + tolerance;
    }
    if (hiddenStartSide === 'right') {
      return node.x >= hiddenStartBoundary - tolerance;
    }
    return true;
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

  function ownerToHexColor(ownerId, fallback = '#f4f4f4') {
    if (ownerId == null) return fallback;
    const entry = players.get(ownerId);
    const raw = typeof entry?.color === 'string' ? entry.color.trim() : '';
    if (!raw) return fallback;
    return raw.startsWith('#') ? raw : `#${raw}`;
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

    if (edge.pipeType !== 'reverse') return false;
    
    const sourceNode = nodes.get(edge.source);
    if (!sourceNode) return false;
    
    return sourceNode.owner === myPlayerId;
  }

  function playerControlsEdge(edge) {
    if (!edge) return false;
    const sourceNode = nodes.get(edge.source);
    if (!sourceNode) return false;
    
    // Player controls the edge if they own the source node
    return sourceNode.owner === myPlayerId;
  }

  function canTargetNodeForFlow(targetNodeId) {
    if (isCrossLikeModeActive()) return false;
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

  // Suppress the target ring if the node is expelling juice (has an outgoing edge owned by me that is on/flowing)
  function shouldSuppressTargetRingForNode(targetNodeId) {
    for (const [edgeId, edge] of edges.entries()) {
      if (edge.source === targetNodeId) {
        const sourceNode = nodes.get(edge.source);
        if (sourceNode && sourceNode.owner === myPlayerId) {
          if (edge.on || edge.flowing) {
            return true;
          }
        }
      }
    }
    return false;
  }

  function getGameCanvas() {
    if (game && game.canvas) return game.canvas;
    const domCanvas = document.querySelector('#game canvas');
    return domCanvas || null;
  }

  function ensureVirtualCursorElement() {
    if (virtualCursorEl) return virtualCursorEl;
    const wrapper = document.createElement('div');
    wrapper.id = 'virtualWarpCursor';
    Object.assign(wrapper.style, {
      position: 'fixed',
      left: '0px',
      top: '0px',
      width: '18px',
      height: '26px',
      pointerEvents: 'none',
      zIndex: 100000,
      display: 'none',
      transform: 'translate3d(-9999px, -9999px, 0)',
      filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.45))',
    });

    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 18 26');
    svg.setAttribute('width', '18');
    svg.setAttribute('height', '26');

    const path = document.createElementNS(svgNS, 'path');
    path.setAttribute('d', 'M0 0 L0 20 L5 15 L9 25 L12 23 L8 13 L18 13 Z');
    path.setAttribute('fill', VIRTUAL_CURSOR_COLOR);
    path.setAttribute('stroke', '#2d1151');
    path.setAttribute('stroke-width', '1.2');
    path.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(path);

    wrapper.appendChild(svg);
    document.body.appendChild(wrapper);
    virtualCursorEl = wrapper;
    return virtualCursorEl;
  }

  function updateVirtualCursorVisual() {
    const el = ensureVirtualCursorElement();
    if (!pointerLockActive) {
      el.style.display = 'none';
      return;
    }
    el.style.display = 'block';
    el.style.transform = `translate3d(${virtualCursorScreenX}px, ${virtualCursorScreenY}px, 0)`;
  }

  function updateMouseWorldFromVirtualCursor() {
    const [wx, wy] = screenToWorld(virtualCursorScreenX, virtualCursorScreenY);
    mouseWorldX = wx;
    mouseWorldY = wy;
    updateVirtualCursorVisual();
  }

  function syncVirtualCursorToEvent(ev) {
    if (ev && !pointerLockActive) {
      lastPointerClientX = ev.clientX;
      lastPointerClientY = ev.clientY;
      virtualCursorScreenX = ev.clientX;
      virtualCursorScreenY = ev.clientY;
    }
    updateMouseWorldFromVirtualCursor();
  }

  function handleSecondaryPointer(ev) {
    if (!ev) return;
    ev.preventDefault();
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) {
      lastPointerDownButton = null;
      return;
    }
    if (gameEnded) {
      lastPointerDownButton = null;
      return;
    }
    maybeEnableVirtualCursor(ev);
    syncVirtualCursorToEvent(ev);

    if (isReplayActive()) {
      lastPointerDownButton = null;
      return;
    }

    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    const wx = mouseWorldX;
    const wy = mouseWorldY;

    if (handleBridgeBuilding(wx, wy, baseScale, true)) {
      redrawStatic();
      lastPointerDownButton = -1;
      return;
    }

    const nodeId = pickNearestNode(wx, wy, 18 / baseScale);
    if (nodeId != null) {
      const node = nodes.get(nodeId);
      if (node && activeAbility !== 'reverse') {
        const useBrass = isCrossLikeModeActive();
        const activated = activateBridgeFromNode(nodeId, useBrass);
        if (activated || brassActivationDenied) {
          if (brassActivationDenied) {
            brassActivationDenied = false;
          }
          redrawStatic();
          lastPointerDownButton = -1;
          return;
        }
      }
    }

    const edgeId = pickEdgeNear(wx, wy, 14 / baseScale);
    if (edgeId == null) {
      const prePipeId = pickPrePipeNear(wx, wy, 14 / baseScale);
      if (prePipeId != null) {
        removePrePipeById(prePipeId, { reason: 'player' });
        lastPointerDownButton = -1;
        return;
      }
    }
    if (edgeId != null && ws && ws.readyState === WebSocket.OPEN) {
      const edge = edges.get(edgeId);
      if (edge) {
        if (!canReverseEdge(edge)) {
          if (edge.pipeType !== 'reverse') {
            showErrorMessage('Edge is not reversible');
          } else {
            const sourceNode = nodes.get(edge.source);
            if (sourceNode && sourceNode.owner != null && sourceNode.owner !== myPlayerId) {
              showErrorMessage('Pipe controlled by Opponent');
            }
          }
        } else {
          const token = localStorage.getItem('token');
          ws.send(JSON.stringify({ type: 'reverseEdge', edgeId, token }));
        }
      }
    }

    lastPointerDownButton = -1;
  }

  function shouldWarpCursor() {
    if (!pointerLockActive) return false;
    if (activeAbility !== 'bridge1way') return false;
    if (!isWarpFrontendActive()) return false;
    if (!warpBoundsScreen) return false;
    const width = warpBoundsScreen.maxX - warpBoundsScreen.minX;
    const height = warpBoundsScreen.maxY - warpBoundsScreen.minY;
    return width > 0 && height > 0;
  }

  function applyWarpWrapToScreen(x, y) {
    if (!shouldWarpCursor()) return { x, y };
    if (!isWarpWrapUnlocked()) {
      const clamped = clampCursorToWarpBounds(x, y);
      if (
        activeAbility === 'bridge1way'
        && (Math.abs(clamped.x - x) > 1e-3 || Math.abs(clamped.y - y) > 1e-3)
      ) {
        notifyWarpGemRequired();
      }
      return clamped;
    }
    const bounds = warpBoundsScreen;
    const width = bounds.maxX - bounds.minX;
    const height = bounds.maxY - bounds.minY;
    const permissions = getWarpAxisPermissionsForGameplay();
    const horizontalAllowed = permissions.horizontal;
    const verticalAllowed = permissions.vertical;
    let nextX = x;
    let nextY = y;
    const originalX = x;
    const originalY = y;
    let horizontalWrap = false;
    let verticalWrap = false;
    let wrapAxis = null;
    let wrapDirection = null;

    if (horizontalAllowed && width > 0) {
      if (originalX < bounds.minX) {
        const delta = bounds.minX - nextX;
        const wraps = Math.floor(delta / width) + 1;
        nextX += wraps * width;
        horizontalWrap = true;
        wrapAxis = 'horizontal';
        wrapDirection = 'leftToRight';
      } else if (originalX > bounds.maxX) {
        const delta = nextX - bounds.maxX;
        const wraps = Math.floor(delta / width) + 1;
        nextX -= wraps * width;
        horizontalWrap = true;
        wrapAxis = 'horizontal';
        wrapDirection = 'rightToLeft';
      }
    }

    if (!horizontalAllowed) {
      const clampedX = Math.max(bounds.minX, Math.min(bounds.maxX, nextX));
      if (clampedX !== nextX) {
        horizontalWrap = false;
        if (!verticalWrap) {
          wrapAxis = null;
          wrapDirection = null;
        }
      }
      nextX = clampedX;
    }

    if (verticalAllowed && height > 0) {
      if (originalY < bounds.minY) {
        const delta = bounds.minY - nextY;
        const wraps = Math.floor(delta / height) + 1;
        nextY += wraps * height;
        verticalWrap = true;
        if (wrapAxis) {
          wrapAxis = 'mixed';
          wrapDirection = null;
        } else {
          wrapAxis = 'vertical';
          wrapDirection = 'topToBottom';
        }
      } else if (originalY > bounds.maxY) {
        const delta = nextY - bounds.maxY;
        const wraps = Math.floor(delta / height) + 1;
        nextY -= wraps * height;
        verticalWrap = true;
        if (wrapAxis) {
          wrapAxis = 'mixed';
          wrapDirection = null;
        } else {
          wrapAxis = 'vertical';
          wrapDirection = 'bottomToTop';
        }
      }
    }

    const wrapOccurred = horizontalWrap || verticalWrap;
    const enforceLimit = bridgeFirstNode !== null;

    if (wrapOccurred && enforceLimit) {
      if (warpWrapUsed) {
        const returningSameEdge = (
          wrapAxis && wrapAxis !== 'mixed' &&
          lastWarpAxis === wrapAxis &&
          lastWarpDirection && wrapDirection &&
          lastWarpDirection !== wrapDirection
        );
        if (returningSameEdge) {
          warpWrapUsed = false;
          lastWarpAxis = null;
          lastWarpDirection = null;
          wrapAxis = null;
          wrapDirection = null;
        } else {
          const now = Date.now();
          if (now - lastDoubleWarpWarningTime > 600) {
            showErrorMessage('no double warping');
            lastDoubleWarpWarningTime = now;
          }
          return clampCursorToWarpBounds(x, y);
        }
      }
      if (!warpWrapUsed && wrapAxis !== null) {
        warpWrapUsed = true;
        lastWarpAxis = wrapAxis;
        lastWarpDirection = wrapDirection;
      }
    }

    return { x: nextX, y: nextY };
  }

  function clampCursorToViewport(x, y) {
    const maxX = window.innerWidth - 1;
    const maxY = window.innerHeight - 1;
    return {
      x: Math.max(0, Math.min(maxX, x)),
      y: Math.max(0, Math.min(maxY, y)),
    };
  }

  function clampCursorToWarpBounds(x, y) {
    if (!warpBoundsScreen) return { x, y };
    return {
      x: Math.max(warpBoundsScreen.minX, Math.min(warpBoundsScreen.maxX, x)),
      y: Math.max(warpBoundsScreen.minY, Math.min(warpBoundsScreen.maxY, y)),
    };
  }

  function resolveVirtualUiTarget() {
    if (!pointerLockActive) return null;
    const element = document.elementFromPoint(virtualCursorScreenX, virtualCursorScreenY);
    if (!element) return null;
    if (virtualCursorEl && (element === virtualCursorEl || virtualCursorEl.contains(element))) {
      return null;
    }
    const canvas = getGameCanvas();
    if (canvas && (element === canvas || canvas.contains(element))) {
      return null;
    }
    if (element.closest && element.closest('#game')) {
      return null;
    }

    const toggle = element.closest ? element.closest('.toggle-switch') : null;
    if (toggle) return toggle;

    if (element.closest) {
      const gemTarget = element.closest('.gem-count');
      if (gemTarget && gemCountsDisplay && gemCountsDisplay.contains(gemTarget)) {
        return gemTarget;
      }
    }

    const button = element.closest ? element.closest('button') : null;
    if (button) return button;

    const interactive = element.closest ? element.closest('input, select, textarea, [role="button"]') : null;
    return interactive || null;
  }

  function dispatchVirtualUiClick(target) {
    if (!target) return;
    try {
      if (typeof target.focus === 'function') {
        target.focus({ preventScroll: true });
      }
    } catch (err) {
      /* ignore focus errors */
    }

    const syntheticClick = new MouseEvent('click', {
      bubbles: true,
      cancelable: true,
      composed: true,
      clientX: virtualCursorScreenX,
      clientY: virtualCursorScreenY,
      view: window,
    });
    syntheticClick.__virtualCursor = true;
    target.dispatchEvent(syntheticClick);
  }

  function isEventInsideGameCanvas(ev) {
    const canvas = getGameCanvas();
    if (!canvas || !ev) return false;
    const rect = canvas.getBoundingClientRect();
    return ev.clientX >= rect.left && ev.clientX <= rect.right && ev.clientY >= rect.top && ev.clientY <= rect.bottom;
  }

  function maybeEnableVirtualCursor(ev) {
    if (!ev) return;
    if (pointerLockActive) return;
    if (!isEventInsideGameCanvas(ev)) return;
    if (gameEnded) return;
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) {
      cancelPendingSingleClick();
      return;
    }
    if (!isWarpLike(gameMode) && !isWarpLike(selectedMode)) return;
    const canvas = getGameCanvas();
    if (!canvas || typeof canvas.requestPointerLock !== 'function') return;
    if (document.pointerLockElement === canvas) return;
    lastPointerClientX = ev.clientX;
    lastPointerClientY = ev.clientY;
    virtualCursorScreenX = ev.clientX;
    virtualCursorScreenY = ev.clientY;
    updateMouseWorldFromVirtualCursor();
    canvas.requestPointerLock();
  }

  function releaseVirtualCursor() {
    if (document.pointerLockElement) {
      document.exitPointerLock();
    }
    updateVirtualCursorVisual();
  }

  function handlePointerLockStateChange() {
    const canvas = getGameCanvas();
    const locked = document.pointerLockElement === canvas;
    pointerLockActive = locked;
    if (!locked && canvas) {
      // Sync screen position to where the pointer reappears (best effort)
      virtualCursorScreenX = lastPointerClientX;
      virtualCursorScreenY = lastPointerClientY;
    }
    if (!locked) {
      warpWrapUsed = false;
      lastDoubleWarpWarningTime = 0;
      lastWarpAxis = null;
      lastWarpDirection = null;
    }
    updateMouseWorldFromVirtualCursor();
  }

  document.addEventListener('pointerlockchange', handlePointerLockStateChange);
  document.addEventListener('pointerlockerror', handlePointerLockStateChange);

  window.addEventListener('pointerdown', (ev) => {
    pendingVirtualUiClickTarget = null;
    const pointerType = ev.pointerType || 'mouse';
    if (pointerType !== 'mouse') return;
    lastPointerDownButton = ev.button;
    if (ev.button === 2) {
      handleSecondaryPointer(ev);
      return;
    }
    if (ev.button === 0) {
      if (pointerLockActive) {
        const uiTarget = resolveVirtualUiTarget();
        if (uiTarget) {
          pendingVirtualUiClickTarget = uiTarget;
          syncVirtualCursorToEvent(ev);
          ev.preventDefault();
          ev.stopPropagation();
          return;
        }
      }
      maybeEnableVirtualCursor(ev);
      syncVirtualCursorToEvent(ev);
    }
  });

  // Input: during picking, click to claim a node once; during playing, edge interactions allowed
  window.addEventListener('click', (ev) => {
    if (ev.__virtualCursor) {
      cancelPendingSingleClick();
      return;
    }
    if (pendingVirtualUiClickTarget) {
      const target = pendingVirtualUiClickTarget;
      pendingVirtualUiClickTarget = null;
      dispatchVirtualUiClick(target);
      ev.preventDefault();
      ev.stopPropagation();
      ev.stopImmediatePropagation?.();
      cancelPendingSingleClick();
      return;
    }
    if (lastPointerDownButton != null && lastPointerDownButton !== 0) {
      lastPointerDownButton = null;
      cancelPendingSingleClick();
      return;
    }
    lastPointerDownButton = null;
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) return;
    maybeEnableVirtualCursor(ev);
    syncVirtualCursorToEvent(ev);
    if (isReplayActive()) {
      cancelPendingSingleClick();
      return;
    }
    if (gameEnded) {
      cancelPendingSingleClick();
      return;
    }
    const wx = mouseWorldX;
    const wy = mouseWorldY;
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;

    if (isNukeModeActive() && ev.button === 0) {
      if (ev.detail >= 2) {
        cancelPendingSingleClick();
        const nuked = attemptNodeNuke(wx, wy, baseScale);
        if (!nuked) {
          handleSingleClick(ev, wx, wy, baseScale);
        }
      } else {
        scheduleSingleClickExecution(ev, wx, wy, baseScale);
      }
      return;
    }

  cancelPendingSingleClick();
  handleSingleClick(ev, wx, wy, baseScale);
  });


  function activateBridgeFromNode(nodeId, useBrass) {
    const node = nodes.get(nodeId);
    if (!node) return false;
    brassActivationDenied = false;

    const magicBrassMode = isMagicResourceModeActive();

    let wantBrass = determineBridgeBrassPreference(node, useBrass);
    if (magicBrassMode) {
      if (rageGemModeActive && canActivateRageGemMode()) {
        wantBrass = false;
      } else if (reverseGemModeActive && canActivateReverseGemMode()) {
        wantBrass = false;
      } else if (brassGemModeActive && canActivateBrassGemMode()) {
        wantBrass = true;
      } else if (!canActivateBrassGemMode()) {
        wantBrass = false;
      }
    }

    const ownershipRequired = pipeStartRequiresOwnership();
    const lacksOwnership = ownershipRequired && node.owner !== myPlayerId;
    if (lacksOwnership && !isPreMoveEnabled()) {
      showErrorMessage('Pipes must start from your nodes', 'money');
      brassActivationDenied = true;
      return false;
    }
    activeAbility = 'bridge1way';
    bridgeFirstNode = nodeId;
    bridgeIsBrass = wantBrass;
    bridgePreviewWillBeBrass = computeInitialBrassPreviewState();
    xbPreviewBlockedByBrass = false;
    brassPreviewIntersections.clear();
    warpWrapUsed = false;
    lastDoubleWarpWarningTime = 0;
    lastWarpAxis = null;
    lastWarpDirection = null;
    hideBridgeCostDisplay();
    updateBrassPreviewIntersections();
    return true;
  }


  function handleBridgeBuilding(wx, wy, baseScale, isRightClick = false) {
    if (isReplayActive()) return false;
    if (activeAbility !== 'bridge1way') return false;
    
    const nodeId = pickNearestNode(wx, wy, 18 / baseScale);
    const edgeId = pickEdgeNear(wx, wy, 14 / baseScale);
    
    if (nodeId != null) {
      const node = nodes.get(nodeId);
      if (node) {
        if (bridgeFirstNode === null) {
          // Start bridge building from any node
          if (pipeStartRequiresOwnership() && node.owner !== myPlayerId && !isPreMoveEnabled()) {
            showErrorMessage('Pipes must start from your nodes', 'money');
            return true;
          }
          bridgeFirstNode = nodeId;
          warpWrapUsed = false;
          lastDoubleWarpWarningTime = 0;
          lastWarpAxis = null;
          lastWarpDirection = null;
          return true; // Handled
        } else if (bridgeFirstNode !== nodeId) {
          // Complete bridge building - second node can be any node
          const firstNode = nodes.get(bridgeFirstNode);
          if (!firstNode) {
            clearBridgeSelection();
            hideBridgeCostDisplay();
            return true;
          }
          const modeIsXb = isXbModeActive();
          const useBrassPipe = bridgePreviewWillBeBrass && (isMagicResourceModeActive() || isCrossLikeModeActive());
          const pipeType = determinePipeTypeForBridge(useBrassPipe);
          const applyBrassCost = pipeType === 'gold' && brassPipesDoubleCost();
          if (modeIsXb && xbPreviewBlockedByBrass) {
            showErrorMessage('Cannot cross brass pipe');
            return true;
          }
          const ownershipRequired = pipeStartRequiresOwnership();
          const firstOwner = firstNode.owner;
          const lacksOwnership = ownershipRequired && firstOwner !== myPlayerId;
          const warpPreference = getActiveWarpPreference();
          const cost = calculateBridgeCost(firstNode, node, applyBrassCost, warpPreference);
          const hasFunds = sandboxModeEnabled() || goldValue >= cost;
          const shouldQueuePrePipe = isPreMoveEnabled() && (lacksOwnership || !hasFunds);

          if (!lacksOwnership && hasFunds && ws && ws.readyState === WebSocket.OPEN) {
            const token = localStorage.getItem('token');
            const warpInfo = buildWarpInfoForBridge(firstNode, node, warpPreference);
            const warpInfoPayload = warpInfo ? {
              axis: warpInfo.axis,
              segments: Array.isArray(warpInfo.segments)
                ? warpInfo.segments.map((segment) => [
                    Number(segment[0]),
                    Number(segment[1]),
                    Number(segment[2]),
                    Number(segment[3])
                  ])
                : []
            } : null;

            const buildBridgePayload = {
              type: 'buildBridge',
              fromNodeId: bridgeFirstNode,
              toNodeId: nodeId,
              cost: cost,
              warpInfo: warpInfoPayload,
              token: token,
              pipeType,
            };

            const usesWarpGem = isMagicResourceModeActive()
              && warpInfoPayload
              && typeof warpInfoPayload.axis === 'string'
              && warpInfoPayload.axis !== 'none';

            if (usesWarpGem) {
              setWarpGemModeActive(false, { clearPending: false });
              pendingWarpGemSpend = true;
            }
            if (isMagicResourceModeActive() && useBrassPipe) {
              setBrassGemModeActive(false, { clearPending: false });
              pendingBrassGemSpend = true;
            } else if (isMagicResourceModeActive() && pipeType === 'rage' && rageGemModeActive) {
              setRageGemModeActive(false, { clearPending: false });
              pendingRageGemSpend = true;
            } else if (isMagicResourceModeActive() && pipeType === 'reverse' && reverseGemModeActive) {
              setReverseGemModeActive(false, { clearPending: false });
              pendingReverseGemSpend = true;
            }
            ws.send(JSON.stringify(buildBridgePayload));
            // Don't reset bridge building state here - wait for server response
            return true; // Handled
          }

          if (shouldQueuePrePipe) {
            queuePrePipe(bridgeFirstNode, nodeId, {
              warpPreference,
              pipeType,
              waitingForOwnership: lacksOwnership,
              waitingForGold: !sandboxModeEnabled() && !hasFunds,
              estimatedCost: cost,
            });
            activeAbility = null;
            clearBridgeSelection();
            hideBridgeCostDisplay();
            return true;
          }

          if (lacksOwnership) {
            showErrorMessage('Pipes must start from your nodes', 'money');
            return true;
          }
          if (!hasFunds) {
            showErrorMessage('Not enough money', 'money');
            return true;
          }
        } else {
          // Clicked same node, cancel selection
          clearBridgeSelection();
          hideBridgeCostDisplay();
          return true; // Handled
        }
      }
    }
    
    // If we get here, it means we clicked on empty space, an edge, or an invalid node
    // Cancel bridge building
    activeAbility = null;
    clearBridgeSelection();
    hideBridgeCostDisplay();
    return true; // Handled
  }

  function cancelPendingSingleClick() {
    if (pendingSingleClickTimeout !== null) {
      clearTimeout(pendingSingleClickTimeout);
      pendingSingleClickTimeout = null;
    }
    pendingSingleClickData = null;
  }

  function scheduleSingleClickExecution(ev, wx, wy, baseScale) {
    cancelPendingSingleClick();
    pendingSingleClickData = { ev, wx, wy, baseScale };
    pendingSingleClickTimeout = window.setTimeout(() => {
      const data = pendingSingleClickData;
      pendingSingleClickTimeout = null;
      pendingSingleClickData = null;
      if (!data) return;
      handleSingleClick(data.ev, data.wx, data.wy, data.baseScale);
    }, DOUBLE_CLICK_DELAY_MS);
  }

  function attemptNodeNuke(wx, wy, baseScale) {
    if (!isNukeModeActive()) return false;
    if (isReplayActive()) return false;
    if (myEliminated || gameEnded) return false;

    const nodeId = pickNearestNode(wx, wy, 18 / baseScale);
    if (nodeId == null) return false;

    const node = nodes.get(nodeId);
    if (!node) return false;
    if (node.owner !== myPlayerId) {
      showErrorMessage('Can only nuke your own nodes');
      return false;
    }

    if (!ws || ws.readyState !== WebSocket.OPEN) return false;

    const token = localStorage.getItem('token');
    ws.send(JSON.stringify({
      type: 'nukeNode',
      nodeId,
      token
    }));
    return true;
  }

  function handleSingleClick(ev, wx, wy, baseScale) {
    if (isReplayActive()) return;
    if (myEliminated || gameEnded) return;

    if (kingSelectionActive) {
      if (kingMoveOptionsPending) {
        return;
      }
      if (kingMovePendingDestinationId != null) {
        return;
      }
      const [pointerScreenX, pointerScreenY] = worldToScreen(wx, wy);
      const crownTargetId = pickKingMoveTargetFromScreen(pointerScreenX, pointerScreenY);
      if (crownTargetId != null) {
        sendKingMoveRequest(crownTargetId);
        return;
      }
      const originNode = nodes.get(kingSelectedNodeId);
      if (originNode) {
        const [originScreenX, originScreenY] = worldToScreen(originNode.x, originNode.y);
        const originCrownRadius = computeStandardKingCrownRadius(baseScale);
        const originLayout = computePlacedKingCrownLayout(originScreenX, originScreenY, originCrownRadius);
        const withinBounds = originLayout && isPointWithinRect(pointerScreenX, pointerScreenY, originLayout.bounds);
        const dx = pointerScreenX - (originLayout?.centerX ?? originScreenX);
        const dy = pointerScreenY - (originLayout?.centerY ?? originScreenY);
        const withinRadius = originLayout ? (dx * dx + dy * dy <= originLayout.hitRadius * originLayout.hitRadius) : false;
        if (withinBounds || withinRadius) {
          return;
        }
      }
      clearKingSelection();
    }

    // Handle bridge building mode
    if (handleBridgeBuilding(wx, wy, baseScale, false)) {
      return; // Bridge building was handled
    }

    const prePipeId = pickPrePipeNear(wx, wy, 14 / baseScale);
    if (prePipeId != null) {
      removePrePipeById(prePipeId, { reason: 'player' });
      return;
    }
    
    // Reverse is not a persistent mode anymore (handled via right-click only)
    
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
        // Block clicking edges that are still building
        if (e.building) {
          // Show a brief hint? For now, silently ignore
          return;
        }
          const sourceNode = nodes.get(e.source);
          // Only eligible if you own the source node
          if (sourceNode && sourceNode.owner === myPlayerId) edgeId = candidateEdgeId;
        }
      }
    }

    // Handle clicks - check for starting node pick first
    if (!myPicked && !sandboxModeEnabled() && nodeId != null && ws && ws.readyState === WebSocket.OPEN) {
      if (hiddenStartActive && !hiddenStartRevealed && phase === 'picking') {
        const candidateNode = nodes.get(nodeId);
        if (candidateNode && !isNodeWithinStartZone(candidateNode)) {
          showErrorMessage('Pick a node in your start zone');
          return;
        }
      }
      const token = localStorage.getItem('token');
      ws.send(JSON.stringify({ type: 'clickNode', nodeId: nodeId, token }));
      return; // Return after handling starting node pick
    }

    if (
      (isCrossLikeModeActive() || sandboxModeEnabled()) &&
      nodeId != null &&
      activeAbility !== 'reverse'
    ) {
      const needsActivation = activeAbility !== 'bridge1way' || bridgeFirstNode === null;
      if (needsActivation && activateBridgeFromNode(nodeId, false)) {
        redrawStatic();
        return;
      }
    }

    if (
      sandboxModeEnabled() &&
      nodeId == null &&
      edgeId == null &&
      ws && ws.readyState === WebSocket.OPEN
    ) {
      const token = localStorage.getItem('token');
      if (token) {
        ws.send(JSON.stringify({
          type: 'sandboxCreateNode',
          x: wx,
          y: wy,
          token,
        }));
      }
      return;
    }

    // Handle all other clicks (node flow targeting, edge clicks, etc.)
    if (ws && ws.readyState === WebSocket.OPEN) {
      const token = localStorage.getItem('token');
      
      if (nodeId != null) {
        // Check if this is a node we can target for flow
        if (canTargetNodeForFlow(nodeId)) {
          if (persistentTargeting) {
            // Full targeting mode: redirect energy towards this node (existing behavior)
            currentTargetNodeId = nodeId; // Set for visual indicator
            currentTargetSetTime = animationTime; // mark when we set it (seconds)
            ws.send(JSON.stringify({
              type: 'redirectEnergy',
              targetNodeId: nodeId,
              token: token
            }));
          } else {
            // Local targeting mode: just activate edges flowing into this node
            ws.send(JSON.stringify({
              type: 'localTargeting',
              targetNodeId: nodeId,
              token: token
            }));
          }
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
    ev.preventDefault();
  });

  // Keyboard shortcuts: only support Escape to cancel transient modes
  window.addEventListener('keydown', (ev) => {
    if (gameEnded) return;
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) return;
    if (ev.key.toLowerCase() === 'escape') {
      if (kingSelectionActive) {
        clearKingSelection();
        return;
      }
      if (activeAbility) {
        activeAbility = null;
        clearBridgeSelection();
        hideBridgeCostDisplay();
      }
      if (pointerLockActive) {
        releaseVirtualCursor();
      }
    }
  });

  // Mouse move: handle hover effects
  window.addEventListener('mousemove', (ev) => {
    if (pointerLockActive) {
      const movementX = Number.isFinite(ev.movementX) ? ev.movementX : (Number.isFinite(ev.mozMovementX) ? ev.mozMovementX : (Number.isFinite(ev.webkitMovementX) ? ev.webkitMovementX : 0));
      const movementY = Number.isFinite(ev.movementY) ? ev.movementY : (Number.isFinite(ev.mozMovementY) ? ev.mozMovementY : (Number.isFinite(ev.webkitMovementY) ? ev.webkitMovementY : 0));
      const allowWarp = shouldWarpCursor();
      if (allowWarp) {
        const wrapped = applyWarpWrapToScreen(virtualCursorScreenX + movementX, virtualCursorScreenY + movementY);
        virtualCursorScreenX = wrapped.x;
        virtualCursorScreenY = wrapped.y;
      } else {
        const clamped = clampCursorToViewport(virtualCursorScreenX + movementX, virtualCursorScreenY + movementY);
        virtualCursorScreenX = clamped.x;
        virtualCursorScreenY = clamped.y;
      }
      lastPointerClientX = virtualCursorScreenX;
      lastPointerClientY = virtualCursorScreenY;
    } else {
      lastPointerClientX = ev.clientX;
      lastPointerClientY = ev.clientY;
      virtualCursorScreenX = ev.clientX;
      virtualCursorScreenY = ev.clientY;
    }

    updateMouseWorldFromVirtualCursor();

    if (replayMode) {
      if (hoveredNodeId !== null || hoveredEdgeId !== null) {
        hoveredNodeId = null;
        hoveredEdgeId = null;
        redrawStatic();
      }
      return;
    }
    if (gameEnded) return;
    const menuVisible = !document.getElementById('menu')?.classList.contains('hidden');
    if (menuVisible) return; // Don't handle hover when menu is visible
    
    const wx = mouseWorldX;
    const wy = mouseWorldY;
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

    if (kingSelectionActive && view) {
      const [pointerScreenX, pointerScreenY] = worldToScreen(wx, wy);
      const hoveredTargetId = pickKingMoveTargetFromScreen(pointerScreenX, pointerScreenY);
      if (hoveredTargetId !== kingMoveTargetHoveredId) {
        kingMoveTargetHoveredId = hoveredTargetId;
        needsRedraw = true;
      }
    } else if (!kingSelectionActive && kingMoveTargetHoveredId !== null) {
      kingMoveTargetHoveredId = null;
      needsRedraw = true;
    }

    // Avoid excessive redraws when numbers & targeting overlays are off and nothing changed
    const overlaysActive = Boolean((persistentNumbers) || (persistentTargeting && currentTargetNodeId !== null) || moneyIndicators.length > 0);
    if (!needsRedraw && !overlaysActive) {
      return;
    }

    if (needsRedraw) {
      redrawStatic();
    }
  });

  // Mouse down: detect king crown clicks separately
  window.addEventListener('mousedown', (ev) => {
    if (!kingSelectionActive) {
      const myKingNodeId = kingNodesByPlayer.get(myPlayerId);
      if (
        phase === 'playing' &&
        myPicked &&
        !kingSelectionActive &&
        myKingNodeId != null &&
        myKingNodeId !== undefined
      ) {
        const kingNode = nodes.get(myKingNodeId);
        if (kingNode) {
          const [screenX, screenY] = worldToScreen(kingNode.x, kingNode.y);
          const [pointerX, pointerY] = getPointerScreenCoords(ev);
          const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
          const crownRadius = computeStandardKingCrownRadius(baseScale);
          const layout = computePlacedKingCrownLayout(screenX, screenY, crownRadius);
          const withinBounds = layout && isPointWithinRect(pointerX, pointerY, layout.bounds);
          const dx = pointerX - (layout?.centerX ?? screenX);
          const dy = pointerY - (layout?.centerY ?? screenY);
          const withinRadius = layout ? (dx * dx + dy * dy <= layout.hitRadius * layout.hitRadius) : false;
          if (withinBounds || withinRadius) {
            startKingSelection(myKingNodeId);
            ev.preventDefault();
            ev.stopPropagation();
            return;
          }
        }
      }
      return;
    }

    if (kingMovePendingDestinationId != null) {
      return;
    }

    const [pointerScreenX, pointerScreenY] = getPointerScreenCoords(ev);
    const crownTargetId = pickKingMoveTargetFromScreen(pointerScreenX, pointerScreenY);
    if (crownTargetId != null) {
      sendKingMoveRequest(crownTargetId);
      ev.preventDefault();
      ev.stopPropagation();
    } else {
      clearKingSelection();
    }
  });

  function screenToWorld(px, py) {
    if (!view) return [px, py];
    return [(px - view.offsetX) / view.scaleX, (py - view.offsetY) / view.scaleY];
  }

  function getPointerScreenCoords(ev) {
    // When pointer lock is active (warp mode), always use virtual cursor position
    if (pointerLockActive) {
      return [virtualCursorScreenX, virtualCursorScreenY];
    }
    if (!ev) {
      return [virtualCursorScreenX, virtualCursorScreenY];
    }
    if (typeof ev.clientX === 'number' && typeof ev.clientY === 'number') {
      return [ev.clientX, ev.clientY];
    }
    const touch = ev.touches && ev.touches[0];
    if (touch && typeof touch.clientX === 'number' && typeof touch.clientY === 'number') {
      return [touch.clientX, touch.clientY];
    }
    return [virtualCursorScreenX, virtualCursorScreenY];
  }

  function handleAbilityClick(abilityName) {
    if (isReplayActive()) return;
    if (myEliminated || gameEnded) return;
    // Allow abilities during playing phase
    
    const abilities = IS_LEGACY_CLIENT
      ? { 'bridge1way': { cost: 4 } }
      : { 'bridge1way': { cost: 4 }, 'destroy': { cost: 2 } };
    
    const ability = abilities[abilityName];
    if (!ability) return;
    
    // Toggle ability activation
    if (activeAbility === abilityName) {
      // Deactivate
      activeAbility = null;
      clearBridgeSelection();
      hideBridgeCostDisplay();
    } else if (abilityName === 'bridge1way') {
      // Activate bridge building
      activeAbility = abilityName;
      clearBridgeSelection();
    } else if (abilityName === 'destroy') {
      // Activate destroy mode
      activeAbility = abilityName;
      clearBridgeSelection(); // reuse for destroy node selection
      hideBridgeCostDisplay();
    }
    // Placeholder abilities do nothing for now
  }



  function syncGemCountsFromPayload(payload) {
    if (!Array.isArray(payload)) {
      playerStats.forEach((stats) => {
        if (stats) stats.gems = createEmptyGemCounts();
      });
      return;
    }

    const seen = new Set();
    payload.forEach((entry) => {
      let pid;
      let rawCounts;
      if (Array.isArray(entry)) {
        [pid, rawCounts] = entry;
      } else if (entry && typeof entry === 'object') {
        pid = entry.playerId ?? entry.id ?? entry.pid ?? entry.player ?? entry[0];
        rawCounts = entry.counts ?? entry.gems ?? entry.values ?? entry.data ?? entry[1];
      } else {
        return;
      }
      const id = Number(pid);
      if (!Number.isFinite(id)) return;
      seen.add(id);
      const stats = ensurePlayerStats(id);
      const nextCounts = createEmptyGemCounts();
      if (rawCounts && typeof rawCounts === 'object') {
        Object.entries(rawCounts).forEach(([key, value]) => {
          const normalizedKey = normalizeGemKey(key);
          if (!normalizedKey) return;
          const numeric = Number(value);
          nextCounts[normalizedKey] = Number.isFinite(numeric) && numeric > 0 ? Math.max(0, Math.floor(numeric)) : 0;
        });
      }
      stats.gems = nextCounts;
    });

    playerStats.forEach((stats, id) => {
      if (!stats) return;
      if (seen.has(id)) return;
      stats.gems = createEmptyGemCounts();
    });
  }

  function updateGemCountsDisplay() {
    if (!gemCountsDisplay || gemCountLabels.size === 0) {
      gemCountsDisplay = document.getElementById('gemCountsDisplay');
      if (gemCountsDisplay && !gemCountsClickHandlerBound) {
        gemCountsDisplay.addEventListener('click', handleGemCountsClick);
        gemCountsClickHandlerBound = true;
      }
      gemCountLabels.clear();
      if (gemCountsDisplay) {
        GEM_TYPE_ORDER.forEach((key) => {
          const container = gemCountsDisplay.querySelector(`[data-gem="${key}"]`);
          if (!container) return;
          const numberEl = container.querySelector('.gem-number');
          if (numberEl) gemCountLabels.set(key, numberEl);
        });
      }
    }

    if (!gemCountsDisplay || gemCountLabels.size === 0) {
      return;
    }

    let targetId = Number.isFinite(myPlayerId) ? myPlayerId : NaN;
    if (!Number.isFinite(targetId)) {
      const storedRaw = localStorage.getItem('myPlayerId');
      if (storedRaw != null) {
        const storedValue = Number(storedRaw);
        if (Number.isFinite(storedValue)) {
          targetId = storedValue;
        }
      }
    }

    let counts = createEmptyGemCounts();
    if (Number.isFinite(targetId) && (playerStats.has(targetId) || players.has(targetId))) {
      const stats = ensurePlayerStats(targetId);
      const maybeCounts = stats && stats.gems;
      if (maybeCounts && typeof maybeCounts === 'object') {
        counts = maybeCounts;
      }
    }

    GEM_TYPE_ORDER.forEach((key) => {
      const label = gemCountLabels.get(key);
      if (!label) return;
      const numeric = Number(counts[key]) || 0;
      label.textContent = String(Math.max(0, Math.floor(numeric)));
    });
    let uiUpdated = false;
    if (brassGemModeActive && !canActivateBrassGemMode()) {
      setBrassGemModeActive(false);
      uiUpdated = true;
    }
    if (rageGemModeActive && !canActivateRageGemMode()) {
      setRageGemModeActive(false);
      uiUpdated = true;
    }
    if (reverseGemModeActive && !canActivateReverseGemMode()) {
      setReverseGemModeActive(false);
      uiUpdated = true;
    }
    if (warpGemModeActive && !canActivateWarpGemMode()) {
      setWarpGemModeActive(false);
      uiUpdated = true;
    }
    if (!uiUpdated) {
      updateGemModeUi();
    }
  }


  function updateGoldBar() {
    const val = Math.max(0, goldValue || 0);
    if (goldDisplay) {
      if (sandboxModeEnabled()) {
        goldDisplay.textContent = '$∞';
      } else {
        goldDisplay.textContent = `$${formatCost(val)}`;
      }
    }
  }

  function updateQuitButtonLabel() {
    if (!quitButton) return;
    if (replayMode || replayStartPending || replaySessionActive) {
      quitButton.textContent = 'Return';
      return;
    }
    quitButton.textContent = (gameEnded || myEliminated) ? 'Quit' : 'Forfeit';
  }

  function updateSandboxButtonVisibility() {
    if (!sandboxResetButton && !sandboxClearButton) return;
    const menuEl = document.getElementById('menu');
    const menuVisible = menuEl ? !menuEl.classList.contains('hidden') : false;
    const inSandbox = sandboxModeEnabled();
    const connected = !!(ws && ws.readyState === WebSocket.OPEN);
    const shouldShow = inSandbox && !menuVisible && !replayMode;
    const displayStyle = shouldShow ? 'block' : 'none';

    if (sandboxResetButton) {
      sandboxResetButton.style.display = displayStyle;
      sandboxResetButton.disabled = !shouldShow || !connected;
      sandboxResetButton.style.opacity = sandboxResetButton.disabled ? '0.6' : '1';
      sandboxResetButton.style.cursor = sandboxResetButton.disabled ? 'not-allowed' : 'pointer';
    }

    if (sandboxClearButton) {
      sandboxClearButton.style.display = displayStyle;
      sandboxClearButton.disabled = !shouldShow || !connected;
      sandboxClearButton.style.opacity = sandboxClearButton.disabled ? '0.6' : '1';
      sandboxClearButton.style.cursor = sandboxClearButton.disabled ? 'not-allowed' : 'pointer';
    }
  }

  function requestSandboxReset() {
    if (!sandboxModeEnabled()) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      showErrorMessage('Reconnect before resetting sandbox');
      return;
    }
    const token = localStorage.getItem('token');
    if (!token) return;
    const payload = {
      type: 'sandboxReset',
      token,
      autoExpand: persistentAutoExpand,
      autoAttack: persistentAutoAttack,
      mode: MODE_QUEUE_KEY,
      settings: buildModeSettingsPayload(),
    };
    ws.send(JSON.stringify(payload));
  }

  function requestSandboxClear() {
    if (!sandboxModeEnabled()) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      showErrorMessage('Reconnect before clearing sandbox');
      return;
    }
    const token = localStorage.getItem('token');
    if (!token) return;
    ws.send(JSON.stringify({ type: 'sandboxClearBoard', token }));
  }

  function showLobby() {
    const lobby = document.getElementById('lobby');
    if (lobby) lobby.style.display = 'block';
  }

  function hideLobby() {
    const lobby = document.getElementById('lobby');
    if (lobby) lobby.style.display = 'none';
  }

  function setLobbyStatus(message) {
    const lobby = document.getElementById('lobby');
    if (lobby) lobby.textContent = message || '';
  }

  function ensurePlayerStats(id) {
    if (!playerStats.has(id)) {
      playerStats.set(id, createDefaultPlayerStats());
    }
    const stats = playerStats.get(id);
    if (!stats.gems || typeof stats.gems !== 'object') {
      stats.gems = createEmptyGemCounts();
    } else {
      GEM_TYPE_ORDER.forEach((key) => {
        if (!Object.prototype.hasOwnProperty.call(stats.gems, key)) {
          stats.gems[key] = 0;
          return;
        }
        const value = Number(stats.gems[key]) || 0;
        stats.gems[key] = value >= 0 ? Math.floor(value) : 0;
      });
    }
    return stats;
  }

  function updateProgressBar() {
    if (!progressBarInner) return;
    if (!progressNameContainer) {
      progressNameContainer = document.getElementById('progressBarNames');
    }

    const menuEl = document.getElementById('menu');
    const menuVisible = menuEl ? !menuEl.classList.contains('hidden') : false;
    if (menuVisible) {
      progressBarInner.innerHTML = '';
      progressSegments.clear();
      if (progressBar) progressBar.style.display = 'none';
      if (progressMarkerLeft) progressMarkerLeft.style.display = 'none';
      if (progressMarkerRight) progressMarkerRight.style.display = 'none';
      if (progressNameContainer) {
        progressNameContainer.innerHTML = '';
        progressNameContainer.style.display = 'none';
      }
      progressNameSegments.clear();
      return;
    }

    if (winCondition !== 'dominate') {
      progressBarInner.innerHTML = '';
      progressSegments.clear();
      const notice = document.createElement('div');
      notice.className = 'progressSegment winConNotice';
      notice.textContent = 'Win-Con: King · Capture the crowned node';
      notice.style.flex = '1';
      progressBarInner.appendChild(notice);
      progressBarInner.style.justifyContent = 'center';
      if (progressBar) progressBar.style.display = nodes.size > 0 ? 'block' : 'none';
      if (progressMarkerLeft) progressMarkerLeft.style.display = 'none';
      if (progressMarkerRight) progressMarkerRight.style.display = 'none';
      if (progressNameContainer) {
        progressNameContainer.innerHTML = '';
        progressNameContainer.style.display = 'none';
      }
      progressNameSegments.clear();
      return;
    }

    const orderedIds = playerOrder.length ? playerOrder : Array.from(players.keys()).sort((a, b) => a - b);
    const activeIds = orderedIds.filter((id) => players.has(id) && !eliminatedPlayers.has(id));

    if (!activeIds.length || totalNodes <= 0) {
      progressBarInner.innerHTML = '';
      progressSegments.clear();
      if (progressBar) progressBar.style.display = 'none';
      if (progressMarkerLeft) progressMarkerLeft.style.display = 'none';
      if (progressMarkerRight) progressMarkerRight.style.display = 'none';
      progressNameSegments.clear();
      if (progressNameContainer) {
        progressNameContainer.innerHTML = '';
        progressNameContainer.style.display = 'none';
      }
      return;
    }

    if (progressBar) progressBar.style.display = 'block';
    if (progressNameContainer) progressNameContainer.style.display = 'flex';

    const denominator = Math.max(totalNodes, 1);
    const seen = new Set();

    progressBarInner.style.justifyContent = activeIds.length === 2 ? 'space-between' : 'flex-start';
    if (progressNameContainer) {
      progressNameContainer.style.justifyContent = progressBarInner.style.justifyContent;
    }

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
        labelEl.textContent = `$${formatCost(stats.gold || 0)}`;
      }

      let nameCell = progressNameSegments.get(id);
      if (!nameCell && progressNameContainer) {
        nameCell = document.createElement('div');
        nameCell.className = 'progressNameCell';
        progressNameSegments.set(id, nameCell);
        progressNameContainer.appendChild(nameCell);
      }
      if (nameCell) {
        const displayName = info.name || `Player ${id}`;
        // Truncate name to max 10 characters
        const truncatedName = displayName.length > 10 ? displayName.substring(0, 10) : displayName;
        nameCell.textContent = truncatedName;
        nameCell.style.order = index;
        nameCell.style.flex = `0 0 ${Math.max(percent, 0)}%`;
        nameCell.style.color = primary || '#ffffff';
        nameCell.dataset.playerId = String(id);
      }
    });

    progressSegments.forEach((segment, id) => {
      if (!seen.has(id)) {
        if (segment.parentElement === progressBarInner) {
          progressBarInner.removeChild(segment);
        }
        progressSegments.delete(id);
        const nameCell = progressNameSegments.get(id);
        if (nameCell && nameCell.parentElement === progressNameContainer) {
          progressNameContainer.removeChild(nameCell);
        }
        progressNameSegments.delete(id);
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
    if (isReplayActive()) {
      stopReplaySession();
    }
    releaseVirtualCursor();
    clearAllPrePipes('menu');
    kingNodesByPlayer.clear();
    replayMode = false;
    replayStartPending = false;
    replaySessionActive = false;
    setReplayControlsDisabled(false);
    ensureReplaySpeedElements();
    replaySpeedValue = 1;
    if (replaySpeedInput) replaySpeedInput.value = '1';
    updateReplaySpeedLabel();
    updateReplaySpeedUI();

    const menu = document.getElementById('menu');
    const homeButtons = document.querySelector('.button-container');
    const playBtnEl = document.getElementById('playBtn');

    hideLobby();
    setLobbyStatus('');
    if (menu) menu.classList.remove('hidden');
    if (homeButtons) homeButtons.style.display = 'flex';
    if (playBtnEl) playBtnEl.style.display = 'block';
    setModeSelectorVisibility(true);
    if (lobbyBackButton) lobbyBackButton.style.display = 'none';

    if (quitButton) quitButton.style.display = 'none';
    if (saveReplayWrapper) saveReplayWrapper.style.display = 'none';
    if (reviewDropdownButton) reviewDropdownButton.style.display = 'none';
    if (overlayMsg) overlayMsg.style.display = 'none';
    if (goldDisplay) goldDisplay.style.display = 'none';
    if (progressBar) progressBar.style.display = 'none';
    if (progressNameContainer) {
      progressNameContainer.style.display = 'none';
      progressNameContainer.innerHTML = '';
    }
    progressNameSegments.clear();
    if (progressMarkerLeft) progressMarkerLeft.style.display = 'none';
    if (progressMarkerRight) progressMarkerRight.style.display = 'none';
    hideTimerDisplay();
    const togglesPanel = document.getElementById('togglesPanel');
    if (togglesPanel) togglesPanel.style.display = settingsOpen ? 'grid' : 'none';
    updateSandboxButtonVisibility();

    updateHomeAutoExpandToggle();
    updateHomeAutoAttackToggle();
    updateHomeNumbersToggle();
    updateHomeEdgeFlowToggle();

    reviewReplayActive = false;
    reviewReplayLastData = null;
    reviewReplayLastFilename = null;
    updateReviewReplayDownloadButton();

    // Clean up juice text objects
    nodeJuiceTexts.forEach(text => {
      if (text) text.destroy();
    });
    nodeJuiceTexts.clear();
    // Clean up edge flow text objects
    edgeFlowTexts.forEach(text => {
      if (text) text.destroy();
    });
    edgeFlowTexts.clear();
    
    // Clear targeting indicator
    currentTargetNodeId = null;
    currentTargetSetTime = null;
    resetHiddenStartState();
    
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
    clearBridgeSelection();
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

  function hideTimerDisplay() {
    gameStartTime = null;
    if (!timerDisplay) return;
    timerDisplay.style.display = 'none';
    timerDisplay.style.color = '#ffffff';
    const minutes = Math.floor(gameDuration / 60);
    const seconds = Math.floor(gameDuration % 60);
    timerDisplay.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
  }

  function syncTimerFromServer(remainingSeconds, durationSeconds) {
    if (!Number.isFinite(remainingSeconds)) return;
    if (Number.isFinite(durationSeconds) && durationSeconds > 0) {
      gameDuration = durationSeconds;
    }

    const clampedRemaining = Math.max(0, Math.min(gameDuration, remainingSeconds));
    const elapsed = gameDuration - clampedRemaining;
    gameStartTime = Date.now() - elapsed * 1000;

    if (timerDisplay) {
      timerDisplay.style.display = 'block';
      timerDisplay.style.color = '#ffffff';
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

  function updateAutoAttackToggle() {
    if (!autoAttackToggle) return;

    const toggleSwitch = autoAttackToggle.querySelector('.toggle-switch');
    if (toggleSwitch) {
      if (myAutoAttack) {
        toggleSwitch.classList.add('enabled');
      } else {
        toggleSwitch.classList.remove('enabled');
      }
    }
  }

  function updateNumbersToggle() {
    if (!numbersToggle) return;
    
    const toggleSwitch = numbersToggle.querySelector('.toggle-switch');
    if (toggleSwitch) {
      if (persistentNumbers) {
        toggleSwitch.classList.add('enabled');
      } else {
        toggleSwitch.classList.remove('enabled');
      }
    }
  }

  function updateEdgeFlowToggle() {
    if (!edgeFlowToggle) return;
    const toggleSwitch = edgeFlowToggle.querySelector('.toggle-switch');
    if (toggleSwitch) {
      if (persistentEdgeFlow) {
        toggleSwitch.classList.add('enabled');
      } else {
        toggleSwitch.classList.remove('enabled');
      }
    }
  }

  function updateHomeEdgeFlowToggle() {
    const toggleSwitch = document.querySelector('#edgeFlowToggle .toggle-switch');
    if (!toggleSwitch) return;
    if (persistentEdgeFlow) toggleSwitch.classList.add('enabled');
    else toggleSwitch.classList.remove('enabled');
  }

  function updatePreMoveToggle() {
    if (!preMoveToggle) return;
    const toggleSwitch = preMoveToggle.querySelector('.toggle-switch');
    if (!toggleSwitch) return;
    if (persistentPreMove) toggleSwitch.classList.add('enabled');
    else toggleSwitch.classList.remove('enabled');
  }

  function updateHomePreMoveToggle() {
    const toggleSwitch = document.querySelector('#preMoveToggle .toggle-switch');
    if (!toggleSwitch) return;
    if (persistentPreMove) toggleSwitch.classList.add('enabled');
    else toggleSwitch.classList.remove('enabled');
  }

  function isPreMoveEnabled() {
    if (!persistentPreMove) return false;
    if (replayMode || replayStartPending || replaySessionActive) return false;
    return true;
  }

  function getPrePipeKey(fromNodeId, toNodeId) {
    return `${fromNodeId}->${toNodeId}`;
  }

  function clearAllPrePipes(reason = '', options = {}) {
    if (!prePipes.size) return;
    prePipes.clear();
    prePipeKeyIndex.clear();
    if (!options.skipRedraw && graphicsEdges) {
      redrawStatic();
    }
  }

  function removePrePipeById(id, options = {}) {
    const record = prePipes.get(id);
    if (!record) return;
    prePipes.delete(id);
    const key = getPrePipeKey(record.fromNodeId, record.toNodeId);
    if (prePipeKeyIndex.get(key) === id) {
      prePipeKeyIndex.delete(key);
    }
    if (options.reason === 'brass') {
      showPrePipeIndicator(record, 'Blocked', '#f9aa7a');
    } else if (options.reason === 'player') {
      showPrePipeIndicator(record, 'Canceled', '#d4dae4');
    }
    if (!options.skipRedraw && graphicsEdges) {
      redrawStatic();
    }
  }

  function showPrePipeIndicator(prePipe, text, color) {
    if (!prePipe) return;
    const fromNode = nodes.get(prePipe.fromNodeId);
    const toNode = nodes.get(prePipe.toNodeId);
    if (!fromNode || !toNode) return;
    const midX = (fromNode.x + toNode.x) / 2;
    const midY = (fromNode.y + toNode.y) / 2;
    createMoneyIndicator(midX, midY, text, color || '#dfe3ea', 1400, {
      strokeColor: 'rgba(0,0,0,0.4)',
      strokeThickness: 0.6,
      worldOffset: -0.3,
      floatDistance: 18,
    });
  }

  function queuePrePipe(fromNodeId, toNodeId, options = {}) {
    if (!isPreMoveEnabled()) return;
    if (fromNodeId == null || toNodeId == null || fromNodeId === toNodeId) return;
    const fromNode = nodes.get(fromNodeId);
    const toNode = nodes.get(toNodeId);
    if (!fromNode || !toNode) return;

    const key = getPrePipeKey(fromNodeId, toNodeId);
    let record = null;
    if (prePipeKeyIndex.has(key)) {
      const existingId = prePipeKeyIndex.get(key);
      record = prePipes.get(existingId) || null;
    }
    const isNewRecord = !record;

    const warpPreference = options.warpPreference && typeof options.warpPreference === 'object'
      ? { ...options.warpPreference }
      : null;
    const waitingForOwnership = !!options.waitingForOwnership;
    const waitingForGold = !!options.waitingForGold;
    const normalizedPipeType = typeof options.pipeType === 'string'
      ? options.pipeType.trim().toLowerCase()
      : 'normal';
    const pipeType = ['gold', 'rage', 'reverse'].includes(normalizedPipeType) ? normalizedPipeType : 'normal';
    const basePlayerColor = ownerToColor(myPlayerId) || 0xffffff;
    const baseColorCss = toCssColor(basePlayerColor);
    const outlineCss = lightenColor(baseColorCss, 0.75);
    const outlineColor = hexToInt(outlineCss);

    if (record) {
      record.waitingForOwnership = waitingForOwnership || record.waitingForOwnership;
      record.waitingForGold = waitingForGold || record.waitingForGold;
      record.warpPreference = warpPreference || record.warpPreference;
      record.pipeType = pipeType;
    } else {
      const id = nextPrePipeId++;
      record = {
        id,
        fromNodeId,
        toNodeId,
        pipeType,
        warpPreference,
        waitingForOwnership,
        waitingForGold,
        createdAt: Date.now(),
        lastKnownCost: Number(options.estimatedCost) || 0,
        outlineColor,
      };
      prePipes.set(id, record);
      prePipeKeyIndex.set(key, id);
    }

    if (isNewRecord) {
      showPrePipeIndicator(record, options.message || 'Queued', '#dce2ec');
    }
    updatePrePipesState();
    if (graphicsEdges) {
      redrawStatic();
    }
  }

  function getPrePipeSegments(prePipe, options = {}) {
    const includeMeta = !!options.includeMeta;
    if (!prePipe) return [];
    const fromNode = nodes.get(prePipe.fromNodeId);
    const toNode = nodes.get(prePipe.toNodeId);
    if (!fromNode || !toNode) return [];
    const path = computeWarpBridgeSegments(fromNode, toNode, prePipe.warpPreference || {});
    const segments = [];
    if (!path || !Array.isArray(path.segments) || !path.segments.length) {
      const entry = { sx: fromNode.x, sy: fromNode.y, ex: toNode.x, ey: toNode.y };
      if (includeMeta) {
        entry.startNode = fromNode;
        entry.endNode = toNode;
      }
      segments.push(entry);
      return segments;
    }
    path.segments.forEach((segment) => {
      if (!segment || !segment.start || !segment.end) return;
      const sx = Number(segment.start.x);
      const sy = Number(segment.start.y);
      const ex = Number(segment.end.x);
      const ey = Number(segment.end.y);
      if (![sx, sy, ex, ey].every((value) => Number.isFinite(value))) return;
      const entry = { sx, sy, ex, ey };
      if (includeMeta) {
        entry.startNode = segment.start.node || null;
        entry.endNode = segment.end.node || null;
      }
      segments.push(entry);
    });
    return segments;
  }

  function prePipeBlockedByBrass(prePipe) {
    const segments = getPrePipeSegments(prePipe);
    if (!segments.length) return false;
    let blocked = false;
    edges.forEach((edge) => {
      if (blocked || !edge || edge.removing) return;
      if (edge.pipeType !== 'gold') return;
      if (edge.source === prePipe.fromNodeId || edge.source === prePipe.toNodeId || edge.target === prePipe.fromNodeId || edge.target === prePipe.toNodeId) {
        return;
      }
      const edgeSegments = getEdgeWarpSegments(edge);
      if (!edgeSegments.length) return;
      for (const cand of segments) {
        for (const existing of edgeSegments) {
          if (![cand, existing].every(Boolean)) continue;
          if ([cand.sx, cand.sy, cand.ex, cand.ey, existing.sx, existing.sy, existing.ex, existing.ey].every((value) => Number.isFinite(value)) &&
            segmentsIntersect(cand.sx, cand.sy, cand.ex, cand.ey, existing.sx, existing.sy, existing.ex, existing.ey)) {
            blocked = true;
            return;
          }
        }
      }
    });
    return blocked;
  }

  function attemptSendPrePipe(prePipe, forcedCost) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    const token = localStorage.getItem('token');
    if (!token) return false;
    const fromNode = nodes.get(prePipe.fromNodeId);
    const toNode = nodes.get(prePipe.toNodeId);
    if (!fromNode || !toNode) return false;
    const warpInfo = buildWarpInfoForBridge(fromNode, toNode, prePipe.warpPreference || {});
    const applyBrassCost = prePipe.pipeType === 'gold' && brassPipesDoubleCost();
    const cost = Number.isFinite(forcedCost)
      ? forcedCost
      : calculateBridgeCost(fromNode, toNode, applyBrassCost, prePipe.warpPreference || {});
    const payload = {
      type: 'buildBridge',
      fromNodeId: prePipe.fromNodeId,
      toNodeId: prePipe.toNodeId,
      cost,
      warpInfo,
      token,
      pipeType: prePipe.pipeType || 'normal',
    };
    ws.send(JSON.stringify(payload));
    showPrePipeIndicator(prePipe, 'Building', '#b7f1c0');
    return true;
  }

  function updatePrePipesState() {
    if (!prePipes.size) return false;
    if (!isPreMoveEnabled()) {
      clearAllPrePipes('disabled', { skipRedraw: true });
      return true;
    }
    const removals = [];
    prePipes.forEach((prePipe, id) => {
      const fromNode = nodes.get(prePipe.fromNodeId);
      const toNode = nodes.get(prePipe.toNodeId);
      if (!fromNode || !toNode) {
        removals.push({ id, reason: 'missing' });
        return;
      }
      const applyBrassCost = prePipe.pipeType === 'gold' && brassPipesDoubleCost();
      const cost = calculateBridgeCost(fromNode, toNode, applyBrassCost, prePipe.warpPreference || {});
      prePipe.lastKnownCost = cost;
      prePipe.waitingForOwnership = pipeStartRequiresOwnership() && fromNode.owner !== myPlayerId;
      const infiniteMoney = sandboxModeEnabled();
      prePipe.waitingForGold = !infiniteMoney && goldValue < cost;
      if (prePipeBlockedByBrass(prePipe)) {
        removals.push({ id, reason: 'brass' });
        return;
      }
      if (!prePipe.waitingForOwnership && !prePipe.waitingForGold) {
        const sent = attemptSendPrePipe(prePipe, cost);
        if (sent) {
          removals.push({ id, reason: 'sent' });
        }
      }
    });

    let changed = removals.length > 0;
    removals.forEach(({ id, reason }) => {
      if (reason === 'brass') {
        removePrePipeById(id, { reason: 'brass', skipRedraw: true });
      } else {
        removePrePipeById(id, { skipRedraw: true });
      }
    });
    if (changed && graphicsEdges) {
      redrawStatic();
    }
    return changed;
  }

  function drawPrePipes() {
    if (!prePipes.size) return;
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    prePipes.forEach((prePipe) => {
      const segments = getPrePipeSegments(prePipe, { includeMeta: true });
      if (!segments.length) return;
      segments.forEach((segment, index) => {
        drawPrePipeSegment(segment, prePipe, index, baseScale);
      });
    });
  }

  function drawPrePipeSegment(segment, prePipe, segmentIndex, baseScale) {
    if (!segment) return;
    const [sx0, sy0] = worldToScreen(segment.sx, segment.sy);
    const [tx0, ty0] = worldToScreen(segment.ex, segment.ey);
    const dx0 = tx0 - sx0;
    const dy0 = ty0 - sy0;
    const len0 = Math.max(1, Math.hypot(dx0, dy0));
    const ux0 = dx0 / len0;
    const uy0 = dy0 / len0;

    const startRadius = segment.startNode ? endpointRadius({ node: segment.startNode }, baseScale) : 0;
    const endRadius = segment.endNode ? endpointRadius({ node: segment.endNode }, baseScale) : 0;

    const sx = sx0 + ux0 * startRadius;
    const sy = sy0 + uy0 * startRadius;
    const tx = tx0 - ux0 * endRadius;
    const ty = ty0 - uy0 * endRadius;

    const dx = tx - sx;
    const dy = ty - sy;
    const len = Math.max(1, Math.hypot(dx, dy));
    const ux = dx / len;
    const uy = dy / len;

    const normalX = -uy;
    const normalY = ux;

    const triH = PIPE_TRIANGLE_HEIGHT;
    const triW = PIPE_TRIANGLE_WIDTH;
    const pipeType = prePipe?.pipeType || 'normal';
    const spacing = Math.max(1, pipeSpacingForType(pipeType));
    const count = Math.max(1, Math.floor(len / spacing));
    const actualSpacing = len / count;
    const angle = Math.atan2(uy, ux);
    const waitingOwnership = !!prePipe.waitingForOwnership;
    const waitingGold = !!prePipe.waitingForGold;
    const outlineColor = Number.isFinite(prePipe.outlineColor) ? prePipe.outlineColor : PRE_PIPE_OUTLINE_COLOR;
    const outlineAlpha = waitingOwnership ? 0.8 : (waitingGold ? 0.55 : 0.3);
    const wave = Math.sin(animationTime * PRE_PIPE_SHAKE_SPEED);

    for (let i = 0; i < count; i++) {
      const alternatingSign = (i % 2 === 0) ? 1 : -1;
      const sway = PRE_PIPE_SHAKE_AMPLITUDE * alternatingSign * wave;
      const offsetX = normalX * sway;
      const offsetY = normalY * sway;
      const cx = sx + (i + 0.5) * actualSpacing * ux + offsetX;
      const cy = sy + (i + 0.5) * actualSpacing * uy + offsetY;
      drawPrePipeTriangle(cx, cy, triW, triH, angle, outlineColor, outlineAlpha, pipeType);
    }
  }

  function drawPrePipeTriangle(cx, cy, baseW, height, angle, outlineColor, outlineAlpha, pipeType = 'normal') {
    const drawInstance = (centerX, centerY) => {
      const shape = createPipeShape(centerX, centerY, baseW, height, angle, pipeType);
      strokePipeShape(shape, 1.4, outlineColor, outlineAlpha);
    };

    forEachPipeOffset(pipeType, angle, (dx, dy) => {
      drawInstance(cx + dx, cy + dy);
    });
  }

  function pickPrePipeNear(wx, wy, maxDist) {
    if (!prePipes.size) return null;
    const maxD2 = maxDist * maxDist;
    let bestId = null;
    let bestD2 = maxD2;
    prePipes.forEach((prePipe, id) => {
      const segments = getPrePipeSegments(prePipe);
      segments.forEach((seg) => {
        const d2 = pointToSegmentDistanceSquared(wx, wy, seg.sx, seg.sy, seg.ex, seg.ey);
        if (d2 <= bestD2) {
          bestId = id;
          bestD2 = d2;
        }
      });
    });
    return bestId;
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
      const segments = getEdgeWarpSegments(e);
      if (!segments.length) continue;
      for (const seg of segments) {
        const d2 = pointToSegmentDistanceSquared(wx, wy, seg.sx, seg.sy, seg.ex, seg.ey);
        if (d2 <= bestD2) {
          bestId = id;
          bestD2 = d2;
        }
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

  function segmentsIntersect(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2) {
    const orientation = (px, py, qx, qy, rx, ry) => {
      const val = (qy - py) * (rx - qx) - (qx - px) * (ry - qy);
      if (Math.abs(val) < 1e-10) return 0;
      return val > 0 ? 1 : 2;
    };

    const onSegment = (px, py, qx, qy, rx, ry) => (
      qx <= Math.max(px, rx) + 1e-10 &&
      qx + 1e-10 >= Math.min(px, rx) &&
      qy <= Math.max(py, ry) + 1e-10 &&
      qy + 1e-10 >= Math.min(py, ry)
    );

    const o1 = orientation(ax1, ay1, ax2, ay2, bx1, by1);
    const o2 = orientation(ax1, ay1, ax2, ay2, bx2, by2);
    const o3 = orientation(bx1, by1, bx2, by2, ax1, ay1);
    const o4 = orientation(bx1, by1, bx2, by2, ax2, ay2);

    if (o1 !== o2 && o3 !== o4) return true;
    if (o1 === 0 && onSegment(ax1, ay1, bx1, by1, ax2, ay2)) return true;
    if (o2 === 0 && onSegment(ax1, ay1, bx2, by2, ax2, ay2)) return true;
    if (o3 === 0 && onSegment(bx1, by1, ax1, ay1, bx2, by2)) return true;
    if (o4 === 0 && onSegment(bx1, by1, ax2, ay2, bx2, by2)) return true;
    return false;
  }

  function drawBridgePreview(e, sNode, tNode) {
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    const warpPreference = getActiveWarpPreference();
    const path = computeWarpBridgeSegments(sNode, tNode, warpPreference);
    if (!path || !Array.isArray(path.segments) || !path.segments.length) {
      return;
    }

    const useBrassPipe = bridgePreviewWillBeBrass;
    const pipeType = determinePipeTypeForBridge(useBrassPipe);
    const cost = calculateBridgeCost(sNode, tNode, pipeType === 'gold' && brassPipesDoubleCost(), warpPreference);
    const canAfford = goldValue >= cost;
    let previewColor;
    if (pipeType === 'gold') {
      previewColor = canAfford ? BRASS_PIPE_COLOR : BRASS_PIPE_DIM_COLOR;
    } else if (pipeType === 'reverse') {
      previewColor = canAfford ? REVERSE_PIPE_COLOR : REVERSE_PIPE_DIM_COLOR;
    } else {
      previewColor = canAfford ? ownerToSecondaryColor(myPlayerId) : 0x000000;
    }

    for (const segment of path.segments) {
      drawBridgePreviewSegment(segment, previewColor, baseScale, pipeType);
    }
  }

  function endpointRadius(endpoint, baseScale) {
    if (!endpoint || !endpoint.node) return 0;
    return Math.max(1, calculateNodeRadius(endpoint.node, baseScale)) + 1;
  }

function drawBridgePreviewSegment(segment, color, baseScale, pipeType = 'normal') {
  if (!segment) return;
  const start = segment.start;
  const end = segment.end;
  if (!start || !end) return;

    const [sx0, sy0] = worldToScreen(start.x, start.y);
    const [tx0, ty0] = worldToScreen(end.x, end.y);
    const dx0 = tx0 - sx0;
    const dy0 = ty0 - sy0;
    const len0 = Math.max(1, Math.hypot(dx0, dy0));
    const ux0 = dx0 / len0;
    const uy0 = dy0 / len0;

    const startRadius = endpointRadius(start, baseScale);
    const endRadius = endpointRadius(end, baseScale);

    const sx = sx0 + ux0 * startRadius;
    const sy = sy0 + uy0 * startRadius;
    const tx = tx0 - ux0 * endRadius;
    const ty = ty0 - uy0 * endRadius;

    const dx = tx - sx;
    const dy = ty - sy;
    const len = Math.hypot(dx, dy);
    if (!Number.isFinite(len) || len < 2) return;

    const ux = dx / len;
    const uy = dy / len;
    const angle = Math.atan2(uy, ux);

    const triH = PIPE_TRIANGLE_HEIGHT;
    const triW = PIPE_TRIANGLE_WIDTH;
    const packedSpacing = Math.max(1, pipeSpacingForType(pipeType));
    const packedCount = Math.max(1, Math.floor(len / packedSpacing));
    const actualSpacing = len / packedCount;

    for (let i = 0; i < packedCount; i++) {
      const cx = sx + (i + 0.5) * actualSpacing * ux;
      const cy = sy + (i + 0.5) * actualSpacing * uy;
      const useBrass = pipeType === 'gold';
      drawPreviewTriangle(cx, cy, triW, triH, angle, color, useBrass, pipeType);
    }
}

function updateBrassPreviewIntersections() {
  brassPreviewIntersections.clear();
  xbPreviewBlockedByBrass = false;

  const gemMode = isMagicResourceModeActive();
  const modeIsXb = isXbModeActive();
  if (gemMode) {
    bridgePreviewWillBeBrass = bridgeIsBrass;
    return;
  }

  const modeIsCrossLike = isCrossLikeModeActive();
  const modeIsCross = isTrueCrossModeActive();

  if (activeAbility !== 'bridge1way' || bridgeFirstNode == null) {
    bridgePreviewWillBeBrass = bridgeIsBrass && modeIsCrossLike;
    return;
  }

  const firstNode = nodes.get(bridgeFirstNode);
  if (!firstNode) return;

  const shouldCheck = modeIsXb || (bridgeIsBrass && modeIsCrossLike);
  bridgePreviewWillBeBrass = bridgeIsBrass && modeIsCrossLike;
  if (!shouldCheck) return;

  let previewTarget = null;
  if (hoveredNodeId !== null && hoveredNodeId !== bridgeFirstNode) {
    const hoveredNode = nodes.get(hoveredNodeId);
    if (hoveredNode) previewTarget = hoveredNode;
  }
  if (!previewTarget) {
    previewTarget = {
      x: mouseWorldX,
      y: mouseWorldY,
      juice: 8.0,
      size: 8.0,
      owner: null,
      pendingGold: 0,
    };
  }

  const warpPreference = getActiveWarpPreference();
  const path = computeWarpBridgeSegments(firstNode, previewTarget, warpPreference);
  if (!path || !Array.isArray(path.segments) || !path.segments.length) return;

  const candidateSegments = [];
  for (const segment of path.segments) {
    if (!segment || !segment.start || !segment.end) continue;
    const sx = Number(segment.start.x);
    const sy = Number(segment.start.y);
    const ex = Number(segment.end.x);
    const ey = Number(segment.end.y);
    if ([sx, sy, ex, ey].every((value) => Number.isFinite(value))) {
      candidateSegments.push({ sx, sy, ex, ey });
    }
  }
  if (!candidateSegments.length) return;

  let willCross = false;
  let blockedByBrass = false;

  edges.forEach((edge, edgeId) => {
    if (!edge) return;
    if (edge.removing) return;
    if (edge.source === bridgeFirstNode || edge.target === bridgeFirstNode) return;
    if (hoveredNodeId != null && (edge.source === hoveredNodeId || edge.target === hoveredNodeId)) return;

    const existingSegments = getEdgeWarpSegments(edge);
    if (!existingSegments.length) return;

    for (const candidate of candidateSegments) {
      for (const existing of existingSegments) {
        if (![candidate, existing].every(Boolean)) continue;
        if ([candidate.sx, candidate.sy, candidate.ex, candidate.ey, existing.sx, existing.sy, existing.ex, existing.ey]
          .every((value) => Number.isFinite(value)) &&
          segmentsIntersect(candidate.sx, candidate.sy, candidate.ex, candidate.ey, existing.sx, existing.sy, existing.ex, existing.ey)
        ) {
          if (edge.pipeType === 'gold') {
            blockedByBrass = true;
          } else {
            brassPreviewIntersections.add(edgeId);
            willCross = true;
          }
          return;
        }
      }
    }
  });

  if (modeIsXb) {
    bridgePreviewWillBeBrass = willCross;
    xbPreviewBlockedByBrass = blockedByBrass;
  } else if (modeIsCrossLike) {
    if (!bridgeIsBrass) {
      brassPreviewIntersections.clear();
    }
    bridgePreviewWillBeBrass = bridgeIsBrass;
  }
}

function computeTrianglePoints(cx, cy, baseW, height, angle) {
  const halfW = baseW / 2;
  const tip = rotatePoint(cx + height / 2, cy, cx, cy, angle); // tip
  const baseLeft = rotatePoint(cx - height / 2, cy - halfW, cx, cy, angle); // base left
  const baseRight = rotatePoint(cx - height / 2, cy + halfW, cx, cy, angle); // base right
  return [tip, baseLeft, baseRight];
}

function pipeSpacingForType(pipeType) {
  if (pipeType === 'reverse') {
    return PIPE_TRIANGLE_HEIGHT * REVERSE_PIPE_SPACING_MULTIPLIER;
  }
  return PIPE_TRIANGLE_HEIGHT;
}

function computeDropletOutlinePoints(cx, cy, baseW, height, angle) {
  const tipForward = height / 2;
  const tailCenter = -height * 0.32;
  const baseRadiusTarget = Math.max(baseW * 0.68, 2.2);
  const dx = Math.max(0.001, tipForward - tailCenter);
  const tailRadius = Math.min(baseRadiusTarget, dx * 0.9);
  const arcSegments = 12;

  const localPoints = [];
  localPoints.push([tipForward, 0]);

  if (tailRadius >= dx) {
    // Fallback to simple oval if geometry degenerates
    const widest = baseW * 0.85;
    localPoints.push([tailCenter * 0.1, widest / 2]);
    localPoints.push([tailCenter, widest / 2]);
    localPoints.push([tailCenter, -widest / 2]);
    localPoints.push([tailCenter * 0.1, -widest / 2]);
  } else {
    const relX = (tailRadius * tailRadius) / dx;
    const relY = (tailRadius / dx) * Math.sqrt(Math.max(0, dx * dx - tailRadius * tailRadius));
    const tangentX = tailCenter + relX;
    const tangentY = relY;
    localPoints.push([tangentX, tangentY]);

    const angleStart = Math.atan2(relY, relX);
    const angleEnd = (Math.PI * 2) - angleStart;
    for (let i = 1; i <= arcSegments; i++) {
      const t = i / arcSegments;
      const theta = angleStart + (angleEnd - angleStart) * t;
      const lx = tailCenter + tailRadius * Math.cos(theta);
      const ly = tailRadius * Math.sin(theta);
      localPoints.push([lx, ly]);
    }
  }

  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  return localPoints.map(([lx, ly]) => [
    cx + lx * cos - ly * sin,
    cy + lx * sin + ly * cos,
  ]);
}

function createPipeShape(cx, cy, baseW, height, angle, pipeType = 'normal') {
  if (pipeType === 'reverse') {
    return { type: 'droplet', points: computeDropletOutlinePoints(cx, cy, baseW, height, angle) };
  }
  return { type: 'triangle', points: computeTrianglePoints(cx, cy, baseW, height, angle) };
}

function strokeClosedShape(points) {
  if (!Array.isArray(points) || !points.length) return;
  graphicsEdges.beginPath();
  graphicsEdges.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i++) {
    graphicsEdges.lineTo(points[i][0], points[i][1]);
  }
  graphicsEdges.closePath();
  graphicsEdges.strokePath();
}

function fillClosedShape(points) {
  if (!Array.isArray(points) || !points.length) return;
  graphicsEdges.beginPath();
  graphicsEdges.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i++) {
    graphicsEdges.lineTo(points[i][0], points[i][1]);
  }
  graphicsEdges.closePath();
  graphicsEdges.fillPath();
}

function strokePipeShape(shape, lineWidth, color, alpha) {
  if (!shape || !Array.isArray(shape.points)) return;
  graphicsEdges.lineStyle(lineWidth, color, alpha);
  if (shape.type === 'droplet') {
    strokeClosedShape(shape.points);
    return;
  }
  const [p1, p2, p3] = shape.points;
  graphicsEdges.strokeTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
}

function fillPipeShape(shape, color, alpha) {
  if (!shape || !Array.isArray(shape.points)) return;
  graphicsEdges.fillStyle(color, alpha);
  if (shape.type === 'droplet') {
    fillClosedShape(shape.points);
    return;
  }
  const [p1, p2, p3] = shape.points;
  graphicsEdges.fillTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
}

function forEachPipeOffset(pipeType, angle, callback) {
  const shouldSplit = pipeType === 'rage' && Number.isFinite(RAGE_PIPE_OFFSET) && RAGE_PIPE_OFFSET > 0;
  if (!shouldSplit) {
    callback(0, 0);
    return;
  }

  const normalX = -Math.sin(angle);
  const normalY = Math.cos(angle);
  const offsetX = normalX * RAGE_PIPE_OFFSET;
  const offsetY = normalY * RAGE_PIPE_OFFSET;
  callback(-offsetX, -offsetY);
  callback(offsetX, offsetY);
}

function drawPreviewTriangle(cx, cy, baseW, height, angle, color, useBrass = false, pipeType = 'normal') {
  const drawInstance = (centerX, centerY) => {
    const shape = createPipeShape(centerX, centerY, baseW, height, angle, pipeType);

    if (useBrass && shape.type !== 'droplet') {
      const [bp1, bp2, bp3] = computeTrianglePoints(
        centerX,
        centerY,
        baseW + BRASS_TRIANGLE_OUTER_WIDTH_BONUS,
        height + BRASS_TRIANGLE_OUTER_HEIGHT_BONUS,
        angle,
      );
      graphicsEdges.lineStyle(BRASS_OUTER_OUTLINE_THICKNESS, BRASS_PIPE_OUTLINE_COLOR, 0.9);
      graphicsEdges.strokeTriangle(bp1[0], bp1[1], bp2[0], bp2[1], bp3[0], bp3[1]);
    }

    const outlineAlpha = useBrass ? 0.95 : 0.9;
    strokePipeShape(shape, 2, color, outlineAlpha);
  };

  forEachPipeOffset(pipeType, angle, (dx, dy) => {
    drawInstance(cx + dx, cy + dy);
  });
}

  function drawEdge(e, sNode, tNode, edgeId) {
    const from = sNode;  // All edges go from source to target
    const to = tNode;
    const isHovered = (hoveredEdgeId === edgeId);
    
    // Offset start/end by node radius so edges don't overlap nodes visually
    const baseScale = view ? Math.min(view.scaleX, view.scaleY) : 1;
    const fromR = Math.max(1, calculateNodeRadius(from, baseScale)) + 1;
    const toR = Math.max(1, calculateNodeRadius(to, baseScale)) + 1;
    const screenPath = buildEdgeScreenPath(e, from, to, fromR, toR);
    if (!screenPath.length) return;

    const totalLength = screenPath.reduce((sum, seg) => sum + seg.length, 0);
    if (totalLength <= 0) return;

    // All edges are single direction: chain of triangles along the path
    const triH = PIPE_TRIANGLE_HEIGHT;
    const triW = PIPE_TRIANGLE_WIDTH;
    const spacing = Math.max(1, pipeSpacingForType(e?.pipeType || 'normal'));
    const packedCount = Math.max(1, Math.floor(totalLength / spacing));
    const actualSpacing = totalLength / packedCount;
    
    const removal = e.removing || null;
    if (removal && removal.mode === 'explosion') {
      drawRemovalExplosion(e, removal, from);
      return;
    }
    const canLeftClick = !removal && (from && from.owner === myPlayerId) && !e.building;
    const canReverse = removal ? false : canReverseEdge(e);

    let hoverColor = null;
    let hoverAllowed = false;

    if (isHovered && !removal) {
      if (canLeftClick) {
        hoverColor = ownerToColor(myPlayerId);
        hoverAllowed = true;
      } else if (canReverse) {
        hoverColor = ownerToSecondaryColor(myPlayerId);
        hoverAllowed = true;
      }
    }

    const removalHighlight = (!removal &&
      activeAbility === 'bridge1way' &&
      bridgePreviewWillBeBrass &&
      isCrossLikeModeActive() &&
      brassPreviewIntersections.has(edgeId)
    );

    const triangleOverrideColor = hoverColor;
    const triangleHoverFlag = !removal && (hoverAllowed || removalHighlight);
    let trianglesToDraw;
    if (removal) {
      trianglesToDraw = packedCount;
    } else if (e.building) {
      const buildingProgress = Math.max(0, Math.min(1, (e.buildTicksElapsed || 0) / Math.max(1, e.buildTicksRequired || 1)));
      trianglesToDraw = Math.max(1, Math.floor(packedCount * buildingProgress));
    } else {
      trianglesToDraw = packedCount;
    }

    let buildingAnimation = null;
    if (e.building) {
      if (!e._buildingPop) {
        e._buildingPop = { spawnTimes: new Map(), lastCount: 0 };
      }
      buildingAnimation = e._buildingPop;

      if (trianglesToDraw < buildingAnimation.lastCount) {
        for (const key of buildingAnimation.spawnTimes.keys()) {
          if (key >= trianglesToDraw) {
            buildingAnimation.spawnTimes.delete(key);
          }
        }
      } else if (trianglesToDraw > buildingAnimation.lastCount) {
        for (let idx = buildingAnimation.lastCount; idx < trianglesToDraw; idx++) {
          buildingAnimation.spawnTimes.set(idx, animationTime);
        }
      }

      buildingAnimation.lastCount = trianglesToDraw;
    } else if (e._buildingPop) {
      delete e._buildingPop;
    }

    let hiddenTriangles = null;
    if (removal) {
      hiddenTriangles = applyRemovalSteps(removal, packedCount);
      if (removal.complete && removal.hiddenCount >= packedCount) {
        return;
      }
    }

    for (let i = 0; i < trianglesToDraw; i++) {
      if (hiddenTriangles && hiddenTriangles[i]) continue;
      const targetDistance = (i + 0.5) * actualSpacing;
      let remaining = targetDistance;
      let segment = screenPath[0];
      for (let s = 0; s < screenPath.length; s++) {
        const seg = screenPath[s];
        if (remaining <= seg.length || s === screenPath.length - 1) {
          segment = seg;
          break;
        }
        remaining -= seg.length;
      }
      const cx = segment.sx + segment.ux * remaining;
      const cy = segment.sy + segment.uy * remaining;

      let triangleScale = 1;
      if (buildingAnimation) {
        const spawnTime = buildingAnimation.spawnTimes.get(i);
        if (typeof spawnTime === 'number') {
          const elapsed = Math.max(0, animationTime - spawnTime);
          const t = Math.min(1, elapsed / BUILDING_TRIANGLE_SHRINK_DURATION);
          const eased = easeOutCubic(t);
          triangleScale = 1 + (BUILDING_TRIANGLE_INITIAL_SCALE - 1) * (1 - eased);
          if (t >= 1) {
            buildingAnimation.spawnTimes.delete(i);
          }
        }
      }

      drawTriangle(cx, cy, triW, triH, segment.angle, e, from, triangleOverrideColor, triangleHoverFlag, i, packedCount, removalHighlight, triangleScale);
    }
  }

function drawRemovalExplosion(edge, removal, fromNode) {
    if (!removal || removal.mode !== 'explosion') return;
    const particles = removal.particles;
    if (!Array.isArray(particles) || !particles.length) return;

    const now = animationTime;
    const elapsed = now - removal.startTime;
    const baseColor = Number.isFinite(removal.baseColor) ? removal.baseColor : edgeColor(edge, fromNode);
    const greyColor = Number.isFinite(removal.greyColor) ? removal.greyColor : 0xf0f0f0;
    const fadeStartTime = removal.startTime + (Number(removal.driftMaxDuration) || 0) + (Number(removal.restDuration) || 0);
    const fadeEndTime = fadeStartTime + (Number(removal.fadeDuration) || 0);
    const pipeType = edge?.pipeType || 'normal';
    const outlineColor = pipeType === 'gold' ? BRASS_PIPE_OUTLINE_COLOR : 0x000000;
    const driftAlphaStart = Number.isFinite(removal.driftAlphaStart) ? removal.driftAlphaStart : 0.6;
    const driftAlphaEnd = Number.isFinite(removal.driftAlphaEnd) ? removal.driftAlphaEnd : 0.95;
    const restAlpha = Number.isFinite(removal.restAlpha) ? removal.restAlpha : 0.65;
    const driftLighten = Math.max(0, Number(removal.driftLighten) || 0.25);

    for (const particle of particles) {
      if (!particle) continue;
      const driftDuration = Math.max(0.1, Number(particle.driftDuration) || 0.5);
      const greyDelay = Math.max(0, Number(particle.greyDelay) || 0);
      const driftPhase = driftDuration + greyDelay;
      const driftProgress = Math.min(1, Math.max(0, elapsed / Math.max(1e-6, driftDuration)));
      const easedDrift = easeOutCubic(driftProgress);
      const currentX = (Number(particle.startX) || 0) + (Number(particle.offsetX) || 0) * easedDrift;
      const currentY = (Number(particle.startY) || 0) + (Number(particle.offsetY) || 0) * easedDrift;

      const spinAmount = Number(particle.spinAmount) || 0;
      const spinProgress = easeOutCubic(Math.min(1, Math.max(0, elapsed / driftDuration)));
      let angle = Number(particle.startAngle) || 0;
      angle += spinAmount * spinProgress;

      let color = baseColor;
      let alpha = 0.9;

      if (elapsed >= driftPhase) {
        color = greyColor;
        alpha = restAlpha;
      } else {
        // Lighten slightly while drifting to emphasize motion
        color = lerpColor(baseColor, greyColor, Math.min(1, driftProgress * driftLighten));
        alpha = driftAlphaStart + (driftAlphaEnd - driftAlphaStart) * driftProgress;
      }

      if (fadeEndTime > fadeStartTime && now >= fadeStartTime) {
        const fadeProgress = Math.min(1, (now - fadeStartTime) / (fadeEndTime - fadeStartTime));
        alpha *= (1 - fadeProgress);
      }

      if (alpha <= 0.01) continue;

      const [screenX, screenY] = worldToScreen(currentX, currentY);
      const triWidth = Number(particle.triWidth) || PIPE_TRIANGLE_WIDTH;
      const triHeight = Number(particle.triHeight) || PIPE_TRIANGLE_HEIGHT;
      drawExplosionTriangle(screenX, screenY, triWidth, triHeight, angle, color, alpha, outlineColor, pipeType);
    }

    if (fadeEndTime > fadeStartTime && now >= fadeEndTime) {
      removal.complete = true;
    }
  }

function drawTriangle(cx, cy, baseW, height, angle, e, fromNode, overrideColor, isHovered, triangleIndex, totalTriangles, removalOutline = false, scaleOverride = 1) {
    const pipeType = e?.pipeType || 'normal';
    const isBrass = pipeType === 'gold';
    const inactiveColor = isBrass ? BRASS_PIPE_DIM_COLOR : 0x999999;
    const isRemovalOutline = !!removalOutline;
    const color = (overrideColor != null) ? overrideColor : edgeColor(e, fromNode);

    const drawInstance = (centerX, centerY) => {
      let finalAngle = angle;
      if (e._spin) {
        const elapsed = Math.max(0, animationTime - e._spin.spinStartTime);
        const perIndexDelay = EDGE_SPIN_PER_TRIANGLE_SEC;
        const local = Math.max(0, elapsed - (triangleIndex || 0) * perIndexDelay);
        const spinPhase = Math.min(1, local / 0.24); // 180deg over ~0.24s (slower)
        // Start from previous orientation (new angle + PI) and settle on new angle
        finalAngle = angle + Math.PI * (1 - spinPhase);
      }

      const scaledBaseW = baseW * Math.max(scaleOverride, 0.01);
      const scaledHeight = height * Math.max(scaleOverride, 0.01);
      const shape = createPipeShape(centerX, centerY, scaledBaseW, scaledHeight, finalAngle, pipeType);
      const isDroplet = shape.type === 'droplet';
      const trianglePoints = !isDroplet ? shape.points : null;
      const brassPoints = (!isDroplet && isBrass && !isRemovalOutline)
        ? computeTrianglePoints(
            centerX,
            centerY,
            scaledBaseW + BRASS_TRIANGLE_OUTER_WIDTH_BONUS,
            scaledHeight + BRASS_TRIANGLE_OUTER_HEIGHT_BONUS,
            finalAngle,
          )
        : null;
      let brassFilled = false;
      const fillBrass = () => {
        if (!brassPoints || brassFilled) return;
        graphicsEdges.fillStyle(BRASS_PIPE_COLOR, 1);
        graphicsEdges.fillTriangle(
          brassPoints[0][0], brassPoints[0][1],
          brassPoints[1][0], brassPoints[1][1],
          brassPoints[2][0], brassPoints[2][1],
        );
        brassFilled = true;
      };

      if (e.flowing) {
        // Animated juice flow effect - filled triangles
        const animatedColor = getAnimatedJuiceColor(color, triangleIndex || 0, totalTriangles || 1, e.flowStartTime);

        if (animatedColor === null) {
          // Triangle not yet filled - show outline (same as non-flowing)
          if (!isRemovalOutline) {
            strokePipeShape(shape, 2, inactiveColor, 1);
          }
        } else {
          // Triangle is filled - overlay animated color core atop brass shell
          if (!isRemovalOutline) {
            fillBrass();
          }
          fillPipeShape(shape, animatedColor.color, animatedColor.alpha);
        }
      } else if (e.on && ENABLE_IDLE_EDGE_ANIMATION) {
        // Edge is on but not flowing - show hollow triangles with same animation pattern
        const animatedColor = getAnimatedJuiceColor(color, triangleIndex || 0, totalTriangles || 1, e.flowStartTime);
        
        if (animatedColor === null) {
          // Triangle not yet reached in animation - show outline
          if (!isRemovalOutline) {
            strokePipeShape(shape, 2, inactiveColor, 1);
          }
        } else {
          // Triangle is in animation cycle - show hollow triangle with animated color outline
          strokePipeShape(shape, 3, animatedColor.color, animatedColor.alpha);
        }
      } else {
        // Edge is not on - show outline
        if (!isRemovalOutline) {
          strokePipeShape(shape, 2, inactiveColor, 1);
        }
      }

      if (isRemovalOutline) {
        strokePipeShape(shape, 3, 0x000000, 1);
        return;
      }

      if (brassPoints) {
        graphicsEdges.lineStyle(BRASS_OUTER_OUTLINE_THICKNESS, BRASS_PIPE_OUTLINE_COLOR, 0.95);
        graphicsEdges.strokeTriangle(
          brassPoints[0][0], brassPoints[0][1],
          brassPoints[1][0], brassPoints[1][1],
          brassPoints[2][0], brassPoints[2][1],
        );
      }

      // Add hover border using the same color
      if (isHovered) {
        strokePipeShape(shape, 2, color, 1);
      }
    };

    forEachPipeOffset(pipeType, angle, (dx, dy) => {
      drawInstance(cx + dx, cy + dy);
    });
  }

  function drawExplosionTriangle(cx, cy, baseW, height, angle, color, alpha, outlineColor, pipeType) {
    const [p1, p2, p3] = computeTrianglePoints(cx, cy, baseW, height, angle);
    const isBrass = pipeType === 'gold';

    if (isBrass) {
      const [bp1, bp2, bp3] = computeTrianglePoints(
        cx,
        cy,
        baseW + BRASS_TRIANGLE_OUTER_WIDTH_BONUS,
        height + BRASS_TRIANGLE_OUTER_HEIGHT_BONUS,
        angle,
      );
      graphicsEdges.fillStyle(BRASS_PIPE_COLOR, Math.max(0, Math.min(1, alpha)));
      graphicsEdges.fillTriangle(bp1[0], bp1[1], bp2[0], bp2[1], bp3[0], bp3[1]);
      graphicsEdges.lineStyle(BRASS_OUTER_OUTLINE_THICKNESS, BRASS_PIPE_OUTLINE_COLOR, Math.max(0, Math.min(1, alpha * 0.95)));
      graphicsEdges.strokeTriangle(bp1[0], bp1[1], bp2[0], bp2[1], bp3[0], bp3[1]);
    }

    graphicsEdges.fillStyle(color, Math.max(0, Math.min(1, alpha)));
    graphicsEdges.fillTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
    const outlineAlpha = Math.max(0, Math.min(1, alpha * 0.9));
    graphicsEdges.lineStyle(2, outlineColor, outlineAlpha);
    graphicsEdges.strokeTriangle(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
  }

  function easeOutCubic(t) {
    const clamped = Math.max(0, Math.min(1, t));
    const inv = 1 - clamped;
    return 1 - inv * inv * inv;
  }

  function lerpColor(colorA, colorB, t) {
    const clamped = Math.max(0, Math.min(1, t));
    const aR = (colorA >> 16) & 0xFF;
    const aG = (colorA >> 8) & 0xFF;
    const aB = colorA & 0xFF;
    const bR = (colorB >> 16) & 0xFF;
    const bG = (colorB >> 8) & 0xFF;
    const bB = colorB & 0xFF;
    const r = Math.round(aR + (bR - aR) * clamped);
    const g = Math.round(aG + (bG - aG) * clamped);
    const b = Math.round(aB + (bB - aB) * clamped);
    return (r << 16) | (g << 8) | b;
  }

  function lerpPoint(a, b, t) {
    return [
      a[0] + (b[0] - a[0]) * t,
      a[1] + (b[1] - a[1]) * t,
    ];
  }

  function edgeColor(e, fromNodeOrNull) {
    // If edge is on (flowing or not), use source node owner color; else grey
    if (!e?.on) return 0x999999;
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
