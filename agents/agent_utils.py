#!/usr/bin/env python3
"""
agent_utils.py - Agent 工具函数集合

提供 agent 运行时需要的辅助函数，包括日志记录和消息处理等。
"""

from typing import Any, Dict, List
from agent_logger import Events


def log_model_output(trace_logger, response, current_step: int) -> None:
    """记录模型输出到日志

    Args:
        trace_logger: 轨迹日志记录器
        response: Anthropic API 的响应对象
        current_step: 当前步骤数
    """
    if not trace_logger:
        return

    # 提取 tool_calls
    tool_calls = []
    for block in response.content:
        if hasattr(block, "type") and block.type == "tool_use":
            tool_calls.append({
                "id": block.id if hasattr(block, "id") else "",
                "name": block.name if hasattr(block, "name") else "",
                "input": block.input if hasattr(block, "input") else {},
            })

    # 提取文本内容
    content_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            content_text += block.text

    # 提取使用情况
    usage_info = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    if hasattr(response, "usage"):
        usage_info = {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        }

    # 记录到日志
    trace_logger.log_event(Events.MODEL_OUTPUT, {
        "content": content_text,
        "usage": usage_info,
        "tool_calls": tool_calls,
        "stop_reason": response.stop_reason,
    }, step=current_step)


def truncate_messages_for_log(messages: List[Dict[str, Any]], log_content_truncate_length: int = 400) -> List[Dict[str, Any]]:
    """截断 messages 以便记录到日志中

    每条消息的 content 最多保留 log_content_truncate_length 个字符

    Args:
        messages: 原始消息列表
        log_content_truncate_length: 日志内容截断长度

    Returns:
        截断后的消息列表
    """
    # 第一步：建立 tool_use_id → name 的映射
    tool_id_to_name = {}
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if hasattr(block, "type") and block.type == "tool_use":
                    tool_id = block.id if hasattr(block, "id") else ""
                    tool_name = block.name if hasattr(block, "name") else ""
                    if tool_id and tool_name:
                        tool_id_to_name[tool_id] = tool_name
                elif isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_id = block.get("id", "")
                    tool_name = block.get("name", "")
                    if tool_id and tool_name:
                        tool_id_to_name[tool_id] = tool_name

    # 第二步：截断 messages
    truncated = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content")

        if isinstance(content, str):
            # 字符串内容，直接截断
            truncated.append({
                "role": role,
                "content": content[:log_content_truncate_length] + "..." if len(content) > log_content_truncate_length else content
            })
        elif isinstance(content, list):
            # 列表内容（text 和 tool_use blocks）
            truncated_blocks = []
            for block in content:
                if hasattr(block, "type"):
                    block_type = block.type
                    if block_type == "text" and hasattr(block, "text"):
                        text = block.text[:log_content_truncate_length] + "..." if len(block.text) > log_content_truncate_length else block.text
                        truncated_blocks.append({"type": block_type, "text": text})
                    elif block_type == "tool_use":
                        name = block.name if hasattr(block, "name") else ""
                        input_obj = block.input if hasattr(block, "input") else {}
                        input_str = str(input_obj)[:log_content_truncate_length] + "..." if len(str(input_obj)) > log_content_truncate_length else str(input_obj)
                        truncated_blocks.append({"type": block_type, "name": name, "input": input_str})
                    else:
                        truncated_blocks.append({"type": block_type})
                elif isinstance(block, dict):
                    block_type = block.get("type", "unknown")
                    if block_type == "text":
                        text = block.get("text", "")
                        text = text[:log_content_truncate_length] + "..." if len(text) > log_content_truncate_length else text
                        truncated_blocks.append({"type": block_type, "text": text})
                    elif block_type == "tool_result":
                        result = str(block.get("content", ""))
                        result = result[:log_content_truncate_length] + "..." if len(result) > log_content_truncate_length else result
                        tool_use_id = block.get("tool_use_id", "")
                        # 通过 tool_use_id 查找工具名称
                        tool_name = tool_id_to_name.get(tool_use_id, "unknown")
                        truncated_blocks.append({
                            "type": block_type,
                            "tool_use_id": tool_use_id[:log_content_truncate_length],
                            "tool_name": tool_name,
                            "content": result
                        })
                    else:
                        truncated_blocks.append({"type": block_type})

            truncated.append({"role": role, "content": truncated_blocks})
        else:
            # 其他类型，保留原样
            truncated.append({"role": role, "content": content})

    return truncated
