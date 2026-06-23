# wire 协议:对外报文 ClientMessage/ServerMessage 的 Pydantic 单一事实源(治理见 docs/wire.md)。
# 与 core 域模型物理分开;codegen(scripts/gen_wire_ts.py)据此生成前端 TS,前端只消费、禁手写。
