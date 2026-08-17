"use client"

import { cn } from "@/lib/utils"
import Image from "next/image"
import chipsImg from "@/pics/casino-chips.png"

interface PlayerInfoCardProps {
  player: {
    id: string
    name: string
    avatar?: string
    points?: number
    isReady?: boolean
  }
  isButton?: boolean
  isCurrentPlayer?: boolean
  className?: string
}

export default function PlayerInfoCard({
  player,
  isButton = false,
  isCurrentPlayer = false,
  className,
}: PlayerInfoCardProps) {
  return (
    <div
      className={cn(
        "relative bg-black/80 backdrop-blur-md border-2 rounded-lg p-2 shadow-2xl transition-all duration-300",
        isCurrentPlayer && "ring-2 ring-green-500/70 ring-offset-2 ring-offset-black/50",
        isCurrentPlayer && "border-green-500",
        !isCurrentPlayer && isButton && "border-primary/70",
        !isCurrentPlayer && !isButton && "border-primary/50",
        className
      )}
      style={{
        boxShadow: isCurrentPlayer
          ? "0 0 20px rgba(34, 197, 94, 0.6), 0 4px 12px rgba(0, 0, 0, 0.8)"
          : "0 4px 12px rgba(0, 0, 0, 0.6)",
      }}
    >
      {/* Ready indicator */}
      {player.isReady && (
        <div className="absolute -top-2 -right-2 bg-green-500 rounded-full w-6 h-6 flex items-center justify-center border-2 border-white shadow-lg z-10">
          <span className="text-white text-xs font-bold">✓</span>
        </div>
      )}

      {/*/!* Button badge *!/*/}
      {/*{isButton && (*/}
      {/*  <div className="absolute -top-2 left-1/2 -translate-x-1/2 bg-primary rounded-full px-2 py-0.5 text-[10px] font-bold text-primary-foreground border border-white/30 shadow-lg">*/}
      {/*    BTN*/}
      {/*  </div>*/}
      {/*)}*/}

      <div className="flex items-center gap-2">
        {/* Avatar */}
        <div className="relative h-10 w-10 shrink-0 rounded-full border-2 border-primary/70 bg-gradient-to-br from-primary/80 to-accent/60 overflow-hidden">
          {player.avatar ? (
            <img
              src={player.avatar}
              alt={player.name}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-sm font-bold text-primary-foreground">
              {player.name.slice(0, 1).toUpperCase()}
            </div>
          )}
        </div>

        {/* Player info */}
        <div className="flex-1 min-w-0">
          <p
            className="text-xs font-bold text-primary truncate"
            style={{
              textShadow: "0 0 8px rgba(212, 175, 55, 0.6), 0 1px 2px rgba(0, 0, 0, 0.8)",
              fontFamily: "var(--font-orbitron), sans-serif",
            }}
          >
            {player.name}
          </p>
          {player.points !== undefined && (
            <div className="flex items-center gap-1 mt-0.5">
              <div className="relative w-3 h-3">
                <Image
                  src={chipsImg}
                  alt="Chips"
                  fill
                  className="object-contain"
                />
              </div>
              <span
                className="text-[10px] font-bold text-amber-300"
                style={{
                  textShadow: "0 0 6px rgba(251, 191, 36, 0.6), 0 1px 2px rgba(0, 0, 0, 0.8)",
                }}
              >
                {player.points.toLocaleString()}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

