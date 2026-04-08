#!/usr/bin/env python3
"""测试 Agent 日志系统"""

from agent_logger import create_agent_logger
import time


def test_logger():
    """测试日志记录器的基本功能"""

    # 创建日志记录器
    logger = create_agent_logger(
        trace_dir="logs/traces",
        enabled=True,
    )

    print(f"会话 ID: {logger.session_id}")
    print(f"日志目录: {logger.trace_dir}")

    # 模拟一些事件
    print("\n记录测试事件...")

    # 用户输入
    logger.log_event("user_input", {"text": "列出当前目录的文件"})

    # 模型输出
    logger.log_event("model_output", {
        "content": "我将使用 ls 命令列出文件",
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
        "tool_calls": [
            {
                "id": "call_123",
                "name": "bash",
                "input": {"command": "ls"},
            }
        ],
        "stop_reason": "tool_use",
    }, step=1)

    # 工具调用
    logger.log_event("tool_call", {
        "tool": "bash",
        "args": {"command": "ls"},
    }, step=1)

    # 工具结果
    logger.log_event("tool_result", {
        "tool": "bash",
        "result": "file1.py\nfile2.py\nfile3.py\n",
    }, step=1)

    # 第二轮
    logger.log_event("model_output", {
        "content": "找到了 3 个 Python 文件",
        "usage": {
            "prompt_tokens": 200,
            "completion_tokens": 30,
            "total_tokens": 230,
        },
        "tool_calls": [],
        "stop_reason": "end_turn",
    }, step=2)

    # 结束会话
    print("\n结束会话...")
    logger.finalize()

    print(f"\n✅ 测试完成！")
    print(f"JSONL 文件: {logger._jsonl_file}")
    print(f"HTML 文件: {logger._html_file}")

    # 显示统计信息
    print(f"\n统计信息:")
    print(f"  总步骤数: {logger._total_steps}")
    print(f"  工具调用次数: {logger._tools_used}")
    print(f"  总 token 数: {logger._total_tokens}")


if __name__ == "__main__":
    test_logger()
