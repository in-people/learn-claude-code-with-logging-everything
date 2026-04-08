#!/usr/bin/env python3
"""
s05_skill_loading.py - Skills

Two-layer skill injection that avoids bloating the system prompt:

    Layer 1 (cheap): skill names in system prompt (~100 tokens/skill)
    Layer 2 (on demand): full skill body in tool_result

    skills/
      pdf/
        SKILL.md          <-- frontmatter (name, description) + body
      code-review/
        SKILL.md

    System prompt:
    +--------------------------------------+
    | You are a coding agent.              |
    | Skills available:                    |
    |   - pdf: Process PDF files...        |  <-- Layer 1: metadata only
    |   - code-review: Review code...      |
    +--------------------------------------+

    When model calls load_skill("pdf"):
    +--------------------------------------+
    | tool_result:                         |
    | <skill>                              |
    |   Full PDF processing instructions   |  <-- Layer 2: full body
    |   Step 1: ...                        |
    |   Step 2: ...                        |
    | </skill>                             |
    +--------------------------------------+

Key insight: "Don't put everything in the system prompt. Load on demand."
"""

import os
import re
import subprocess
from pathlib import Path
import logging

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

from agent_logger import create_agent_logger, Events, LoggerConfig

load_dotenv(override=True)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
SKILLS_DIR = WORKDIR / "skills"

# 设置基础日志（只针对自己的 logger）
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("s05_agent")
logger.setLevel(logging.INFO)


# -- SkillLoader: scan skills/<name>/SKILL.md with YAML frontmatter --
class SkillLoader:
    def __init__(self, skills_dir: Path, trace_logger=None):
        self.skills_dir = skills_dir
        self.skills = {}
        self.trace_logger = trace_logger
        self._load_all()

    # 扫描并加载技能目录下的所有SKILL.md文件
    def _load_all(self):
        if not self.skills_dir.exists():
            logger.warning(f"Skills directory not found: {self.skills_dir}")
            return
        for f in sorted(self.skills_dir.rglob("SKILL.md")):  # rglob递归查找所有名为SKILLL.md的文件
            text = f.read_text()
            meta, body = self._parse_frontmatter(text)
            name = meta.get("name", f.parent.name)
            self.skills[name] = {"meta": meta, "body": body, "path": str(f)}
            logger.info(f"Loaded skill: {name} from {f}")

    # 解析技能的元数据、内容
    def _parse_frontmatter(self, text: str) -> tuple:
        """Parse YAML frontmatter between --- delimiters."""
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        try:
            # 把yaml文本解析为Python字典
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        return meta, match.group(2).strip()

    def get_descriptions(self) -> str:
        """Layer 1: short descriptions for the system prompt."""
        if not self.skills:
            return "(no skills available)"
        lines = []
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "No description")
            tags = skill["meta"].get("tags", "")
            line = f"  - {name}: {desc}"
            if tags:
                line += f" [{tags}]"
            lines.append(line)
        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        """Layer 2: full skill body returned in tool_result."""
        skill = self.skills.get(name)
        if not skill:
            error_msg = f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
            logger.warning(error_msg)
            # 记录技能加载失败
            # if self.trace_logger:
            #     self.trace_logger.log_event(Events.SKILL_LOAD_FAILED, {
            #         "skill_name": name,
            #         "error": error_msg,
            #         "available_skills": list(self.skills.keys()),
            #     })
            return error_msg

        # 记录技能加载成功
        # if self.trace_logger:
        #     self.trace_logger.log_event(Events.SKILL_LOADED, {
        #         "skill_name": name,
        #         "description": skill["meta"].get("description", ""),
        #         "path": skill["path"],
        #     })

        logger.info(f"Loaded skill content: {name}")
        return f"<skill name=\"{name}\">\n{skill['body']}\n</skill>"


# Layer 1: skill metadata injected into system prompt
def get_system_prompt(skill_loader: SkillLoader) -> str:
    """生成包含技能列表的系统提示"""
    return f"""You are a coding agent at {WORKDIR}.
Use load_skill to access specialized knowledge before tackling unfamiliar topics.

Skills available:
{skill_loader.get_descriptions()}"""

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
        lines = safe_path(path).read_text().splitlines()
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


# 工具处理器将在运行时创建，因为需要访问 skill_loader
def create_tool_handlers(skill_loader: SkillLoader) -> dict:
    """创建工具处理器字典"""
    return {
        "bash":       lambda **kw: run_bash(kw["command"]),
        "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
        "load_skill": lambda **kw: skill_loader.get_content(kw["name"]),
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
    {"name": "load_skill", "description": "Load specialized knowledge by name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "Skill name to load"}}, "required": ["name"]}},
]


def agent_loop(messages: list, trace_logger=None, step_counter={"count": 0}, tool_handlers=None):
    """主代理循环

    Args:
        messages: 消息历史
        trace_logger: 轨迹日志记录器
        step_counter: 步骤计数器
        tool_handlers: 工具处理器字典
    """
    step_counter["count"] = 0
    while True:
        step_counter["count"] += 1
        current_step = step_counter["count"]

        # 调用模型
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )

        # 记录模型输出
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

            trace_logger.log_event(Events.MODEL_OUTPUT, {
                "content": content_text,
                "usage": {
                    "prompt_tokens": response.usage.input_tokens if hasattr(response, "usage") else 0,
                    "completion_tokens": response.usage.output_tokens if hasattr(response, "usage") else 0,
                    "total_tokens": (response.usage.input_tokens + response.usage.output_tokens) if hasattr(response, "usage") else 0,
                },
                "tool_calls": tool_calls,
                "stop_reason": response.stop_reason,
            }, step=current_step)

        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                # 记录工具调用
                if trace_logger:
                    trace_logger.log_event(Events.TOOL_CALL, {
                        "tool": block.name,
                        "args": block.input,
                    }, step=current_step)

                handler = tool_handlers.get(block.name) if tool_handlers else None
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


if __name__ == "__main__":
    # 创建轨迹日志记录器
    trace_logger = create_agent_logger(
        trace_dir="logs/traces",
        enabled=True,
    )

    # 创建技能加载器并传入 trace_logger
    skill_loader = SkillLoader(SKILLS_DIR, trace_logger=trace_logger)

    # 设置系统提示
    global SYSTEM
    SYSTEM = get_system_prompt(skill_loader)

    print(f"SKILLS: {skill_loader.get_descriptions()}")

    # 创建工具处理器
    tool_handlers = create_tool_handlers(skill_loader)

    step_counter = {"count": 0}
    logger.info(f"开始会话: {trace_logger.session_id}")

    history = []
    try:
        while True:
            try:
                query = input("\033[36ms05 >> \033[0m")
            except (EOFError, KeyboardInterrupt):
                break
            if query.strip().lower() in ("q", "exit", ""):
                break

            # 记录用户输入
            trace_logger.log_event(Events.USER_INPUT, {"text": query})

            history.append({"role": "user", "content": query})
            agent_loop(history, trace_logger=trace_logger, step_counter=step_counter, tool_handlers=tool_handlers)

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