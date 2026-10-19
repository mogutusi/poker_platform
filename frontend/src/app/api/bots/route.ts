// Dev-only: start / stop AI players (scripts/bots.mjs) for a room from the table page.
//
// The bots are a separate Node process that logs in as dev seed accounts and plays over the real
// encrypted protocol, exactly like a browser would (see scripts/bots.mjs). This route only launches
// and stops that process; it never talks to the game server itself. Their log goes to this dev
// server's console (WebStorm's "Frontend" run window).
//
// Only available under `next dev`: in a production build every method answers 404, because it
// spawns processes and uses dev credentials.

import { spawn, spawnSync, type ChildProcess } from "node:child_process"
import { existsSync } from "node:fs"
import path from "node:path"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"

const STYLES = ["tight", "balanced", "loose", "aggressive"] as const

// One bot process per room. Kept on globalThis so it survives dev-server hot reloads of this module.
const g = globalThis as unknown as { __pokerBots?: Map<string, { proc: ChildProcess; count: number; style: string }> }
const running = (g.__pokerBots ??= new Map())

const isDev = process.env.NODE_ENV === "development"
const notFound = () => new Response("Not found", { status: 404 })

function frontendDir(): string {
  return process.cwd() // `next dev` runs from frontend/
}

/**
 * The bots import the frontend's own crypto as a plain ES module (dist-smoke/crypto.js); build it
 * if it's missing. Uses esbuild's JS API through `node -e`: the `node_modules/.bin` / `bin/esbuild`
 * entry points differ per OS (a native binary on Linux/macOS, a .cmd shim on Windows).
 */
function ensureCryptoBundle(): string | null {
  const dir = frontendDir()
  const out = path.join(dir, "dist-smoke", "crypto.js")
  if (existsSync(out)) return null
  const script =
    "require('esbuild').buildSync({entryPoints:['src/crypto/index.ts'],bundle:true,format:'esm'," +
    `platform:'neutral',outfile:${JSON.stringify(out)},logLevel:'warning'})`
  const r = spawnSync(process.execPath, ["-e", script], { cwd: dir, encoding: "utf8" })
  return r.status === 0 ? null : r.stderr || "esbuild failed"
}

/** Same backend the browser uses. `localhost` → 127.0.0.1: Node may resolve localhost to ::1 on
 *  Windows, while uvicorn listens on 127.0.0.1 only. */
function backendUrl(): string {
  return (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace("//localhost", "//127.0.0.1")
}

function status(room: string) {
  const entry = running.get(room)
  return { room, running: !!entry, count: entry?.count ?? 0, style: entry?.style ?? null }
}

export async function GET(req: Request) {
  if (!isDev) return notFound()
  const room = new URL(req.url).searchParams.get("room") ?? ""
  return Response.json(status(room))
}

export async function POST(req: Request) {
  if (!isDev) return notFound()
  const body = (await req.json().catch(() => ({}))) as { room?: string; count?: number; style?: string; exclude?: string[] }
  const room = String(body.room ?? "").trim()
  if (!room) return Response.json({ error: "缺少房间名" }, { status: 400 })
  if (running.has(room)) return Response.json({ error: "这个房间已经有一组 AI 在打了,先移除再加" }, { status: 409 })
  const count = Math.max(1, Math.min(8, Math.floor(Number(body.count) || 3)))
  const style = STYLES.includes(body.style as (typeof STYLES)[number]) ? body.style! : "balanced"
  // Never use the requester's own account as a bot: connecting it would knock them offline.
  const exclude = (body.exclude ?? []).filter((n) => /^[\w-]{1,32}$/.test(n))

  const buildError = ensureCryptoBundle()
  if (buildError) return Response.json({ error: `打包加密模块失败:${buildError}` }, { status: 500 })

  const args = [path.join(frontendDir(), "scripts", "bots.mjs"), "--room", room, "--count", String(count), "--style", style]
  if (exclude.length) args.push("--exclude", exclude.join(","))
  const proc = spawn(process.execPath, args, {
    cwd: frontendDir(),
    env: { ...process.env, BOTS_STDIN_CONTROL: "1", SMOKE_API_URL: backendUrl() },
    stdio: ["pipe", "pipe", "pipe"],
  })
  running.set(room, { proc, count, style })

  // Forward the bots' log to this dev server's console, and keep the tail so a failure can be
  // shown in the browser instead of the button just silently resetting.
  let tail = ""
  const onOutput = (chunk: Buffer, to: NodeJS.WriteStream) => {
    const text = chunk.toString()
    to.write(text)
    tail = (tail + text).slice(-1500)
  }
  proc.stdout?.on("data", (c: Buffer) => onOutput(c, process.stdout))
  proc.stderr?.on("data", (c: Buffer) => onOutput(c, process.stderr))

  // Answer once the bots are seated and ready ("N 个 AI 已就位"), or with the error if the process dies first.
  const outcome = await new Promise<{ ok: boolean; seated?: number }>((resolve) => {
    const timer = setTimeout(() => resolve({ ok: true }), 30_000) // still joining: report as running
    proc.stdout?.on("data", () => {
      const m = tail.match(/(\d+) 个 AI 已就位/)
      if (m) {
        clearTimeout(timer)
        resolve({ ok: true, seated: Number(m[1]) })
      }
    })
    proc.on("exit", () => {
      clearTimeout(timer)
      resolve({ ok: false })
    })
  })
  proc.on("exit", () => {
    if (running.get(room)?.proc === proc) running.delete(room)
  })
  if (!outcome.ok) {
    running.delete(room)
    const lines = tail
      .split(/\r?\n/)
      .filter((l) => l.trim() && !/MODULE_TYPELESS|Reparsing|eliminate this warning|trace-warnings/.test(l))
    return Response.json({ error: `AI 没能加入:${lines.slice(-3).join(" / ") || "进程意外退出"}` }, { status: 500 })
  }
  if (outcome.seated) running.set(room, { proc, count: outcome.seated, style })
  return Response.json(status(room))
}

export async function DELETE(req: Request) {
  if (!isDev) return notFound()
  const room = new URL(req.url).searchParams.get("room") ?? ""
  const entry = running.get(room)
  if (entry) {
    // Closing stdin tells the bots to leave the table cleanly (they send leave_room first).
    entry.proc.stdin?.end()
    setTimeout(() => entry.proc.kill(), 3000)
    running.delete(room)
  }
  return Response.json(status(room))
}
