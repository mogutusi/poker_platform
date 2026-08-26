// UI 展示类型。**协议类型一律来自 `wire.gen.ts`**(后端 codegen 产物,只读不手改):
// 凡是和服务器交换的数据,类型都在那里;这里只放渲染自己要用的形状。
//
// 曾经这里还有 Player/GameState/GameAction/ApiResponse/WebSocketMessage 五个接口,是 0076 合入上游
// mockup 时带来的。它们手写了一份协议形状,`chips` 与 `phase` 因此和后端 enum 漂移——这份漂移被当作
// 「手写第二份协议类型必然漂移」的活反例记档了很久。0099 数引用时发现它们(以及唯一用到 Player 的
// PlayerSeat 组件)**一个消费者都没有**,已随死代码一并删除。
export interface Card {
  suit: 'hearts' | 'diamonds' | 'clubs' | 'spades'  // 花色,决定牌面图与红/黑配色
  rank: 'A' | '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9' | '10' | 'J' | 'Q' | 'K'  // 点数,渲染用的字面量(与后端 CardRank 各自独立,这里只服务图片命名)
  value: number  // 排序用的数值(A=14…2=2);纯展示排序,牌力判定一律在服务器
}
