// 扑克牌相关类型定义
export interface Card {
  suit: 'hearts' | 'diamonds' | 'clubs' | 'spades'
  rank: 'A' | '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9' | '10' | 'J' | 'Q' | 'K'
  value: number
}

export interface Player {
  id: string
  name: string
  chips: number
  position: number
  cards: Card[]
  isActive: boolean
  isDealer: boolean
  currentBet: number
}

export interface GameState {
  id: string
  players: Player[]
  communityCards: Card[]
  pot: number
  currentPlayer: string
  dealerPosition: number
  smallBlind: number
  bigBlind: number
  phase: 'preflop' | 'flop' | 'turn' | 'river' | 'showdown'
}

export interface GameAction {
  type: 'fold' | 'call' | 'raise' | 'check' | 'all-in'
  amount?: number
  playerId: string
}

// API 响应类型
export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
  message?: string
}

// WebSocket 消息类型
export interface WebSocketMessage {
  type: 'game_update' | 'player_action' | 'chat_message' | 'error'
  data: any
  timestamp: string
}