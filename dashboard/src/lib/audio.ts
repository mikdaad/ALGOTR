/**
 * ─── Audio Alert Utility ───
 * Browser-based audio notifications using the Web Audio API.
 * Generates crisp synthesized tones — no external audio files needed.
 *
 *   BUY signal  → High-pitched bright bell (880Hz → 1320Hz sweep)
 *   SELL signal → Low-pitched alert tone (330Hz → 220Hz sweep)
 */

let audioCtx: AudioContext | null = null;

function getAudioContext(): AudioContext {
  if (!audioCtx || audioCtx.state === "closed") {
    audioCtx = new AudioContext();
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume();
  }
  return audioCtx;
}

export function playBuyAlert(): void {
  try {
    const ctx = getAudioContext();
    const now = ctx.currentTime;

    // Bright ascending bell — two oscillators for richness
    const osc1 = ctx.createOscillator();
    const osc2 = ctx.createOscillator();
    const gain = ctx.createGain();

    osc1.type = "sine";
    osc1.frequency.setValueAtTime(880, now);
    osc1.frequency.exponentialRampToValueAtTime(1320, now + 0.15);

    osc2.type = "triangle";
    osc2.frequency.setValueAtTime(1760, now);
    osc2.frequency.exponentialRampToValueAtTime(2640, now + 0.15);

    gain.gain.setValueAtTime(0.3, now);
    gain.gain.exponentialRampToValueAtTime(0.01, now + 0.4);

    osc1.connect(gain);
    osc2.connect(gain);
    gain.connect(ctx.destination);

    osc1.start(now);
    osc2.start(now);
    osc1.stop(now + 0.4);
    osc2.stop(now + 0.4);

    // Second bell hit for "ding-ding" effect
    const osc3 = ctx.createOscillator();
    const gain2 = ctx.createGain();
    osc3.type = "sine";
    osc3.frequency.setValueAtTime(1320, now + 0.2);
    osc3.frequency.exponentialRampToValueAtTime(1760, now + 0.35);
    gain2.gain.setValueAtTime(0.2, now + 0.2);
    gain2.gain.exponentialRampToValueAtTime(0.01, now + 0.6);
    osc3.connect(gain2);
    gain2.connect(ctx.destination);
    osc3.start(now + 0.2);
    osc3.stop(now + 0.6);
  } catch {
    // Audio not available — fail silently
  }
}

export function playSellAlert(): void {
  try {
    const ctx = getAudioContext();
    const now = ctx.currentTime;

    // Low descending warning tone
    const osc1 = ctx.createOscillator();
    const osc2 = ctx.createOscillator();
    const gain = ctx.createGain();

    osc1.type = "sawtooth";
    osc1.frequency.setValueAtTime(440, now);
    osc1.frequency.exponentialRampToValueAtTime(220, now + 0.3);

    osc2.type = "sine";
    osc2.frequency.setValueAtTime(330, now);
    osc2.frequency.exponentialRampToValueAtTime(165, now + 0.3);

    gain.gain.setValueAtTime(0.2, now);
    gain.gain.exponentialRampToValueAtTime(0.01, now + 0.5);

    osc1.connect(gain);
    osc2.connect(gain);
    gain.connect(ctx.destination);

    osc1.start(now);
    osc2.start(now);
    osc1.stop(now + 0.5);
    osc2.stop(now + 0.5);

    // Second hit
    const osc3 = ctx.createOscillator();
    const gain2 = ctx.createGain();
    osc3.type = "square";
    osc3.frequency.setValueAtTime(220, now + 0.25);
    osc3.frequency.exponentialRampToValueAtTime(110, now + 0.45);
    gain2.gain.setValueAtTime(0.12, now + 0.25);
    gain2.gain.exponentialRampToValueAtTime(0.01, now + 0.6);
    osc3.connect(gain2);
    gain2.connect(ctx.destination);
    osc3.start(now + 0.25);
    osc3.stop(now + 0.6);
  } catch {
    // Audio not available — fail silently
  }
}

export function playSignalAlert(direction: "BUY" | "SELL"): void {
  if (direction === "BUY") {
    playBuyAlert();
  } else {
    playSellAlert();
  }
}
