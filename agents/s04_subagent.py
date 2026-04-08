
#!/usr/bin/env python3
"""
s04_subagent.py - Subagents

Spawn a child agent with fresh messages=[]. The child works in its own
context, sharing the filesystem, then returns only a summary to the parent.

    Parent agent                     Subagent
    +------------------+             +------------------+
    | messages=[...]   |             | messages=[]      |  <-- fresh
    |                  |  dispatch   |                  |
    | tool: task       | ---------->| while tool_use:  |
    |   prompt="..."   |            |   call tools     |
    |   description="" |            |   append results |
    |                  |  summary   |                  |
    |   result = "..." | <--------- | return last text |
    +------------------+             +------------------+
              |
    Parent context stays clean.
    Subagent context is discarded.

Key insight: "Process isolation gives context isolation for free."
"""

import os
import subprocess
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from agent_logger import create_agent_logger, Events
from agent_utils import log_model_output, truncate_messages_for_log
import logging

load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

# 设置基础日志（只针对自己的 logger）
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("s04_agent")
logger.setLevel(logging.INFO)

SYSTEM = f"You are a coding agent at {WORKDIR}. Use the task tool to delegate exploration or subtasks."

# 子Agent只会返回总结的内容给主Agent。 子messages会被丢弃。
SUBAGENT_SYSTEM = f"You are a coding subagent at {WORKDIR}. Complete the given task, then summarize your findings."


# -- Tool implementations shared by parent and child --
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
                           capture_output=True, text=True, timeout=120)
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
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

# Child gets all base tools except task (no recursive spawning)
CHILD_TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
]


# -- Subagent: fresh context, filtered tools, summary-only return --
def run_subagent(prompt: str, trace_logger=None, parent_step: int = 0) -> str:
    """运行子代理，完成指定任务并返回摘要

    Args:
        prompt: 任务描述
        trace_logger: 轨迹日志记录器
        parent_step: 父代理的当前步骤数
    """
    sub_messages = [{"role": "user", "content": prompt}]  # fresh context

    # 记录子代理启动
    if trace_logger:
        trace_logger.log_event(Events.SUBAGENT_STARTED, {
            "prompt": prompt,
            "parent_step": parent_step,
        })

    for sub_step in range(30):  # safety limit
        response = client.messages.create(
            model=MODEL, system=SUBAGENT_SYSTEM, messages=sub_messages,
            tools=CHILD_TOOLS, max_tokens=8000,
        )

        # 记录子代理的模型输出
        if trace_logger:
            tool_calls = []
            for block in response.content:
                if hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id if hasattr(block, "id") else "",
                        "name": block.name if hasattr(block, "name") else "",
                        "input": block.input if hasattr(block, "input") else {},
                    })

            content_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    content_text += block.text

            trace_logger.log_event(Events.SUBAGENT_MODEL_OUTPUT, {
                "step": sub_step + 1,
                "content": content_text,
                "usage": {
                    "prompt_tokens": response.usage.input_tokens if hasattr(response, "usage") else 0,
                    "completion_tokens": response.usage.output_tokens if hasattr(response, "usage") else 0,
                    "total_tokens": (response.usage.input_tokens + response.usage.output_tokens) if hasattr(response, "usage") else 0,
                },
                "tool_calls": tool_calls,
                "stop_reason": response.stop_reason,
            }, step=parent_step)

        sub_messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)

                # 记录工具调用
                if trace_logger:
                    trace_logger.log_event(Events.SUBAGENT_TOOL_CALL, {
                        "step": sub_step + 1,
                        "tool": block.name,
                        "args": block.input,
                    }, step=parent_step)

                try:
                    output = handler(**block.input) if handler else f"Unknown tool: {block.name}"

                    # 记录工具结果
                    if trace_logger:
                        trace_logger.log_event(Events.SUBAGENT_TOOL_RESULT, {
                            "step": sub_step + 1,
                            "tool": block.name,
                            "result": str(output)[:50000],
                        }, step=parent_step)

                except Exception as e:
                    error_msg = f"Error executing {block.name}: {str(e)}"
                    logger.error(error_msg)

                    # 记录错误
                    if trace_logger:
                        trace_logger.log_event(Events.SUBAGENT_ERROR, {
                            "step": sub_step + 1,
                            "error": error_msg,
                            "tool": block.name,
                        }, step=parent_step)

                    output = error_msg

                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)[:50000]})
        sub_messages.append({"role": "user", "content": results})

        # 刷新子代理日志
        if trace_logger:
            trace_logger.flush()

    # Only the final text returns to the parent -- child context is discarded
    summary = "".join(b.text for b in response.content if hasattr(b, "text")) or "(no summary)"

    # 记录子代理完成
    if trace_logger:
        trace_logger.log_event(Events.SUBAGENT_COMPLETED, {
            "parent_step": parent_step,
            "summary": summary[:1000],  # 限制摘要长度
        })

    return summary


# -- Parent tools: base tools + task dispatcher --
PARENT_TOOLS = CHILD_TOOLS + [
    {"name": "task", "description": "Spawn a subagent with fresh context. It shares the filesystem but not conversation history.",
     "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}, "description": {"type": "string", "description": "Short description of the task"}}, "required": ["prompt"]}},
]

def agent_loop(messages: list, trace_logger=None, step_counter={"count": 0}):
    """主代理循环

    Args:
        messages: 消息历史
        trace_logger: 轨迹日志记录器
        step_counter: 步骤计数器
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
                "tools": [{"name": t.get("name"), "description": t.get("description")} for t in PARENT_TOOLS],
            }, step=current_step)

        # 调用模型
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=PARENT_TOOLS, max_tokens=8000,
        )

        # 记录模型输出
        log_model_output(trace_logger, response, current_step)

        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                if block.name == "task":
                    desc = block.input.get("description", "subtask")
                    print(f"> task ({desc}): {block.input['prompt'][:80]}")

                    # 记录子代理调度
                    if trace_logger:
                        trace_logger.log_event(Events.SUBAGENT_DISPATCH, {
                            "description": desc,
                            "prompt": block.input["prompt"],
                        }, step=current_step)

                    output = run_subagent(block.input["prompt"], trace_logger=trace_logger, parent_step=current_step)
                else:
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
                        error_msg = f"Error executing {block.name}: {str(e)}"
                        logger.error(error_msg)

                        # 记录错误
                        if trace_logger:
                            trace_logger.log_event(Events.ERROR, {
                                "error": error_msg,
                                "tool": block.name,
                            }, step=current_step)

                        output = error_msg

                print(f"  {str(output)[:200]}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
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
                query = input("\033[36ms04 >> \033[0m")
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