# docs_generator.py - 生成 WebSocket 消息文档
"""生成 WebSocket API 文档"""
from pydantic import TypeAdapter
import json

from app.pokertable.wsm_schemas import ClientMessage, ServerMessage


def generate_message_docs():
    """生成消息类型文档"""
    
    # 生成 JSON Schema
    client_adapter = TypeAdapter(ClientMessage)
    server_adapter = TypeAdapter(ServerMessage)
    
    client_schema = client_adapter.json_schema()
    server_schema = server_adapter.json_schema()
    
    # 保存为文件（前端可以使用）
    with open("app/docs/websocket_client_messages.json", "w") as f:
        json.dump(client_schema, f, indent=2)
    
    with open("app/docs/websocket_server_messages.json", "w") as f:
        json.dump(server_schema, f, indent=2)
    
    print("✅ WebSocket 消息文档已生成")


# def print_markdown_docs():
#     """打印 Markdown 格式的文档"""
#     print("# WebSocket API 文档\n")
    
#     print("## 客户端 -> 服务器消息\n")
    
#     messages = [
#         ("设置用户状态", SetUserStatusMessage),
#         ("设置小盲", SetSmallBlindMessage),
#         ("设置买入", SetBuyInMessage),
#         ("开始手牌", StartHandMessage),
#         ("玩家操作", PlayerActionMessage),
#         ("聊天", ChatMessage),
#     ]
    
#     for title, msg_class in messages:
#         print(f"### {title}\n")
#         print(f"**type:** `{msg_class.model_fields['type'].default}`\n")
#         print("**示例:**")
#         print("")
#         example = msg_class.model_config.get("json_schema_extra", {}).get("example")
#         print(json.dumps(example, indent=2, ensure_ascii=False))
#         print("```\n")


if __name__ == "__main__":
    generate_message_docs()
    # print_markdown_docs()## 🎨 前端 TypeScript 类型（可自动生成）

