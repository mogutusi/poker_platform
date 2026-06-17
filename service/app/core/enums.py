from enum import StrEnum


class RoomStatus(StrEnum):
    PENDING_START = "pending_start"  # no hand running; waiting for StartHand
    HAND_STARTED = "hand_started"  # a hand is in progress


class HandStatus(StrEnum):
    PRE_FLOP = "pre_flop"  # hole cards dealt, before community cards
    FLOP = "flop"  # first 3 community cards
    TURN = "turn"  # 4th community card
    RIVER = "river"  # 5th community card
    SHOWDOWN = "showdown"  # reveal hole cards, decide winners
    ENDING = "ending"  # settling and clearing the hand

    @property
    def next_status(self) -> "HandStatus | None":
        # street advanced on betting-round close; RIVER -> SHOWDOWN, none after SHOWDOWN
        chain = {
            HandStatus.PRE_FLOP: HandStatus.FLOP,
            HandStatus.FLOP: HandStatus.TURN,
            HandStatus.TURN: HandStatus.RIVER,
            HandStatus.RIVER: HandStatus.SHOWDOWN,
        }
        return chain.get(self)


class PlayerActionType(StrEnum):
    FOLD = "fold"  # give up the hand
    BET = "bet"  # bet/call/raise, merged — carries a target total amount
    CHECK = "check"  # pass action with no chips, only when nothing to call


class PlayerStatus(StrEnum):
    ACTIVE = "active"  # can still act this hand
    FOLDED = "folded"  # gave up the hand
    ALLIN = "allin"  # all chips committed; cannot act further


class UserStatus(StrEnum):
    WATCHING = "watching"  # in room, not seated (spectator)
    OFFLINE = "offline"  # disconnected; seat/state held pending reconnect or cleanup
    SITTING_IN = "sitting_in"  # seated, not marked ready for next hand
    READY_TO_PLAY = "ready_to_play"  # seated and ready; dealt into the next hand
    SITTING_OUT = "sitting_out"  # seated but skipping hands
    PLAYING = "playing"  # seated and currently in a live hand

    def can_change_to(self, new_status: "UserStatus") -> bool:
        # any transition (system- or player-driven); used to gate every status change
        return (self, new_status) in USER_STATUS_TRANSITIONS

    def userself_can_change_to(self, new_status: "UserStatus") -> bool:
        # subset a player may request via SetUserStatus (excludes connection/hand-flow transitions)
        return (self, new_status) in USER_STATUS_SELF_TRANSITIONS


# All legal UserStatus transitions (system + player). Every transition must be checked here.
USER_STATUS_TRANSITIONS: set[tuple[UserStatus, UserStatus]] = {
    # disconnect / reconnect
    (UserStatus.WATCHING, UserStatus.OFFLINE),
    (UserStatus.READY_TO_PLAY, UserStatus.OFFLINE),
    (UserStatus.PLAYING, UserStatus.OFFLINE),
    (UserStatus.SITTING_IN, UserStatus.OFFLINE),
    (UserStatus.SITTING_OUT, UserStatus.OFFLINE),
    (UserStatus.OFFLINE, UserStatus.WATCHING),
    (UserStatus.OFFLINE, UserStatus.READY_TO_PLAY),
    (UserStatus.OFFLINE, UserStatus.PLAYING),
    (UserStatus.OFFLINE, UserStatus.SITTING_IN),
    (UserStatus.OFFLINE, UserStatus.SITTING_OUT),
    # sit down
    (UserStatus.WATCHING, UserStatus.SITTING_IN),
    # stand up
    (UserStatus.READY_TO_PLAY, UserStatus.WATCHING),
    (UserStatus.SITTING_OUT, UserStatus.WATCHING),
    (UserStatus.SITTING_IN, UserStatus.WATCHING),
    # stay in the table
    (UserStatus.SITTING_IN, UserStatus.READY_TO_PLAY),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_IN),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_IN, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_OUT, UserStatus.SITTING_IN),
    # game flow (system-driven on hand start/end)
    (UserStatus.READY_TO_PLAY, UserStatus.PLAYING),
    (UserStatus.PLAYING, UserStatus.SITTING_IN),
    (UserStatus.PLAYING, UserStatus.SITTING_OUT),
}

# Subset a player may initiate via SetUserStatus; excludes connection/hand-flow (system) transitions.
USER_STATUS_SELF_TRANSITIONS: set[tuple[UserStatus, UserStatus]] = {
    (UserStatus.SITTING_IN, UserStatus.READY_TO_PLAY),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_IN),
    (UserStatus.SITTING_IN, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_IN, UserStatus.WATCHING),
    (UserStatus.PLAYING, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_OUT, UserStatus.SITTING_IN),
    (UserStatus.SITTING_OUT, UserStatus.WATCHING),
    (UserStatus.READY_TO_PLAY, UserStatus.WATCHING),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_OUT),
}
