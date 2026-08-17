"use client"

import { cn } from "@/lib/utils"
import Image from "next/image"
import chipsImg from "@/pics/casino-chips.png"

interface SeatPlayer {
  id: string
  name: string
  avatar?: string
  points?: number
}

interface TableSeatProps {
  seatNumber: number
  player?: SeatPlayer
  isButton?: boolean
  onClick?: () => void
  className?: string
}

export default function TableSeat({
  seatNumber,
  player,
  isButton = false,
  onClick,
  className,
}: TableSeatProps) {
  const isEmpty = !player
  const isClickable = isEmpty && onClick

  return (
    <div
      className={cn(
        "relative flex flex-col items-center transition-all duration-300",
        isClickable && "cursor-pointer hover:scale-110",
        className
      )}
      onClick={isClickable ? onClick : undefined}
    >
      {/* Seat icon/avatar */}
      <div
        className={cn(
          "relative flex h-16 w-16 items-center justify-center rounded-full border-2 transition-all duration-300",
          isEmpty
            ? "border-primary/40 bg-secondary/40 hover:border-primary/70 hover:bg-secondary/60"
            : "border-primary/70 bg-accent/60 shadow-lg",
          isButton && "ring-2 ring-primary/50 ring-offset-2 ring-offset-accent/40"
        )}
      >
        {isEmpty ? (
          // Empty seat icon
          <div className="flex flex-col items-center justify-center">
            <svg
              className="h-8 w-8 text-primary/50"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
              />
            </svg>
          </div>
        ) : (
          // Occupied seat with avatar
          <>
            <div className="flex h-full w-full items-center justify-center rounded-full bg-gradient-to-br from-primary/80 to-accent/60 text-lg font-bold text-primary-foreground">
              {player.avatar ? (
                <img
                  src={player.avatar}
                  alt={player.name}
                  className="h-full w-full rounded-full object-cover"
                />
              ) : (
                player.name.slice(0, 1).toUpperCase()
              )}
            </div>
            {/*{isButton && (*/}
            {/*  <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 rounded-full bg-primary px-2 py-0.5 text-[10px] font-bold text-primary-foreground">*/}
            {/*    BTN*/}
            {/*  </div>*/}
            {/*)}*/}
          </>
        )}
      </div>

      {/* Player name/id and points below seat */}
      {player && (
        <div className="mt-2 max-w-[90px] truncate text-center px-1">
          <p className="text-xs font-semibold text-primary leading-tight">{player.name}</p>
          {player.points !== undefined && (
            <div className="flex items-center justify-center gap-1 mt-1">
              <div className="relative w-4 h-4">
                <Image
                  src={chipsImg}
                  alt="Chips"
                  fill
                  className="object-contain"
                />
              </div>
              <span className="text-[10px] font-bold text-primary">{player.points.toLocaleString()}</span>
            </div>
          )}
        </div>
      )}

      {/* Empty seat hint */}
      {isEmpty && isClickable && (
        <div className="mt-1 text-center px-1">
          <p className="text-[10px] text-primary/60 leading-tight">点击入座</p>
        </div>
      )}
    </div>
  )
}

