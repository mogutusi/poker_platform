// ⚠️ GENERATED — DO NOT EDIT BY HAND.
// Source of truth: service/app/wire/{server,client}.py (Pydantic).
// Regenerate: cd service && python scripts/gen_wire_ts.py
// Drift is guarded by tests/wire/test_codegen_uptodate.py (pytest goes red on stale output).

// ── enums ──

export type CardSuit = "h" | "d" | "c" | "s";

export type CardRank = "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "T" | "J" | "Q" | "K" | "A";

export type PlayerStatus = "active" | "folded" | "allin";

export type HandStatus = "pre_flop" | "flop" | "turn" | "river" | "showdown" | "ending";

export type PlayerActionType = "fold" | "bet" | "check";

export type UserStatus = "watching" | "offline" | "sitting_in" | "ready_to_play" | "sitting_out" | "playing";

export type RoomStatus = "pending_start" | "hand_started";

export type ErrorCode = "INTERNAL" | "NO_SUCH_ROOM" | "ROOM_FULL" | "ALREADY_IN_ROOM" | "NOT_IN_ROOM" | "SEAT_TAKEN" | "NOT_YOUR_SEAT" | "INVALID_STATUS_TRANSITION" | "INSUFFICIENT_POINTS" | "INVALID_BUY_IN" | "INVALID_SMALL_BLIND" | "HAND_IN_PROGRESS" | "NO_HAND" | "NOT_YOUR_TURN" | "ILLEGAL_ACTION" | "NOT_ENOUGH_PLAYERS" | "NOT_READY" | "NO_VOTE_IN_PROGRESS" | "NOT_A_VOTER" | "CANNOT_OPEN_VOTE" | "INVALID_MESSAGE" | "MESSAGE_TOO_LONG" | "RATE_LIMITED" | "CANNOT_DM_SELF";

// ── value objects ──

export interface Card {
  rank: CardRank;
  suit: CardSuit;
}

export interface PlayerView {
  seat_position: number;
  nickname: string;
  points: number;
  bet_amount: number;
  status: PlayerStatus;
}

export interface ShowdownReveal {
  seat_position: number;
  nickname: string;
  hole_cards: [Card, Card];
}

export interface NickAmount {
  nickname: string;
  amount: number;
}

export interface SeatStack {
  seat_position: number;
  nickname: string;
  points: number;
}

export interface SeatView {
  seat_position: number;
  nickname: string;
  status: UserStatus;
  points: number;
  new_here: boolean;
}

export interface FreeEntryVoteView {
  candidates: string[];
  voters: string[];
  approvals: string[];
}

// ── server → client messages ──

export interface HandStarted {
  type: "hand_started";
  hand_seq: number;
  button_position: number;
  small_blind: number;
  big_blind: number;
  players: PlayerView[];
  acting_position: number | null;
  pot: number;
  last_bet: number;
  min_raise_to: number;
}

export interface HoleCards {
  type: "hole_cards";
  cards: [Card, Card];
}

export interface HandStatusChanged {
  type: "hand_status_changed";
  status: HandStatus;
  board: Card[];
  last_bet: number;
  min_raise_to: number;
  players: PlayerView[];
}

export interface PlayerActed {
  type: "player_acted";
  seat_position: number;
  nickname: string;
  action: PlayerActionType;
  bet_amount: number;
  points: number;
  status: PlayerStatus;
  last_bet: number;
  min_raise_to: number;
  pot: number;
  acting_position: number | null;
}

export interface HandShowDown {
  type: "hand_show_down";
  board: Card[];
  reveals: ShowdownReveal[];
}

export interface HandEnded {
  type: "hand_ended";
  winnings: NickAmount[];
  refunds: NickAmount[];
  stacks: SeatStack[];
}

export interface UserStatusChanged {
  type: "user_status_changed";
  nickname: string;
  status: UserStatus;
  seat_position: number | null;
  new_here: boolean | null;
}

export interface UserJoined {
  type: "user_joined";
  nickname: string;
}

export interface UserLeft {
  type: "user_left";
  nickname: string;
  seat_position: number | null;
}

export interface PlayerBoughtIn {
  type: "player_bought_in";
  nickname: string;
  seat_position: number;
  amount: number;
  seat_points: number;
}

export interface RoomConfigChanged {
  type: "room_config_changed";
  small_blind: number;
  big_blind: number;
  buy_in: number;
}

export interface StateSnapshot {
  type: "state_snapshot";
  room: string;
  max_seats: number;
  button_position: number;
  small_blind: number;
  big_blind: number;
  buy_in: number;
  room_status: RoomStatus;
  seats: SeatView[];
  watchers: string[];
  hand_status: HandStatus | null;
  board: Card[];
  pot: number;
  last_bet: number;
  min_raise_to: number;
  acting_position: number | null;
  players: PlayerView[];
  your_hole_cards: [Card, Card] | null;
  free_entry_vote: FreeEntryVoteView | null;
}

