import { Card } from '@/types/poker'

// 扑克牌工具函数
export const createDeck = (): Card[] => {
  const suits: Card['suit'][] = ['hearts', 'diamonds', 'clubs', 'spades']
  const ranks: Card['rank'][] = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
  
  const deck: Card[] = []
  
  suits.forEach(suit => {
    ranks.forEach((rank, index) => {
      deck.push({
        suit,
        rank,
        value: rank === 'A' ? 14 : rank === 'K' ? 13 : rank === 'Q' ? 12 : rank === 'J' ? 11 : index + 1
      })
    })
  })
  
  return shuffleDeck(deck)
}

export const shuffleDeck = (deck: Card[]): Card[] => {
  const shuffled = [...deck]
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]]
  }
  return shuffled
}

export const getCardDisplay = (card: Card): string => {
  const suitSymbols = {
    hearts: '♥',
    diamonds: '♦',
    clubs: '♣',
    spades: '♠'
  }
  
  return `${card.rank}${suitSymbols[card.suit]}`
}

export const getCardColor = (card: Card): string => {
  return card.suit === 'hearts' || card.suit === 'diamonds' ? 'text-red-500' : 'text-black'
}

// 筹码格式化
export const formatChips = (amount: number): string => {
  if (amount >= 1000000) {
    return `${(amount / 1000000).toFixed(1)}M`
  } else if (amount >= 1000) {
    return `${(amount / 1000).toFixed(1)}K`
  }
  return amount.toString()
}

// 计算手牌强度
export const evaluateHand = (cards: Card[]): string => {
  if (cards.length < 5) return '不完整'
  
  // 简化的手牌评估逻辑
  const values = cards.map(card => card.value).sort((a, b) => b - a)
  const suits = cards.map(card => card.suit)
  
  // 检查同花
  const isFlush = suits.every(suit => suit === suits[0])
  
  // 检查顺子
  const isStraight = values.every((value, index) => 
    index === 0 || value === values[index - 1] - 1
  )
  
  if (isFlush && isStraight) return '同花顺'
  if (isFlush) return '同花'
  if (isStraight) return '顺子'
  
  // 检查对子和三条
  const valueCounts = values.reduce((acc, value) => {
    acc[value] = (acc[value] || 0) + 1
    return acc
  }, {} as Record<number, number>)
  
  const counts = Object.values(valueCounts).sort((a, b) => b - a)
  
  if (counts[0] === 4) return '四条'
  if (counts[0] === 3 && counts[1] === 2) return '葫芦'
  if (counts[0] === 3) return '三条'
  if (counts[0] === 2 && counts[1] === 2) return '两对'
  if (counts[0] === 2) return '一对'
  
  return '高牌'
}