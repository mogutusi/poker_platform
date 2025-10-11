'use client'

import { formatChips } from '@/utils/poker'

interface ChipProps {
  amount: number
  size?: 'small' | 'medium' | 'large'
  className?: string
}

const sizeClasses = {
  small: 'w-8 h-8 text-xs',
  medium: 'w-12 h-12 text-sm',
  large: 'w-16 h-16 text-base'
}

const getChipColor = (amount: number): string => {
  if (amount >= 1000) return 'bg-poker-gold text-poker-black'
  if (amount >= 500) return 'bg-red-600 text-white'
  if (amount >= 100) return 'bg-blue-600 text-white'
  if (amount >= 50) return 'bg-green-600 text-white'
  if (amount >= 25) return 'bg-yellow-600 text-white'
  return 'bg-gray-600 text-white'
}

export default function Chip({ amount, size = 'medium', className = '' }: ChipProps) {
  return (
    <div className={`
      ${sizeClasses[size]} 
      ${getChipColor(amount)}
      rounded-full border-2 border-white flex items-center justify-center font-bold shadow-lg hover:shadow-xl transition-shadow cursor-pointer
      ${className}
    `}>
      {formatChips(amount)}
    </div>
  )
}