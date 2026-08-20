"use client"

import type React from "react"
import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { changeNickname, changePassword, fetchProfile, RestError, type Profile } from "@/transport/rest"
import { getSession, saveKUser } from "@/transport/session"
import { setMe } from "@/store/room"

/** 一个区块的操作结果:成功和失败共用一条提示条,免得三个表单各写两套。 */
type Feedback = { ok: boolean; text: string } | null

/** 昵称长度上限对齐后端 profile.py 的 _NICKNAME_MAX_LEN(超了后端只回笼统的 400)。 */
const NICKNAME_MAX_LEN = 50
/** K_user 是 16 字节 = 32 个十六进制字符,见 docs/transport.md §六。 */
const K_USER_HEX_RE = /^[0-9a-fA-F]{32}$/

/**
 * 两个信封端点共有的错误分层(service/docs/rest.md):401 是信封/会话的事,与业务无关;
 * 500 是服务端故障。业务码(403/409/400)含义各端点不同,返回 null 交给调用方自己译。
 */
function describeCommon(err: unknown): string | null {
  if (err instanceof RestError) {
    if (err.status === 401) return "会话已失效,请回登录页重新登录。"
    if (err.status === 500) return "服务器出错了,稍后再试。"
    return null
  }
  return "网络不通,检查后端是否在跑。"
}

