#!/usr/bin/env python3
"""
s06_context_compact.py - Compact

Three-layer compression pipeline so the agent can work forever:

    Every turn:
    +------------------+
    | Tool call result |
    +------------------+
            |
            v
    [Layer 1: micro_compact]        (silent, every turn)
      Replace tool_result content older than last 3
      with "[Previous: used {tool_name}]"
            |
            v
    [Check: tokens > 50000?]
       |               |
       no              yes
       |               |
       v               v
    continue    [Layer 2: auto_compact]
                  Save full transcript to .transcripts/
                  Ask LLM to summarize conversation.
                  Replace all messages with [summary].
                        |
                        v
                [Layer 3: compact tool]
                  Model calls compact -> immediate summarization.
                  Same as auto, triggered manually.

Key insight: "The agent can forget strategically and keep working forever."
"""

import json
import logging
import os
import subprocess
import time
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

# 设置基础日志（只针对自己的 logger）
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("s06_agent")
logger.setLevel(logging.INFO)

SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks."

THRESHOLD = 50000
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
KEEP_RECENT = 3
LOG_CONTENT_TRUNCATE_LENGTH = 400  # 日志中消息内容截断长度


def estimate_tokens(messages: list) -> int:
    """Rough token count: ~4 chars per token."""
    return len(str(messages)) // 4

# -- Layer 1: micro_compact - replace old tool results with placeholders --
def micro_compact(messages: list, trace_logger=None, current_step: int = 0) -> list:
    # Collect (msg_index, part_index, tool_result_dict) for all tool_result entries
    tool_results = []
    for msg_idx, msg in enumerate(messages):
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part_idx, part in enumerate(msg["content"]):
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tool_results.append((msg_idx, part_idx, part))
    if len(tool_results) <= KEEP_RECENT:
        return messages
    # Find tool_name for each result by matching tool_use_id in prior assistant messages
    tool_name_map = {}
    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_name_map[block.id] = block.name
    # Clear old results (keep last KEEP_RECENT)
    to_clear = tool_results[:-KEEP_RECENT]
    compacted_tools = []
    for _, _, result in to_clear:
        if isinstance(result.get("content"), str) and len(result["content"]) > 100:
            tool_id = result.get("tool_use_id", "")
            tool_name = tool_name_map.get(tool_id, "unknown")
            result["content"] = f"[Previous: used {tool_name}]" # 修改嵌套字典的引用，会影响原列表
            compacted_tools.append(tool_name)

    # 记录微压缩事件
    if trace_logger and compacted_tools:
        trace_logger.log_event(Events.MICRO_COMPACT, {
            "compacted_count": len(compacted_tools),
            "tools": compacted_tools,
        }, step=current_step)

    return messages

# -- Layer 2: auto_compact - save transcript, summarize, replace messages --
# 调用LLM压缩messages,总结已完成的任务、当前状态和关键决策
def auto_compact(messages: list, trace_logger=None, current_step: int = 0, trigger: str = "auto") -> list:
    # Save full transcript to disk
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(transcript_path, "w", encoding='utf-8') as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n") # 把messages写入json文件
    print(f"[transcript saved: {transcript_path}]")

    # 记录压缩开始
    if trace_logger:
        trace_logger.log_event(Events.COMPACT_STARTED, {
            "trigger": trigger,
            "transcript_path": str(transcript_path),
            "messages_count": len(messages),
        }, step=current_step)

    # Ask LLM to summarize
    conversation_text = json.dumps(messages, default=str)[:80000]
    response = client.messages.create(  # 调用大模型总结
        model=MODEL,
        messages=[{"role": "user", "content":
            "Summarize this conversation for continuity. Include: "
            "1) What was accomplished, 2) Current state, 3) Key decisions made. "
            "Be concise but preserve critical details.\n\n" + conversation_text}],
        max_tokens=2000,
    )
    summary = response.content[0].text

    # 记录压缩完成
    if trace_logger:
        trace_logger.log_event(Events.COMPACT_COMPLETED, {
            "trigger": trigger,
            "summary": summary[:1000],  # 限制摘要长度
            "usage": {
                "prompt_tokens": response.usage.input_tokens if hasattr(response, "usage") else 0,
                "completion_tokens": response.usage.output_tokens if hasattr(response, "usage") else 0,
                "total_tokens": (response.usage.input_tokens + response.usage.output_tokens) if hasattr(response, "usage") else 0,
            },
        }, step=current_step)

    # Replace all messages with compressed summary
    return [
        {"role": "user", "content": f"[Conversation compressed. Transcript: {transcript_path}]\n\n{summary}"},
        {"role": "assistant", "content": "Understood. I have the context from the summary. Continuing."},
    ]


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
    "compact":    lambda **kw: "Manual compression requested.",
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
    {"name": "compact", "description": "Trigger manual conversation compression.",
     "input_schema": {"type": "object", "properties": {"focus": {"type": "string", "description": "What to preserve in the summary"}}}},
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

        # Layer 1: micro_compact before each LLM call  微压缩
        micro_compact(messages, trace_logger=trace_logger, current_step=current_step)

        # Layer 2: auto_compact if token estimate exceeds threshold
        if estimate_tokens(messages) > THRESHOLD:
            print("[auto_compact triggered]")
            messages[:] = auto_compact(messages, trace_logger=trace_logger, current_step=current_step, trigger="auto")

        # 记录模型调用（包含截断的 messages）
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

        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        manual_compact = False
        for block in response.content:
            if block.type == "tool_use":
                if block.name == "compact":
                    manual_compact = True
                    output = "Compressing..."

                    # 记录手动压缩触发
                    if trace_logger:
                        trace_logger.log_event(Events.COMPACT_MANUAL_TRIGGER, {
                            "focus": block.input.get("focus", ""),
                        }, step=current_step)
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

                print(f"> {block.name}: {str(output)[:200]}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
        messages.append({"role": "user", "content": results})
        # Layer 3: manual compact triggered by the compact tool
        if manual_compact:
            print("[manual compact]")
            messages[:] = auto_compact(messages, trace_logger=trace_logger, current_step=current_step, trigger="manual")  # 把工具执行结果添加到messages队列。执行压缩操作


if __name__ == "__main__":
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
                query = input("\033[36ms06 >> \033[0m")
            except (EOFError, KeyboardInterrupt):
                break
            if query.strip().lower() in ("q", "exit", ""):
                break
            trace_logger.log_event(Events.USER_INPUT, {"text": query})

            history.append({"role": "user", "content": query})
            agent_loop(history, trace_logger=trace_logger, step_counter=step_counter)

            # 强制刷新日志到磁盘
            trace_logger.flush()

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