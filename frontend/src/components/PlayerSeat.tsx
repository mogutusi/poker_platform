'use client'

import { Player } from '@/types/poker'
import PokerCard from './PokerCard'
import Chip from './Chip'

interface PlayerSeatProps {
  player: Player
  isCurrentPlayer?: boolean
  className?: string
}

export default function PlayerSeat({ player, isCurrentPlayer = false, className = '' }: PlayerSeatProps) {
  return (
    <div className={`
      card p-4 min-w-48
      ${isCurrentPlayer ? 'ring-2 ring-poker-gold' : ''}
      ${className}
    `}>
      <div className="flex justify-between items-start mb-3">
        <div>
          <h3 className="font-semibold text-lg">{player.name}</h3>
          <p className="text-sm text-gray-600">
            {player.isDealer ? '庄家' : `位置 ${player.position}`}
          </p>
        </div>
        <Chip amount={player.chips} size="small" />
      </div>

      {player.currentBet > 0 && (
        <div className="mb-3">
          <div className="flex items-center space-x-2">
            <span className="text-sm text-gray-600">下注:</span>
            <Chip amount={player.currentBet} size="small" />
          </div>
        </div>
      )}

      <div className="flex space-x-1">
        {player.cards.map((card, index) => (
          <PokerCard 
            key={index} 
            card={card} 
            isHidden={!player.isActive}
          />
        ))}
      </div>

      {!player.isActive && (
        <div className="mt-2 text-center">
          <span className="text-sm text-red-500 font-medium">已弃牌</span>
        </div>
      )}
    </div>
  )
}