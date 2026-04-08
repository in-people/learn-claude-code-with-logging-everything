#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
s02_tool_use.py - Tools

The agent loop from s01 didn't change. We just added tools to the array
and a dispatch map to route calls.

    +----------+      +-------+      +------------------+
    |   User   | ---> |  LLM  | ---> | Tool Dispatch    |
    |  prompt  |      |       |      | {                |
    +----------+      +---+---+      |   bash: run_bash |
                          ^          |   read: run_read |
                          |          |   write: run_wr  |
                          +----------+   edit: run_edit |
                          tool_result| }                |
                                     +------------------+

Key insight: "The loop didn't change at all. I just added tools."
"""

import logging
import os
import subprocess
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from agent_logger import create_agent_logger, Events
from agent_utils import log_model_output, truncate_messages_for_log

load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("s02_agent")
logger.setLevel(logging.INFO)

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks. Act, don't explain."


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()  # 拼接为绝对路径
    if not path.is_relative_to(WORKDIR):  # 判断path是否为 WORKDIR的子路径
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int = None) -> str:
    try:
        text = safe_path(path).read_text(encoding='utf-8')
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding='utf-8')
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text(encoding='utf-8')
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1), encoding='utf-8') # 替换文本并写回文件 1表示只替换第一个匹配项
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

def personal_info(id: str) -> str:
    """根据ID返回个人信息"""

    user_database = {
        "1001": {
            "name": "liulin",
            "age": 28,
            "department": "研发部",
            "position": "高级工程师",
            "email": "liulin@example.com",
            "phone": "13800138001"
        },
    }

    user_info = user_database.get('1001')
    if not user_info:
        return f"错误：未找到ID为 {id} 的用户信息"

    info_text = f"""
个人信息查询结果：
────────────────────
用户ID: {id}
姓名: {user_info['name']}
年龄: {user_info['age']}
部门: {user_info['department']}
职位: {user_info['position']}
邮箱: {user_info['email']}
电话: {user_info['phone']}
────────────────────
"""
    return info_text.strip()


# -- The dispatch map: {tool_name: handler} --
# 键: 工具名称
# 值: lambda函数，接收关键字参数并调用具体的执行函数

# 当调用bash工具时
# 从kwargs中提取command参数
# 调用runbash(command)执行shell命令

#   使用示例：

#   # 假设 LLM 调用了工具：
#   tool_call = {
#       "name": "bash",
#       "input": {"command": "ls -la"}
#   }

#   # 执行方式 1：直接调用
#   handler = TOOL_HANDLERS[tool_call["name"]]
#   result = handler(**tool_call["input"])
#   # 等同于: run_bash("ls -la")

#   # 执行方式 2：动态调用
#   result = TOOL_HANDLERS["read_file"](path="test.txt", limit=100)
#   # 等同于: run_read("test.txt", 100)

# -- The dispatch map: {tool_name: handler} --
# 键: 工具名称
# 值: lambda函数，接收关键字参数并调用具体的执行函数
# 映射表 将工具名称映射到具体的执行函数

# lambda **kw 是什么意思？

# lambda **kw: ... 表示创建一个匿名函数，该函数接收任意数量的关键字参数：
#   这个 lambda 等价于：
#   def temp(**kw):
#       return run_bash(kw["command"])

# 策略模式（Strategy Pattern）是一种行为设计模式，核心思想是：定义一系列算法（策略），把它们封装起来，并且使它们可以相互替换。
# 问题：没有策略模式时，用 if-else 处理不同工具   
# 解决：策略模式  用字典映射策略
# 添加新工具只需添加一行
TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "personal_infos": lambda **kw: personal_info(kw["id"]),
}

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "personal_infos", "description": "Query personal information by user ID.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
]


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

        if trace_logger:
            trace_logger.log_event(Events.MODEL_CALL, {
                "system": SYSTEM,
                "messages": truncate_messages_for_log(messages),
                "tools": [{"name": t.get("name"), "description": t.get("description")} for t in TOOLS],
            }, step=current_step)


        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )

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
                tool_name = block.name
                handler = TOOL_HANDLERS.get(tool_name)

                if trace_logger:
                    trace_logger.log_event(Events.TOOL_CALL, {
                        "tool": tool_name,
                        "args": block.input,
                    }, step=current_step)


                try:
                    output = handler(**block.input) if handler else f"Unknown tool: {tool_name}"
                    print(f"> {tool_name}: {output[:200]}")

                    if trace_logger:
                        trace_logger.log_event(Events.TOOL_RESULT, {
                            "tool": tool_name,
                            "result": output[:50000],
                        }, step=current_step)

                except Exception as e:
                    error_msg = f"Error executing {tool_name}: {str(e)}"
                    logger.error(error_msg)

                    if trace_logger:
                        trace_logger.log_event(Events.ERROR, {
                            "error": error_msg,
                            "tool": tool_name,
                        }, step=current_step)

                    output = error_msg

                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

        messages.append({"role": "user", "content": results})
        if trace_logger:
            trace_logger.flush()


if __name__ == "__main__":
    trace_logger = create_agent_logger(
        trace_dir="logs/traces",
        enabled=True,
    )
    step_counter={"count": 0}
    logger.info(f"开始会话: {trace_logger.session_id}")

    history = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        trace_logger.log_event(Events.USER_INPUT, {"text": query})

        history.append({"role": "user", "content": query})
        agent_loop(history, trace_logger=trace_logger, step_counter=step_counter)

        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        print()

    # 结束会话，保存日志
    trace_logger.finalize()
    logger.info("会话结束")