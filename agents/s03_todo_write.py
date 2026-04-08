#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
s03_todo_write.py - TodoWrite

The model tracks its own progress via a TodoManager. A nag reminder
forces it to keep updating when it forgets.

    +----------+      +-------+      +---------+
    |   User   | ---> |  LLM  | ---> | Tools   |
    |  prompt  |      |       |      | + todo  |
    +----------+      +---+---+      +----+----+
                          ^               |
                          |   tool_result |
                          +---------------+
                                |
                    +-----------+-----------+
                    | TodoManager state     |
                    | [ ] task A            |
                    | [>] task B <- doing   |
                    | [x] task C            |
                    +-----------------------+
                                |
                    if rounds_since_todo >= 3:
                      inject <reminder>

Key insight: "The agent can track its own progress -- and I can see it."
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

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]


# 2026-03-31 07:31:15,458 - httpx - INFO - HTTP Request: POST https://open.bigmodel.cn/api/anthropic/v1/messages "HTTP/1.1 200 OK"
# 有上述日志打印
# 这是因为 logging.basicConfig(level=logging.INFO) 会捕获所有模块的 INFO 级别日志，包括httpx（Anthropic SDK 底层使用的 HTTP 客户端）的日志。

# 把基础日志级别修改为 level=logging.WARNING 
# logger.setLevel(logging.INFO) 只让自己的logger输出INFO
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("s03_agent")
logger.setLevel(logging.INFO)

SYSTEM = f"""You are a coding agent at {WORKDIR}.
Use the todo tool to plan multi-step tasks. Mark in_progress before starting, completed when done.
Prefer tools over prose."""

# 一个疑惑
# 又来一个新任务。创建了一个任务列表。 上一次的任务列表哪里去了？

# -- TodoManager: structured state the LLM writes to --
# 管理和跟踪AI代理的任务进度

# 每个待办事项包含三个字段
# id: 任务的唯一标识符
# text: 任务描述文本
# status：任务状态 pending、ip_process、completed
# 唯一进行中任务：同时只能有一个任务状态为 in_process


# TODO.update([
#     {"id": "1", "text": "读取文件", "status": "completed"},
#     {"id": "2", "text": "修改代码", "status": "in_progress"},
#     {"id": "3", "text": "运行测试", "status": "pending"}
# ])


# #### `render() -> str`
# 将任务列表渲染成可读的文本格式：

# ```
# [x] #1: 读取文件
# [>] #2: 修改代码
# [ ] #3: 运行测试


# 第一次
# TODO.update([
#     {"id": "1", "text": "读取文件", "status": "completed"},
#     {"id": "2", "text": "修改代码", "status": "in_progress"},
#     {"id": "3", "text": "运行测试", "status": "pending"}
# ])

# 第二次
# TODO.update([
#     {"id": "1", "text": "读取文件", "status": "completed"},
#     {"id": "2", "text": "修改代码", "status": "completed"},
#     {"id": "3", "text": "运行测试", "status": "in_progress"}
# ])

# 打印
# [x] #1: 读取文件
# [x] #2: 修改代码
# [>] #3: 运行测试
class TodoManager:
    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        if len(items) > 20:
            raise ValueError("Max 20 todos allowed")
        validated = [] # 每一次调用都创建一个空的任务列表
        in_progress_count = 0
        for i, item in enumerate(items):
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", "pending")).lower()
            item_id = str(item.get("id", str(i + 1)))
            if not text:
                raise ValueError(f"Item {item_id}: text required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {item_id}: invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"id": item_id, "text": text, "status": status})
        if in_progress_count > 1:
            raise ValueError("Only one task can be in_progress at a time")
        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."
        lines = []
        for item in self.items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[item["status"]]
            lines.append(f"{marker} #{item['id']}: {item['text']}")
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


TODO = TodoManager()


# -- Tool implementations --
def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=120,
                           encoding='utf-8', errors='replace')
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text(encoding='utf-8').splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding='utf-8')
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text(encoding='utf-8')
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1), encoding='utf-8')
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo":       lambda **kw: TODO.update(kw["items"]),
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
    {"name": "todo", "description": "Update task list. Track progress on multi-step tasks.",
     "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}}, "required": ["id", "text", "status"]}}}, "required": ["items"]}},
]


# -- Agent loop with nag reminder injection --
def agent_loop(messages: list, trace_logger=None, step_counter={"count": 0}):
    """主代理循环

    Args:
        messages: 消息历史
        trace_logger: 轨迹日志记录器
        step_counter: 步骤计数器
    """
    step_counter["count"] = 0
    rounds_since_todo = 0
    todo_used_at_least_once = False
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

        # Nag reminder is injected below, alongside tool results
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )

        # 记录模型输出
        log_model_output(trace_logger, response, current_step)

        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        used_todo = False
        for block in response.content:
            if block.type == "tool_use":
                # 记录工具调用
                if trace_logger:
                    trace_logger.log_event(Events.TOOL_CALL, {
                        "tool": block.name,
                        "args": block.input,
                    }, step=current_step)

                handler = TOOL_HANDLERS.get(block.name)
                try:
                    output = handler(**block.input) if handler else f"Unknown tool: {block.name}"

                    # 记录工具结果
                    if trace_logger:
                        trace_logger.log_event(Events.TOOL_RESULT, {
                            "tool": block.name,
                            "result": str(output)[:50000],
                        }, step=current_step)

                except Exception as e:
                    error_msg = f"Error: {e}"
                    output = error_msg

                    # 记录错误
                    if trace_logger:
                        trace_logger.log_event(Events.ERROR, {
                            "error": error_msg,
                            "tool": block.name,
                        }, step=current_step)

                    logger.error(error_msg)

                print(f"> {block.name}: {str(output)[:200]}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
                if block.name == "todo":
                    used_todo = True
                    todo_used_at_least_once = True
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1

        # 记录nag提醒注入
        if rounds_since_todo >= 3 and todo_used_at_least_once:
            reminder_text = "<reminder>Update your todos.</reminder>"
            results.insert(0, {"type": "text", "text": reminder_text})

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
    step_counter = {"count": 0}
    logger.info(f"开始会话: {trace_logger.session_id}")

    history = []
    try:
        while True:
            try:
                query = input("\033[36ms03 >> \033[0m")
            except (EOFError, KeyboardInterrupt):
                break
            if query.strip().lower() in ("q", "exit", ""):
                break

            # 记录用户输入
            trace_logger.log_event(Events.USER_INPUT, {"text": query})

            history.append({"role": "user", "content": query})
            agent_loop(history, trace_logger=trace_logger, step_counter=step_counter)

            # 强制刷新日志到磁盘
            trace_logger.flush()

            # 打印最后的回复
            response_content = history[-1]["content"]
            if isinstance(response_content, list):
                for block in response_content:
                    if hasattr(block, "text"):
                        print(block.text)
            print()
    finally:
        # 结束会话，保存日志（确保资源释放）
        trace_logger.finalize()
        logger.info("会话结束")