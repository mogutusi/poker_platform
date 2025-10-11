'use client'

import { Card } from '@/types/poker'
import { getCardDisplay, getCardColor } from '@/utils/poker'

interface PokerCardProps {
  card: Card
  isHidden?: boolean
  className?: string
}

export default function PokerCard({ card, isHidden = false, className = '' }: PokerCardProps) {
  if (isHidden) {
    return (
      <div className={`w-16 h-24 bg-gradient-to-br from-blue-600 to-blue-800 border-2 border-blue-400 rounded-lg flex items-center justify-center shadow-md ${className}`}>
        <div className="w-8 h-8 bg-white rounded-full flex items-center justify-center">
          <span className="text-blue-600 font-bold text-xs">?</span>
        </div>
      </div>
    )
  }

  return (
    <div className={`w-16 h-24 bg-white border-2 border-gray-300 rounded-lg flex flex-col items-center justify-center shadow-md hover:shadow-lg transition-shadow ${className}`}>
      <div className={`text-lg font-bold ${getCardColor(card)}`}>
        {getCardDisplay(card)}
      </div>
    </div>
  )
}