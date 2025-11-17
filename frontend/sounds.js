(() => {
  const SOUND_STORAGE_KEY = 'soundEnabled';
  let soundEnabled = true; // persisted in localStorage
  let audioCtx = null;
  let globalGain = null;

  function loadPersistentSound() {
    const saved = localStorage.getItem(SOUND_STORAGE_KEY);
    soundEnabled = saved !== 'false'; // default true
    return soundEnabled;
  }

  function savePersistentSound(value) {
    soundEnabled = !!value;
    localStorage.setItem(SOUND_STORAGE_KEY, soundEnabled.toString());
    if (globalGain) globalGain.gain.value = soundEnabled ? 1.0 : 0.0;
    return soundEnabled;
  }

  function ensureAudio() {
    if (!audioCtx) {
      try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        globalGain = audioCtx.createGain();
        globalGain.gain.value = soundEnabled ? 1.0 : 0.0;
        globalGain.connect(audioCtx.destination);
      } catch (e) {
        console.warn('Audio init failed', e);
      }
    }
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume().catch(() => {});
    }
  }

  function playToneSequence(steps) {
    if (!soundEnabled) return;
    ensureAudio();
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    steps.forEach((step) => {
      const t0 = now + (step.delay || 0);
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = step.type || 'sine';
      osc.frequency.setValueAtTime(step.freq, t0);
      const attack = step.attack ?? 0.005;
      const decay = step.decay ?? 0.15;
      const vol = step.volume ?? 0.2;
      gain.gain.setValueAtTime(0.0001, t0);
      gain.gain.exponentialRampToValueAtTime(Math.max(0.0002, vol), t0 + attack);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + attack + decay);
      osc.connect(gain);
      gain.connect(globalGain);
      osc.start(t0);
      osc.stop(t0 + attack + decay + 0.05);
    });
  }

  function createWhiteNoiseBuffer(durationSec) {
    if (!audioCtx) return null;
    const sampleRate = audioCtx.sampleRate || 44100;
    const frameCount = Math.max(1, Math.floor(durationSec * sampleRate));
    const buffer = audioCtx.createBuffer(1, frameCount, sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < frameCount; i++) {
      data[i] = (Math.random() * 2 - 1);
    }
    return buffer;
  }

  function playNoiseBurst({ duration = 0.15, volume = 0.15, filterType = 'lowpass', filterFreq = 600, q = 0.7, attack = 0.004, decay = 0.10 }) {
    if (!soundEnabled) return;
    ensureAudio();
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    const src = audioCtx.createBufferSource();
    const noise = createWhiteNoiseBuffer(duration);
    if (!noise) return;
    src.buffer = noise;
    const filter = audioCtx.createBiquadFilter();
    filter.type = filterType;
    filter.frequency.value = filterFreq;
    filter.Q.value = q;
    const gain = audioCtx.createGain();
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(Math.max(0.0002, volume), now + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + attack + decay);
    src.connect(filter);
    filter.connect(gain);
    gain.connect(globalGain);
    src.start(now);
    src.stop(now + duration);
  }

  function playCaptureDing() {
    playToneSequence([
      { freq: 880, type: 'sine', attack: 0.005, decay: 0.12, volume: 0.15, delay: 0.00 },
      { freq: 1320, type: 'sine', attack: 0.005, decay: 0.12, volume: 0.12, delay: 0.05 },
    ]);
  }

  function playEnemyCaptureDing() {
    playToneSequence([
      { freq: 780, type: 'sine', attack: 0.005, decay: 0.12, volume: 0.14, delay: 0.00 },
      { freq: 1170, type: 'sine', attack: 0.005, decay: 0.12, volume: 0.11, delay: 0.05 },
    ]);
  }

  function playChaChing() {
    if (!soundEnabled) return;
    ensureAudio();
    if (!audioCtx) return;
    const now = audioCtx.currentTime;

    const strikeOsc = audioCtx.createOscillator();
    const strikeGain = audioCtx.createGain();
    strikeOsc.type = 'square';
    strikeOsc.frequency.setValueAtTime(2000, now);
    strikeOsc.frequency.exponentialRampToValueAtTime(900, now + 0.22);
    strikeGain.gain.setValueAtTime(0.0001, now);
    strikeGain.gain.exponentialRampToValueAtTime(0.28, now + 0.012);
    strikeGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.28);
    strikeOsc.connect(strikeGain);
    strikeGain.connect(globalGain);
    strikeOsc.start(now);
    strikeOsc.stop(now + 0.3);

    const sparkleOsc = audioCtx.createOscillator();
    const sparkleGain = audioCtx.createGain();
    sparkleOsc.type = 'triangle';
    sparkleOsc.frequency.setValueAtTime(3200, now + 0.03);
    sparkleOsc.frequency.exponentialRampToValueAtTime(1800, now + 0.22);
    sparkleGain.gain.setValueAtTime(0.0001, now + 0.03);
    sparkleGain.gain.exponentialRampToValueAtTime(0.16, now + 0.05);
    sparkleGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.26);
    sparkleOsc.connect(sparkleGain);
    sparkleGain.connect(globalGain);
    sparkleOsc.start(now + 0.03);
    sparkleOsc.stop(now + 0.28);

    playNoiseBurst({
      duration: 0.12,
      volume: 0.12,
      filterType: 'highpass',
      filterFreq: 2500,
      q: 1.5,
      attack: 0.002,
      decay: 0.12,
    });
  }

  function playLoseNodeWarning() {
    // Intentionally silent placeholder for future warning cue
  }

  function playBridgeHammerHit(hitIndex = 0) {
    if (!soundEnabled) return;
    ensureAudio();
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    const osc = audioCtx.createOscillator();
    const og = audioCtx.createGain();
    osc.type = 'sine';
    const startFreq = 220 - hitIndex * 10;
    const endFreq = 95 - hitIndex * 3;
    osc.frequency.setValueAtTime(Math.max(80, startFreq), now);
    osc.frequency.exponentialRampToValueAtTime(Math.max(60, endFreq), now + 0.10);
    og.gain.setValueAtTime(0.0001, now);
    og.gain.exponentialRampToValueAtTime(0.22, now + 0.008);
    og.gain.exponentialRampToValueAtTime(0.0001, now + 0.16);
    osc.connect(og);
    og.connect(globalGain);
    osc.start(now);
    osc.stop(now + 0.18);
    playNoiseBurst({ duration: 0.12, volume: 0.20, filterType: 'bandpass', filterFreq: 850 + hitIndex * 110, q: 3.5, attack: 0.002, decay: 0.11 });
  }

  function playBridgeExplosion() {
    if (!soundEnabled) return;
    ensureAudio();
    if (!audioCtx) return;
    const now = audioCtx.currentTime;

    const boomOsc = audioCtx.createOscillator();
    const boomGain = audioCtx.createGain();
    boomOsc.type = 'sawtooth';
    boomOsc.frequency.setValueAtTime(110, now);
    boomOsc.frequency.exponentialRampToValueAtTime(36, now + 0.5);
    boomGain.gain.setValueAtTime(0.0001, now);
    boomGain.gain.exponentialRampToValueAtTime(0.38, now + 0.03);
    boomGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.6);
    boomOsc.connect(boomGain);
    boomGain.connect(globalGain);
    boomOsc.start(now);
    boomOsc.stop(now + 0.65);

    playNoiseBurst({
      duration: 0.55,
      volume: 0.32,
      filterType: 'bandpass',
      filterFreq: 720,
      q: 0.9,
      attack: 0.005,
      decay: 0.5,
    });

    playNoiseBurst({
      duration: 0.35,
      volume: 0.18,
      filterType: 'highpass',
      filterFreq: 2400,
      q: 1.8,
      attack: 0.004,
      decay: 0.25,
    });
  }

  function playCrownAttackHorn() {
    if (!soundEnabled) return;
    ensureAudio();
    if (!audioCtx) return;
    const now = audioCtx.currentTime;

    const hornOsc = audioCtx.createOscillator();
    const hornGain = audioCtx.createGain();
    hornOsc.type = 'sawtooth';
    hornOsc.frequency.setValueAtTime(220, now);
    hornOsc.frequency.exponentialRampToValueAtTime(90, now + 0.45);
    hornGain.gain.setValueAtTime(0.0001, now);
    hornGain.gain.exponentialRampToValueAtTime(0.38, now + 0.06);
    hornGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.9);
    hornOsc.connect(hornGain);
    hornGain.connect(globalGain);
    hornOsc.start(now);
    hornOsc.stop(now + 0.95);

    playNoiseBurst({
      duration: 0.5,
      volume: 0.22,
      filterType: 'lowpass',
      filterFreq: 520,
      q: 0.9,
      attack: 0.01,
      decay: 0.5,
    });
  }

  function playReverseShuffle() {
    if (!soundEnabled) return;
    ensureAudio();
    if (!audioCtx) return;
    const grains = 8;
    for (let i = 0; i < grains; i++) {
      const delay = i * 0.018 + (Math.random() * 0.006);
      const center = 900 + Math.random() * 1800;
      const q = 0.8 + Math.random() * 1.4;
      const vol = 0.045 + Math.random() * 0.03;
      const attack = 0.004 + Math.random() * 0.006;
      const decay = 0.045 + Math.random() * 0.030;
      setTimeout(() => {
        if (!audioCtx) return;
        const now = audioCtx.currentTime;
        const src = audioCtx.createBufferSource();
        const buf = createWhiteNoiseBuffer(0.08);
        if (!buf) return;
        src.buffer = buf;
        const filter = audioCtx.createBiquadFilter();
        filter.type = 'bandpass';
        filter.frequency.value = center;
        filter.Q.value = q;
        const gain = audioCtx.createGain();
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(Math.max(0.0002, vol), now + attack);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + attack + decay);
        src.connect(filter);
        filter.connect(gain);
        gain.connect(globalGain);
        src.start(now);
        src.stop(now + 0.09);
      }, Math.floor(delay * 1000));
    }
  }

  window.DurbSounds = {
    loadPersistentSound,
    savePersistentSound,
    ensureAudio,
    playCaptureDing,
    playEnemyCaptureDing,
    playChaChing,
    playCrownAttackHorn,
    playLoseNodeWarning,
    playBridgeHammerHit,
    playBridgeExplosion,
    playReverseShuffle,
    isSoundEnabled: () => soundEnabled,
  };
})();
