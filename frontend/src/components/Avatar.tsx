// Generated profile picture: a little cartoon face drawn from the nickname, so every player
// (you, other people, AI players) gets the same recognisable picture everywhere: table seat,
// lobby preview, profile card. No upload / storage: the backend has no avatar field, and the
// picture is a pure function of the name.

const BACKGROUNDS = ["#7c2d12", "#14532d", "#1e3a8a", "#581c87", "#7f1d1d", "#134e4a", "#713f12", "#3f3f46"]
const SKINS = ["#f6d3b3", "#e9b98f", "#c98d63", "#a8693f", "#7a4a2a", "#f1c7a0"]
const HAIRS = ["#1f1309", "#4a2c12", "#8a5a2b", "#d4a24c", "#c0392b", "#2c2c2c", "#e6e6e6"]
const ACCENTS = ["#d4af37", "#e5484d", "#30a46c", "#3e63dd", "#f76b15", "#8e4ec6"]

function hash(name: string): number {
  let h = 2166136261
  for (const ch of name) {
    h ^= ch.charCodeAt(0)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

export default function Avatar({ name, size = 40, className }: { name: string; size?: number; className?: string }) {
  const h = hash(name || "?")
  const pick = <T,>(list: T[], shift: number) => list[(h >>> shift) % list.length]
  const bg = pick(BACKGROUNDS, 0)
  const skin = pick(SKINS, 3)
  const hair = pick(HAIRS, 6)
  const accent = pick(ACCENTS, 9)
  const hairStyle = (h >>> 12) % 4 // 0 short, 1 long, 2 cap, 3 bald
  const mouthStyle = (h >>> 15) % 3 // 0 smile, 1 grin, 2 flat
  const glasses = (h >>> 18) % 5 === 0
  const eyeY = 47 + ((h >>> 20) % 3)

  return (
    <svg
      viewBox="0 0 100 100"
      width={size}
      height={size}
      role="img"
      aria-label={`${name} 的头像`}
      className={className}
    >
      <defs>
        <clipPath id={`av-${h}`}>
          <circle cx="50" cy="50" r="50" />
        </clipPath>
      </defs>
      <g clipPath={`url(#av-${h})`}>
        <rect width="100" height="100" fill={bg} />
        {/* shoulders / shirt */}
        <path d="M14 100 C18 78, 82 78, 86 100 Z" fill={accent} />
        {/* long hair sits behind the head */}
        {hairStyle === 1 && <path d="M24 46 C22 20, 78 20, 76 46 L80 82 L20 82 Z" fill={hair} />}
        {/* neck + head */}
        <rect x="42" y="66" width="16" height="14" rx="4" fill={skin} />
        <ellipse cx="50" cy="48" rx="24" ry="26" fill={skin} />
        {/* hair on top */}
        {hairStyle === 0 && <path d="M26 44 C24 22, 76 22, 74 44 C66 32, 36 32, 26 44 Z" fill={hair} />}
        {hairStyle === 1 && <path d="M26 42 C26 24, 74 24, 74 42 C64 30, 38 30, 26 42 Z" fill={hair} />}
        {hairStyle === 2 && (
          <>
            <path d="M25 40 C25 20, 75 20, 75 40 Z" fill={accent} />
            <rect x="22" y="37" width="56" height="6" rx="3" fill={accent} />
          </>
        )}
        {/* eyes */}
        <circle cx="41" cy={eyeY} r="3.2" fill="#1b1b1b" />
        <circle cx="59" cy={eyeY} r="3.2" fill="#1b1b1b" />
        {glasses && (
          <g fill="none" stroke="#1b1b1b" strokeWidth="2">
            <circle cx="41" cy={eyeY} r="7" />
            <circle cx="59" cy={eyeY} r="7" />
            <path d={`M48 ${eyeY} L52 ${eyeY}`} />
          </g>
        )}
        {/* mouth */}
        {mouthStyle === 0 && <path d="M41 59 Q50 67 59 59" fill="none" stroke="#5a2a1a" strokeWidth="2.5" strokeLinecap="round" />}
        {mouthStyle === 1 && <path d="M40 58 Q50 70 60 58 Z" fill="#5a2a1a" />}
        {mouthStyle === 2 && <path d="M43 61 L57 61" stroke="#5a2a1a" strokeWidth="2.5" strokeLinecap="round" />}
      </g>
    </svg>
  )
}