export interface ChatMessage {
  type: "chat_message";
  from_nick: string;
  text: string;
}

export interface RoomChatHistory {
  type: "room_chat_history";
  room: string;
  messages: ChatMessage[];
}

export interface DMDelivered {
  type: "dm_delivered";
  msg_id: string;
  from_nick: string;
  text: string;
  created_at: string;
}

export interface DMUndelivered {
  type: "dm_undelivered";
  to_nick: string;
}

export interface DMRead {
  type: "dm_read";
  reader_nick: string;
  read_through: string;
}

export interface FreeEntryVoteUpdated {
  type: "free_entry_vote_updated";
  candidates: string[];
  voters: string[];
  approvals: string[];
}

export interface FreeEntryVoteClosed {
  type: "free_entry_vote_closed";
  passed: boolean;
  waived: string[];
}

export interface ErrorMessage {
  type: "error";
  code: ErrorCode;
  detail?: string;
}

// ── client → server messages ──

export interface SitDown {
  type: "sit_down";
  seat: number;
  wait_for_big_blind?: boolean;
}

export interface BuyIn {
  type: "buy_in";
  seat: number;
  amount: number;
}

export interface SetUserStatus {
  type: "set_user_status";
  status: UserStatus;
  seat?: number | null;
}

export interface SetSmallBlind {
  type: "set_small_blind";
  amount: number;
}

export interface SetBuyIn {
  type: "set_buy_in";
  amount: number;
}

export interface LeaveRoom {
  type: "leave_room";
}

export interface StartHand {
  type: "start_hand";
  seat: number;
}

export interface PlayerAction {
  type: "player_action";
  action: PlayerActionType;
  bet_amount?: number | null;
}

export interface RoomChat {
  type: "room_chat";
  text: string;
}

export interface OpenFreeEntryVote {
  type: "open_free_entry_vote";
}

export interface VoteFreeEntry {
  type: "vote_free_entry";
  approve: boolean;
}

export interface JoinRoom {
  type: "join_room";
  room: string;
}

export interface FetchRoomChat {
  type: "fetch_room_chat";
  room: string;
}

export interface DirectMessage {
  type: "direct_message";
  to_nick: string;
  text: string;
}

export interface DMMarkRead {
  type: "dm_mark_read";
  peer_nick: string;
  read_through: string;
}

// ── discriminated unions ──

export type ServerMessage =
  | HandStarted
  | HoleCards
  | HandStatusChanged
  | PlayerActed
  | HandShowDown
  | HandEnded
  | UserStatusChanged
  | UserJoined
  | UserLeft
  | PlayerBoughtIn
  | RoomConfigChanged
  | StateSnapshot
  | ChatMessage
  | RoomChatHistory
  | DMDelivered
  | DMUndelivered
  | DMRead
  | FreeEntryVoteUpdated
  | FreeEntryVoteClosed
  | ErrorMessage;

export type ClientMessage =
  | SitDown
  | BuyIn
  | SetUserStatus
  | SetSmallBlind
  | SetBuyIn
  | LeaveRoom
  | StartHand
  | PlayerAction
  | RoomChat
  | OpenFreeEntryVote
  | VoteFreeEntry
  | JoinRoom
  | FetchRoomChat
  | DirectMessage
  | DMMarkRead;

// ── emoji catalog (chat render; backend passthrough, see messaging.md / changes/0034) ──

export type EmojiCode = "smile" | "laugh" | "cry" | "cool" | "thinking" | "poker_face" | "thumbs_up" | "clap" | "fire" | "gg" | "fold" | "all_in";

export interface EmojiMeta {
  label: string;
  glyph: string;
}

export const EMOJI_CATALOG: Record<EmojiCode, EmojiMeta> = {
  "smile": { label: "微笑", glyph: "😊" },
  "laugh": { label: "大笑", glyph: "😂" },
  "cry": { label: "哭", glyph: "😭" },
  "cool": { label: "酷", glyph: "😎" },
  "thinking": { label: "思考", glyph: "🤔" },
  "poker_face": { label: "扑克脸", glyph: "😐" },
  "thumbs_up": { label: "赞", glyph: "👍" },
  "clap": { label: "鼓掌", glyph: "👏" },
  "fire": { label: "火", glyph: "🔥" },
  "gg": { label: "打得好", glyph: "🎉" },
  "fold": { label: "弃牌", glyph: "🏳️" },
  "all_in": { label: "全下", glyph: "🟢" },
};
