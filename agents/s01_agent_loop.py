#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
s01_agent_loop.py - The Agent Loop

The entire secret of an AI coding agent in one pattern:

    while stop_reason == "tool_use":
        response = LLM(messages, tools)
        execute tools
        append results

    +----------+      +-------+      +---------+
    |   User   | ---> |  LLM  | ---> |  Tool   |
    |  prompt  |      |       |      | execute |
    +----------+      +---+---+      +----+----+
                          ^               |
                          |   tool_result |
                          +---------------+
                          (loop continues)

This is the core loop: feed tool results back to the model
until the model decides to stop. Production agents layer
policy, hooks, and lifecycle controls on top.
"""

import logging
import os
import subprocess

from anthropic import Anthropic
from dotenv import load_dotenv

from agent_logger import create_agent_logger, Events
from agent_utils import log_model_output, truncate_messages_for_log

load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("s01_agent")
logger.setLevel(logging.INFO)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]


SYSTEM = f"You are a coding agent at {os.getcwd()}. Use bash to solve tasks. Act, don't explain."

TOOLS = [{
    "name": "bash",
    "description": "Run a shell command.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}]


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=120,
                           encoding='utf-8', errors='replace')
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


# -- The core pattern: a while loop that calls tools until the model stops --
def agent_loop(messages: list, trace_logger=None, step_counter={"count": 0}):
    """Agent 主循环

    Args:
        messages: 消息历史
        trace_logger: 轨迹日志记录器
        step_counter: 步骤计数器（用于记录当前执行到第几步）
    """
    step_counter["count"] = 0
    while True:
        step_counter["count"] += 1
        current_step = step_counter["count"]

        # 记录模型调用
        if trace_logger:
            trace_logger.log_event(Events.MODEL_CALL, {
                "system": SYSTEM,
                "messages": truncate_messages_for_log(messages),
                "tools": [{"name": t.get("name"), "description": t.get("description")} for t in TOOLS],
            }, step=current_step)

        # 调用模型
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )

        # 记录模型输出
        log_model_output(trace_logger, response, current_step)

        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        # If the model didn't call a tool, we're done
        if response.stop_reason != "tool_use":
            return

        # Execute each tool call, collect results
        results = []
        for block in response.content:
            if block.type == "tool_use":
                command = block.input["command"]
                print(f"\033[33m$ {command}\033[0m")

                # 记录工具调用
                if trace_logger:
                    trace_logger.log_event(Events.TOOL_CALL, {
                        "tool": "bash",
                        "args": {"command": command},
                    }, step=current_step)

                # 执行命令
                try:
                    output = run_bash(command)
                    print("tool output: ", output[:200])

                    # 记录工具结果
                    if trace_logger:
                        trace_logger.log_event(Events.TOOL_RESULT, {
                            "tool": "bash",
                            "result": output[:50000],
                        }, step=current_step)

                except Exception as e:
                    error_msg = f"Error executing command: {str(e)}"
                    logger.error(error_msg)

                    # 记录错误
                    if trace_logger:
                        trace_logger.log_event(Events.ERROR, {
                            "error": error_msg,
                            "tool": "bash",
                        }, step=current_step)

                    output = error_msg

                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output})

        messages.append({"role": "user", "content": results})

        # 刷新日志
        if trace_logger:
            trace_logger.flush()


if __name__ == "__main__":
    # 创建轨迹日志记录器
    trace_logger = create_agent_logger(
        trace_dir="logs/traces",
        enabled=True,
    )
    step_counter={"count": 0}
    logger.info(f"开始会话: {trace_logger.session_id}")

    history = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        # 记录用户输入
        trace_logger.log_event(Events.USER_INPUT, {"text": query})

        history.append({"role": "user", "content": query})
        agent_loop(history, trace_logger=trace_logger, step_counter=step_counter)

        # 打印最后的回复
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        print()

    # 结束会话，保存日志
    trace_logger.finalize()
    logger.info("会话结束")
