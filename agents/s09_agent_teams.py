#!/usr/bin/env python3
"""
s09_agent_teams.py - Agent Teams

Persistent named agents with file-based JSONL inboxes. Each teammate runs
its own agent loop in a separate thread. Communication via append-only inboxes.

    Subagent (s04):  spawn -> execute -> return summary -> destroyed
    Teammate (s09):  spawn -> work -> idle -> work -> ... -> shutdown

    .team/config.json                   .team/inbox/
    +----------------------------+      +------------------+
    | {"team_name": "default",   |      | alice.jsonl      |
    |  "members": [              |      | bob.jsonl        |
    |    {"name":"alice",        |      | lead.jsonl       |
    |     "role":"coder",        |      +------------------+
    |     "status":"idle"}       |
    |  ]}                        |      send_message("alice", "fix bug"):
    +----------------------------+        open("alice.jsonl", "a").write(msg)

                                        read_inbox("alice"):
    spawn_teammate("alice","coder",...)   msgs = [json.loads(l) for l in ...]
         |                                open("alice.jsonl", "w").close()
         v                                return msgs  # drain
    Thread: alice             Thread: bob
    +------------------+      +------------------+
    | agent_loop       |      | agent_loop       |
    | status: working  |      | status: idle     |
    | ... runs tools   |      | ... waits ...    |
    | status -> idle   |      |                  |
    +------------------+      +------------------+

    5 message types (all declared, not all handled here):
    +-------------------------+-----------------------------------+
    | message                 | Normal text message               |
    | broadcast               | Sent to all teammates             |
    | shutdown_request        | Request graceful shutdown (s10)   |
    | shutdown_response       | Approve/reject shutdown (s10)     |
    | plan_approval_response  | Approve/reject plan (s10)         |
    +-------------------------+-----------------------------------+

Key insight: "Teammates that can talk to each other."
"""

import json
import logging
import os
import subprocess
import threading
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
TEAM_DIR = WORKDIR / ".team"
INBOX_DIR = TEAM_DIR / "inbox"

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("s09_agent")
logger.setLevel(logging.INFO)

SYSTEM = f"You are a team lead at {WORKDIR}. Spawn teammates and communicate via inboxes."

VALID_MSG_TYPES = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
}


# -- MessageBus: JSONL inbox per teammate --
# 每一个团队成员，一个JSON信箱
class MessageBus:
    def __init__(self, inbox_dir: Path):
        self.dir = inbox_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, sender: str, to: str, content: str,
             msg_type: str = "message", extra: dict = None) -> str:
        if msg_type not in VALID_MSG_TYPES:
            return f"Error: Invalid type '{msg_type}'. Valid: {VALID_MSG_TYPES}"
        msg = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            msg.update(extra)
        inbox_path = self.dir / f"{to}.jsonl"
        with open(inbox_path, "a", encoding='utf-8') as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n") # 将msg序列化为字符串；并写入到文件
        return f"Sent {msg_type} to {to}"

    def read_inbox(self, name: str) -> list:
        inbox_path = self.dir / f"{name}.jsonl"
        if not inbox_path.exists():
            return []
        messages = []
        for line in inbox_path.read_text(encoding='utf-8').strip().splitlines():
            if line:
                messages.append(json.loads(line))
        inbox_path.write_text("", encoding='utf-8')
        return messages

    def broadcast(self, sender: str, content: str, teammates: list) -> str:
        count = 0
        for name in teammates:
            if name != sender:
                self.send(sender, name, content, "broadcast")
                count += 1
        return f"Broadcast to {count} teammates"

# 轻量级消息队列系统
BUS = MessageBus(INBOX_DIR)


