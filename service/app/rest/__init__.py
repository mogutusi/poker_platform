# REST 查询面(rest.md):事后/聚合查询 + 账号管理,走请求级读、不进 WS/reduce/world 写。
# 首个端点 GET /lobby/rooms(0048,唯一读 committed world 的 REST);leaderboard/hands/profile 读 DB(P7 续,依赖 P5 鉴权)。
