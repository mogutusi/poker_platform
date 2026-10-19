// Table sound effects, synthesized with Web Audio (no audio files to ship or license).
//
// Browsers only let a page make sound after the user has interacted with it, so the context is
// created lazily and resumed on the first click/key. Everything here fails silently: no sound is
// never an error. The on/off switch is remembered per browser ("poker.sound").

let ctx: AudioContext | null = null
let noise: AudioBuffer | null = null
let enabled = true

try {
  enabled = typeof localStorage === "undefined" || localStorage.getItem("poker.sound") !== "0"
} catch {
  /* storage blocked: default on */
}

function audio(): AudioContext | null {
  if (typeof window === "undefined") return null
  try {
    if (!ctx) {
      const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!AC) return null
      ctx = new AC()
      const len = Math.floor(ctx.sampleRate * 0.5)
      noise = ctx.createBuffer(1, len, ctx.sampleRate)
      const d = noise.getChannelData(0)
      for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1
    }
    if (ctx.state === "suspended") void ctx.resume().catch(() => {})
    return ctx
  } catch {
    return null
  }
}

if (typeof window !== "undefined") {
  const unlock = () => {
    audio()
    window.removeEventListener("pointerdown", unlock)
    window.removeEventListener("keydown", unlock)
  }
  window.addEventListener("pointerdown", unlock)
  window.addEventListener("keydown", unlock)
}

export function soundEnabled(): boolean {
  return enabled
}

export function setSoundEnabled(on: boolean): void {
  enabled = on
  try {
    localStorage.setItem("poker.sound", on ? "1" : "0")
  } catch {
    /* storage blocked: lasts for this page only */
  }
}

/** A short filtered noise burst: the building block for chip clicks and card swishes. */
function burst(at: number, freq: number, q: number, dur: number, gain: number, sweepTo?: number) {
  const ac = ctx!
  const src = ac.createBufferSource()
  src.buffer = noise
  src.playbackRate.value = 0.8 + Math.random() * 0.4
  const bp = ac.createBiquadFilter()
  bp.type = "bandpass"
  bp.frequency.setValueAtTime(freq, at)
  if (sweepTo) bp.frequency.exponentialRampToValueAtTime(sweepTo, at + dur)
  bp.Q.value = q
  const g = ac.createGain()
  g.gain.setValueAtTime(0.0001, at)
  g.gain.exponentialRampToValueAtTime(gain, at + Math.min(0.006, dur / 4))
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur)
  src.connect(bp).connect(g).connect(ac.destination)
  src.start(at, Math.random() * 0.3, dur + 0.02)
}

/** A short pitched tone (the knock of a check, the "your turn" chime). */
function tone(at: number, freq: number, dur: number, gain: number, type: OscillatorType = "sine") {
  const ac = ctx!
  const o = ac.createOscillator()
  o.type = type
  o.frequency.setValueAtTime(freq, at)
  const g = ac.createGain()
  g.gain.setValueAtTime(0.0001, at)
  g.gain.exponentialRampToValueAtTime(gain, at + 0.008)
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur)
  o.connect(g).connect(ac.destination)
  o.start(at)
  o.stop(at + dur + 0.02)
}

function ready(): AudioContext | null {
  if (!enabled) return null
  const ac = audio()
  return ac && ac.state === "running" ? ac : null
}

/** Chips hitting the felt / each other. More chips for bigger amounts (relative to the big blind). */
export function playChips(amount = 1, bigBlind = 2) {
  const ac = ready()
  if (!ac) return
  const bbs = amount / Math.max(1, bigBlind)
  const clicks = Math.max(2, Math.min(9, Math.round(2 + Math.log2(1 + bbs) * 1.5)))
  const t0 = ac.currentTime + 0.01
  for (let i = 0; i < clicks; i++) {
    const at = t0 + i * (0.035 + Math.random() * 0.03)
    burst(at, 3200 + Math.random() * 2200, 6, 0.045, 0.35)
    tone(at, 2400 + Math.random() * 900, 0.03, 0.03, "triangle")
  }
}

/** A pile of chips being pushed (to the pot, to the winner). */
export function playChipSweep(amount = 1, bigBlind = 2) {
  const ac = ready()
  if (!ac) return
  const t0 = ac.currentTime + 0.01
  burst(t0, 1800, 0.8, 0.22, 0.12, 900)
  const n = Math.max(4, Math.min(12, Math.round(4 + Math.log2(1 + amount / Math.max(1, bigBlind)))))
  for (let i = 0; i < n; i++) burst(t0 + 0.05 + i * 0.028 + Math.random() * 0.02, 3000 + Math.random() * 2500, 5, 0.04, 0.22)
}

/** A card sliding off the deck onto the felt. `count` cards in quick succession (the flop = 3). */
export function playDeal(count = 1) {
  const ac = ready()
  if (!ac) return
  const t0 = ac.currentTime + 0.01
  for (let i = 0; i < count; i++) {
    const at = t0 + i * 0.12
    burst(at, 2600, 1.2, 0.11, 0.3, 1200)
    burst(at + 0.09, 600, 1.5, 0.04, 0.18) // the card landing
  }
}

/** Two knuckle taps on the table. */
export function playCheck() {
  const ac = ready()
  if (!ac) return
  const t0 = ac.currentTime + 0.01
  for (const dt of [0, 0.11]) {
    tone(t0 + dt, 140, 0.09, 0.35)
    burst(t0 + dt, 900, 2, 0.04, 0.2)
  }
}

/** Cards tossed into the muck. */
export function playFold() {
  const ac = ready()
  if (!ac) return
  burst(ac.currentTime + 0.01, 1500, 0.9, 0.2, 0.18, 500)
}

/** Gentle two-note chime: it's your turn. */
export function playYourTurn() {
  const ac = ready()
  if (!ac) return
  const t0 = ac.currentTime + 0.01
  tone(t0, 880, 0.25, 0.12)
  tone(t0 + 0.12, 1320, 0.35, 0.1)
}