export default function SettingsPage() {
  const router = useRouter()

  const [profile, setProfile] = useState<Profile | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [nickname, setNickname] = useState("")
  const [nicknameBusy, setNicknameBusy] = useState(false)
  const [nicknameMsg, setNicknameMsg] = useState<Feedback>(null)

  const [oldPassword, setOldPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [passwordBusy, setPasswordBusy] = useState(false)
  const [passwordMsg, setPasswordMsg] = useState<Feedback>(null)

  const [kUser, setKUser] = useState("")
  const [kUserMsg, setKUserMsg] = useState<Feedback>(null)
  // 会话是内存态,服务端预渲染时必然为空;放 state 里等挂载后再读,免得 SSR 与首帧对不上。
  const [rotateHint, setRotateHint] = useState(false)

  useEffect(() => {
    // 会话只活在内存里(docs/transport.md §六),刷新即失;本页每个操作都要信封,没会话直接回登录页。
    const session = getSession()
    if (!session) {
      router.replace("/")
      return
    }
    setRotateHint(session.rotateHint)
    let cancelled = false
    fetchProfile()
      .then((p) => {
        if (cancelled) return
        setProfile(p)
        setNickname(p.nickname)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(describeCommon(err) ?? "读取资料失败,请稍后再试。")
      })
    return () => {
      cancelled = true
    }
  }, [router])

  const handleNickname = async (e: React.FormEvent) => {
    e.preventDefault()
    setNicknameMsg(null)
    const next = nickname.trim()
    // 后端把「空 / 首尾空白 / 超长 / 与现名相同」全归成一个 400,回包分不出是哪种;
    // 所以这几种在本地先拦掉并给准话,剩下的 400 才真是意外。
    if (!next) return setNicknameMsg({ ok: false, text: "昵称不能为空。" })
    if (next.length > NICKNAME_MAX_LEN) return setNicknameMsg({ ok: false, text: `昵称最多 ${NICKNAME_MAX_LEN} 个字。` })
    if (next === profile?.nickname) return setNicknameMsg({ ok: false, text: "这就是你现在的昵称。" })

    setNicknameBusy(true)
    try {
      const res = await changeNickname(next)
      // 回包里的 nickname 是权威值(并发改名时以服务器为准),拿它回填而不是拿本地输入。
      setProfile((p) => (p ? { ...p, nickname: res.nickname } : p))
      setNickname(res.nickname)
      // 「我是谁」也要跟着改:昵称是判断哪个座位是我、哪条私信是我发的的键,
      // 不同步的话回牌桌会认不出自己的座位,私聊也会把自己当成别人。
      setMe(res.nickname)
      setNicknameMsg({ ok: true, text: `昵称已改为「${res.nickname}」。` })
    } catch (err) {
      const common = describeCommon(err)
      if (common) setNicknameMsg({ ok: false, text: common })
      else if (err instanceof RestError && err.status === 403)
        // 昵称是服务器房间状态的键,在房中改会让键错乱,所以后端只允许大厅改。
        setNicknameMsg({ ok: false, text: "你还在房间里。昵称只能在大厅改,先离开牌桌再回来。" })
      else if (err instanceof RestError && err.status === 409)
        setNicknameMsg({ ok: false, text: "这个昵称已经被人占了,换一个。" })
      else setNicknameMsg({ ok: false, text: "昵称不合法,换一个再试。" })
    } finally {
      setNicknameBusy(false)
    }
  }

  const handlePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    setPasswordMsg(null)
    if (!newPassword) return setPasswordMsg({ ok: false, text: "新密码不能为空。" })

    setPasswordBusy(true)
    try {
      await changePassword(oldPassword, newPassword)
      setOldPassword("")
      setNewPassword("")
      // 后端明确「改密码不吊销其它会话」(rest.md §用户资料),所以别吓唬用户说要重新登录。
      setPasswordMsg({ ok: true, text: "密码已更新。当前会话继续有效,下次登录用新密码。" })
    } catch (err) {
      const common = describeCommon(err)
      if (common) setPasswordMsg({ ok: false, text: common })
      else if (err instanceof RestError && err.status === 403)
        setPasswordMsg({ ok: false, text: "旧密码不对(或这个账号没有启用密码登录)。" })
      else setPasswordMsg({ ok: false, text: "请求被拒,检查两个密码框再试。" })
    } finally {
      setPasswordBusy(false)
    }
  }

  const handleKUser = (e: React.FormEvent) => {
    e.preventDefault()
    setKUserMsg(null)
    const hex = kUser.trim()
    // saveKUser 自己也会校验长度,但它的报错是给开发者看的;这里先自查一遍好给人话。
    if (!K_USER_HEX_RE.test(hex)) return setKUserMsg({ ok: false, text: "要 32 个十六进制字符(0-9 a-f)。" })
    try {
      saveKUser(hex)
      setKUser("")
      // K_user 只在登录握手时用,换钥不影响手上这个会话——说清楚,免得用户以为要立刻重登。
      setKUserMsg({ ok: true, text: "新钥匙已存到本机。当前会话不受影响,下次登录起生效。" })
    } catch {
      setKUserMsg({ ok: false, text: "这把钥匙存不下,确认是管理员发的那 32 位十六进制。" })
    }
  }

  return (
    <div className="min-h-screen relative overflow-hidden bg-background text-foreground p-4 md:p-8">
      {/* 背景花色与大厅/登录页保持同一套视觉语言 */}
      <div className="pointer-events-none absolute inset-0 opacity-10">
        <div className="absolute -top-4 left-10 text-8xl md:text-9xl float">♠</div>
        <div className="absolute top-20 right-10 text-7xl md:text-8xl float" style={{ animationDelay: "0.5s" }}>
          ♥
        </div>
        <div className="absolute bottom-24 left-6 text-7xl md:text-8xl float" style={{ animationDelay: "1s" }}>
          ♣
        </div>
        <div className="absolute -bottom-4 right-6 text-8xl md:text-9xl float" style={{ animationDelay: "1.5s" }}>
          ♦
        </div>
      </div>

      <div className="relative z-10 mx-auto flex max-w-3xl flex-col gap-5">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">Settings</p>
            <p className="text-2xl font-semibold text-primary">账号设置</p>
          </div>
          {/* 本页只有这一个出口 */}
          <Button
            variant="outline"
            size="sm"
            className="border-primary/40 bg-card/60 text-xs uppercase tracking-wide hover:bg-primary/10"
            onClick={() => router.push("/lobby")}
          >
            返回大厅
          </Button>
        </div>

        {loadError && (
          <div className="rounded-lg border-2 border-destructive/50 bg-destructive/20 p-3 text-sm font-semibold">
            {loadError}
          </div>
        )}

        {/* 我的资料 */}
        <Card className="border-2 border-primary/30 bg-card/95 p-5 shadow-xl">
          <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">Profile</p>
          <p className="mb-4 text-lg font-semibold text-primary">我的资料</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="昵称" value={profile?.nickname} />
            <Field label="账号名" value={profile?.name} />
            {/* 这是结算后的全局积分,不是桌上筹码——桌上那份只在 StateSnapshot 里 */}
            <Field label="结算积分" value={profile ? profile.points.toLocaleString() : undefined} />
          </div>
        </Card>

        {/* 改昵称 */}
        <Card className="border-2 border-primary/30 bg-card/95 p-5 shadow-xl">
          <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">Nickname</p>
          <p className="text-lg font-semibold text-primary">更改昵称</p>
          <p className="mt-1 text-xs text-muted-foreground">
            昵称是牌桌上别人看到的名字,也是服务器认人的键,所以只能在大厅(不在任何房间时)改。
          </p>
          <form onSubmit={handleNickname} className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1 space-y-2">
              <Label htmlFor="nickname" className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
                新昵称
              </Label>
              <Input
                id="nickname"
                value={nickname}
                onChange={(e) => setNickname(e.target.value)}
                maxLength={NICKNAME_MAX_LEN}
                disabled={nicknameBusy || !profile}
                className="h-10 border-primary/40 bg-black/30"
              />
            </div>
            <Button
              type="submit"
              disabled={nicknameBusy || !profile}
              className="h-10 bg-primary font-semibold text-primary-foreground hover:bg-primary/90"
            >
              {nicknameBusy ? "提交中..." : "保存昵称"}
            </Button>
          </form>
          <Notice msg={nicknameMsg} />
        </Card>

        {/* 改密码 */}
        <Card className="border-2 border-primary/30 bg-card/95 p-5 shadow-xl">
          <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">Password</p>
          <p className="text-lg font-semibold text-primary">修改密码</p>
          <p className="mt-1 text-xs text-muted-foreground">
            要填旧密码:光有会话改不动密码,盗到会话的人也就锁不死你。改完其它设备的登录状态不受影响。
          </p>
          <form onSubmit={handlePassword} className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="old-password" className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
                旧密码
              </Label>
              <Input
                id="old-password"
                type="password"
                autoComplete="current-password"
                value={oldPassword}
                onChange={(e) => setOldPassword(e.target.value)}
                disabled={passwordBusy}
                className="h-10 border-primary/40 bg-black/30"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-password" className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
                新密码
              </Label>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                disabled={passwordBusy}
                className="h-10 border-primary/40 bg-black/30"
              />
            </div>
            <div className="sm:col-span-2">
              <Button
                type="submit"
                disabled={passwordBusy}
                className="h-10 bg-primary font-semibold text-primary-foreground hover:bg-primary/90"
              >
                {passwordBusy ? "提交中..." : "保存密码"}
              </Button>
            </div>
          </form>
          <Notice msg={passwordMsg} />
        </Card>

        {/* 换 K_user */}
        <Card className="border-2 border-primary/30 bg-card/95 p-5 shadow-xl">
          <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">K_user</p>
          <p className="text-lg font-semibold text-primary">更换 K_user 密钥</p>
          <p className="mt-1 text-xs text-muted-foreground">
            K_user 是管理员带外发给你的 32 位十六进制密钥,登录握手要用它,与密码是两回事。
            管理员每周轮换一次;换过之后不在这里更新本地这把,下次登录就登不进去。
          </p>
          {rotateHint && (
            // 登录时服务器是用宽限期内的旧钥认出你的,宽限期一过这把就废了。
            <p className="mt-3 rounded-lg border-2 border-primary/50 bg-primary/15 p-3 text-sm font-semibold">
              你这次登录用的是旧钥匙,请尽快向管理员要新的换上。
            </p>
          )}
          <form onSubmit={handleKUser} className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1 space-y-2">
              <Label htmlFor="kuser" className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
                新 K_user
              </Label>
              <Input
                id="kuser"
                type="password"
                value={kUser}
                onChange={(e) => setKUser(e.target.value)}
                placeholder="32 位十六进制"
                className="h-10 border-primary/40 bg-black/30 font-mono"
              />
            </div>
            <Button
              type="submit"
              className="h-10 bg-primary font-semibold text-primary-foreground hover:bg-primary/90"
            >
              保存密钥
            </Button>
          </form>
          <Notice msg={kUserMsg} />
        </Card>
      </div>
    </div>
  )
}

/** 资料里的只读一项。value 还没到就显示占位,不拿空串冒充「没有」。 */
function Field({ label, value }: { label: string; value?: string }) {
  return (
    <div className="rounded-lg bg-secondary/50 p-3">
      <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">{label}</p>
      <p className="mt-1 text-base font-semibold text-card-foreground">{value ?? "…"}</p>
    </div>
  )
}

function Notice({ msg }: { msg: Feedback }) {
  if (!msg) return null
  return (
    <p
      className={`mt-3 rounded-lg border-2 p-3 text-sm font-semibold ${
        msg.ok ? "border-primary/50 bg-primary/15" : "border-destructive/60 bg-destructive/20"
      }`}
    >
      {msg.text}
    </p>
  )
}
