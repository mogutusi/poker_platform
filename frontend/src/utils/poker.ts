// 纯展示用的小工具。
//
// 这里**不放任何牌局规则**:发牌、洗牌、比牌力全部在后端 reduce 里,前端复算只会和服务器分叉
// (见 docs/architecture.md「服务器是唯一真相」)。0078 已删掉原先的 createDeck / shuffleDeck /
// evaluateHand —— 它们是没有后端时的占位,留着会诱使人再去本地算一遍。

/** 筹码显示:上千折成 k,保留一位小数。 */
export const formatChips = (amount: number): string => {
  if (amount >= 1000) {
    return `${(amount / 1000).toFixed(1)}k`
  }
  return amount.toString()
}
