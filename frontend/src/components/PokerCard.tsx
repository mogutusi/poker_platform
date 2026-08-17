'use client'

import { cn } from '@/lib/utils'
import { Card } from '@/types/poker'

const sizeClasses = {
  sm: 'h-14 w-10',
  md: 'h-20 w-14',
  lg: 'h-24 w-16',
}

const SUIT_LETTER: Record<Card['suit'], string> = {
  hearts: 'h',
  diamonds: 'd',
  spades: 's',
  clubs: 'c',
}

/** Filename for one card: rank + suit letter, e.g. Ah, 10s, Kd. Back = back.png */
function getCardFilename(card: Card): string {
  return `${card.rank}${SUIT_LETTER[card.suit]}.png`
}

const CARDS_BASE = '/cards'

interface PokerCardProps {
  card: Card
  isHidden?: boolean
  size?: 'sm' | 'md' | 'lg'
  cornerLabel?: boolean
  className?: string
}

export default function PokerCard({ card, isHidden = false, size = 'md', className }: PokerCardProps) {
  const sizeClass = sizeClasses[size]
  const src = isHidden ? `${CARDS_BASE}/back.png` : `${CARDS_BASE}/${getCardFilename(card)}`

  return (
    <div
      className={cn(
        'overflow-hidden rounded-[10px] border border-white/90 bg-white shadow-lg transition-all duration-200 hover:shadow-xl hover:scale-[1.02]',
        sizeClass,
        className
      )}
    >
      <img
        src={src}
        alt={isHidden ? 'Card back' : `${card.rank} of ${card.suit}`}
        className="h-full w-full object-cover object-center"
      />
    </div>
  )
}
