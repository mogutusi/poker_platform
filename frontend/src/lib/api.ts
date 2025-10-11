// API 客户端配置
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export class ApiClient {
  private baseUrl: string

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`
    
    const config: RequestInit = {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
      ...options,
    }

    try {
      const response = await fetch(url, config)
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      
      return await response.json()
    } catch (error) {
      console.error('API request failed:', error)
      throw error
    }
  }

  // 用户相关 API
  async login(username: string, password: string) {
    return this.request('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
  }

  async register(userData: { username: string; password: string; email: string }) {
    return this.request('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify(userData),
    })
  }

  // 游戏相关 API
  async getGameState(gameId: string) {
    return this.request(`/api/games/${gameId}`)
  }

  async joinGame(gameId: string) {
    return this.request(`/api/games/${gameId}/join`, {
      method: 'POST',
    })
  }

  async leaveGame(gameId: string) {
    return this.request(`/api/games/${gameId}/leave`, {
      method: 'POST',
    })
  }

  async makeAction(gameId: string, action: any) {
    return this.request(`/api/games/${gameId}/action`, {
      method: 'POST',
      body: JSON.stringify(action),
    })
  }

  // 用户信息 API
  async getUserProfile() {
    return this.request('/api/user/profile')
  }

  async updateUserProfile(data: any) {
    return this.request('/api/user/profile', {
      method: 'PUT',
      body: JSON.stringify(data),
    })
  }
}

export const apiClient = new ApiClient()