# -- TeammateManager: persistent named agents with config.json --
class TeammateManager:
    def __init__(self, team_dir: Path):
        self.dir = team_dir
        self.dir.mkdir(exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.config = self._load_config()
        self.threads = {}
        self.trace_loggers = {}  # 每个成员固定一个 trace_logger

    def _load_config(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text(encoding='utf-8'))
        return {"team_name": "default", "members": []}

    def _save_config(self):
        self.config_path.write_text(json.dumps(self.config, indent=2, ensure_ascii=False), encoding='utf-8')

    def _find_member(self, name: str) -> dict:
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    def spawn(self, name: str, role: str, prompt: str) -> str:
        member = self._find_member(name)

        if member:
            if member["status"] not in ("idle", "shutdown"):
                return f"Error: '{name}' is currently {member['status']}"
            member["status"] = "working"
            member["role"] = role
        else:
            member = {"name": name, "role": role, "status": "working"} # 一个成员加入
            self.config["members"].append(member)
        self._save_config()

        # 为新成员创建 trace_logger，已存在成员复用
        if name not in self.trace_loggers:
            self.trace_loggers[name] = create_agent_logger(
                trace_dir="logs/traces/teammates",
                enabled=True,
            )

        thread = threading.Thread(
            target=self._teammate_loop, # 启动一个agent
            args=(name, role, prompt, self.trace_loggers[name]),
            daemon=True,
        )
        self.threads[name] = thread
        thread.start()
        return f"Spawned '{name}' (role: {role})"

    def _teammate_loop(self, name: str, role: str, prompt: str, teammate_trace_logger):
        step_counter = {"count": 0}

        sys_prompt = (
            f"You are '{name}', role: {role}, at {WORKDIR}. "
            f"Use send_message to communicate. Complete your task."
        )
        messages = [{"role": "user", "content": prompt}]
        tools = self._teammate_tools()

        teammate_trace_logger.log_event(Events.TEAMMATE_INPUT, {"text": f"任务: {prompt}"})

        for _ in range(50):
            step_counter["count"] += 1
            current_step = step_counter["count"]

            if teammate_trace_logger:
                teammate_trace_logger.log_event(Events.MODEL_CALL, {
                    "system": sys_prompt,
                    "messages": truncate_messages_for_log(messages),
                    "tools": [{"name": t.get("name"), "description": t.get("description")} for t in tools],
                }, step=current_step)

            inbox = BUS.read_inbox(name) # 从自己的邮箱读信息
            for msg in inbox:
                messages.append({"role": "user", "content": json.dumps(msg)})
            try:
                response = client.messages.create(
                    model=MODEL,
                    system=sys_prompt,
                    messages=messages,
                    tools=tools,
                    max_tokens=8000,
                )
            except Exception as e:
                break

            log_model_output(teammate_trace_logger, response, current_step)

            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                break
            results = []
            for block in response.content:
                if block.type == "tool_use":

                    if teammate_trace_logger:
                        teammate_trace_logger.log_event(Events.TOOL_CALL, {
                            "tool": block.name,
                            "args": block.input,
                        }, step=current_step)

                    try:
                        output = self._exec(name, block.name, block.input)

                        if teammate_trace_logger:
                            teammate_trace_logger.log_event(Events.TOOL_RESULT, {
                                "tool": block.name,
                                "result": str(output)[:50000],
                            }, step=current_step)

                    except Exception as e:
                        error_msg = f"Error executing {block.name}: {str(e)}"

                        if teammate_trace_logger:
                            teammate_trace_logger.log_event(Events.ERROR, {
                                "error": error_msg,
                                "tool": block.name,
                            }, step=current_step)

                        output = error_msg

                    print(f"  [{name}] {block.name}: {str(output)[:120]}")
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    })
            messages.append({"role": "user", "content": results})
            teammate_trace_logger.flush()

        # 任务完成，但不 finalize trace_logger（因为会复用）
        member = self._find_member(name)
        if member and member["status"] != "shutdown":
            member["status"] = "idle"
            self._save_config()

    def _exec(self, sender: str, tool_name: str, args: dict) -> str:
        # these base tools are unchanged from s02
        if tool_name == "bash":
            return _run_bash(args["command"])
        if tool_name == "read_file":
            return _run_read(args["path"])
        if tool_name == "write_file":
            return _run_write(args["path"], args["content"])
        if tool_name == "edit_file":
            return _run_edit(args["path"], args["old_text"], args["new_text"])
        if tool_name == "send_message":
            return BUS.send(sender, args["to"], args["content"], args.get("msg_type", "message"))
        if tool_name == "read_inbox":
            return json.dumps(BUS.read_inbox(sender), indent=2)
        return f"Unknown tool: {tool_name}"

    def _teammate_tools(self) -> list:
        # these base tools are unchanged from s02
        return [
            {"name": "bash", "description": "Run a shell command.",
             "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
            {"name": "read_file", "description": "Read file contents.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
            {"name": "write_file", "description": "Write content to file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Replace exact text in file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
            {"name": "send_message", "description": "Send message to a teammate.",
             "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)}}, "required": ["to", "content"]}},
            {"name": "read_inbox", "description": "Read and drain your inbox.",
             "input_schema": {"type": "object", "properties": {}}},
        ]

    def list_all(self) -> str:
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    def member_names(self) -> list:
        return [m["name"] for m in self.config["members"]]

    def cleanup(self):
        """清理所有 trace_loggers"""
        for trace_logger in self.trace_loggers.values():
            trace_logger.finalize()
        self.trace_loggers.clear()


TEAM = TeammateManager(TEAM_DIR)


# -- Base tool implementations (these base tools are unchanged from s02) --
def _safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def _run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(
            command, shell=True, cwd=WORKDIR,
            capture_output=True, text=True, timeout=120,
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def _run_read(path: str, limit: int = None) -> str:
    try:
        lines = _safe_path(path).read_text(encoding='utf-8').splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def _run_write(path: str, content: str) -> str:
    try:
        fp = _safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding='utf-8')
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"


def _run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = _safe_path(path)
        c = fp.read_text(encoding='utf-8')
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1), encoding='utf-8')
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# -- Lead tool dispatch (9 tools) --
# 多了两个工具

# 团队协作工具
# spawn_teammate 产生一个新队员
# list_teammates 列出所有新队员

# 消息通信工具
# send_message 发送消息
# read_inbox 固定从lead邮箱读取信息
# broadcast 想所有团队成员广播信息

TOOL_HANDLERS = {
    "bash":            lambda **kw: _run_bash(kw["command"]),
    "read_file":       lambda **kw: _run_read(kw["path"], kw.get("limit")),
    "write_file":      lambda **kw: _run_write(kw["path"], kw["content"]),
    "edit_file":       lambda **kw: _run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "spawn_teammate":  lambda **kw: TEAM.spawn(kw["name"], kw["role"], kw["prompt"]),
    "list_teammates":  lambda **kw: TEAM.list_all(),
    "send_message":    lambda **kw: BUS.send("lead", kw["to"], kw["content"], kw.get("msg_type", "message")),
    "read_inbox":      lambda **kw: json.dumps(BUS.read_inbox("lead"), indent=2),
    "broadcast":       lambda **kw: BUS.broadcast("lead", kw["content"], TEAM.member_names()),
}

# these base tools are unchanged from s02
TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "spawn_teammate", "description": "Spawn a persistent teammate that runs in its own thread.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["name", "role", "prompt"]}},
    {"name": "list_teammates", "description": "List all teammates with name, role, status.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_message", "description": "Send a message to a teammate's inbox.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)}}, "required": ["to", "content"]}},
    {"name": "read_inbox", "description": "Read and drain the lead's inbox.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "broadcast", "description": "Send a message to all teammates.",
     "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
]


def agent_loop(messages: list, step_counter: dict = None, trace_logger = None):
    while True:
        step_counter["count"] += 1
        current_step = step_counter["count"]

        if trace_logger:
            trace_logger.log_event(Events.MODEL_CALL, {
                "system": SYSTEM,
                "messages": truncate_messages_for_log(messages),
                "tools": [{"name": t.get("name"), "description": t.get("description")} for t in TOOLS],
            }, step=current_step)

        inbox = BUS.read_inbox("lead")
        if inbox:
            messages.append({
                "role": "user",
                "content": f"<inbox>{json.dumps(inbox, indent=2)}</inbox>",
            })
            messages.append({
                "role": "assistant",
                "content": "Noted inbox messages.",
            })
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )

        log_model_output(trace_logger, response, current_step)

        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)

                if trace_logger:
                    trace_logger.log_event(Events.TOOL_CALL, {
                        "tool": block.name,
                        "args": block.input,
                    }, step=current_step)

                try:
                    output = handler(**block.input) if handler else f"Unknown tool: {block.name}"

                    if trace_logger:
                        trace_logger.log_event(Events.TOOL_RESULT, {
                            "tool": block.name,
                            "result": str(output)[:50000],
                        }, step=current_step)

                except Exception as e:
                    error_msg = f"Error executing {block.name}: {str(e)}"
                    logger.error(error_msg)

                    if trace_logger:
                        trace_logger.log_event(Events.ERROR, {
                            "error": error_msg,
                            "tool": block.name,
                        }, step=current_step)

                    output = error_msg

                print(f"> {block.name}: {str(output)[:200]}")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(output),
                })
        messages.append({"role": "user", "content": results})


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
                query = input("\033[36ms09 >> \033[0m")
            except (EOFError, KeyboardInterrupt):
                break
            if query.strip().lower() in ("q", "exit", ""):
                break
            if query.strip() == "/team":
                print(TEAM.list_all())
                continue
            if query.strip() == "/inbox":
                print(json.dumps(BUS.read_inbox("lead"), indent=2))
                continue

            trace_logger.log_event(Events.USER_INPUT, {"text": query})

            history.append({"role": "user", "content": query})
            agent_loop(history, step_counter, trace_logger)

            trace_logger.flush()

            response_content = history[-1]["content"]
            if isinstance(response_content, list):
                for block in response_content:
                    if hasattr(block, "text"):
                        print(block.text)
            print()
    finally:
        trace_logger.finalize()
        TEAM.cleanup()  # 清理所有队友的 trace_loggers
        logger.info("会话结束")