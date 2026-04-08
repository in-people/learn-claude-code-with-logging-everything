
"""Agent Logger - 重构版

改进：
1. 分离关注点：文件写入、HTML渲染、统计追踪各司其职
2. 消除重复：使用渲染器注册模式，统一处理相似事件
3. 缩小依赖：HTML模板独立，时区可配置
4. 向稳定演进：支持上下文管理器，易于测试和扩展
"""

import html
import json
import logging
import threading
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol
from dataclasses import dataclass, field


# ============================================================================
# 配置和常量
# ============================================================================

@dataclass
class LoggerConfig:
    """日志配置"""
    trace_dir: Path = Path("logs/traces")
    enabled: bool = True
    timezone: timezone = timezone(timedelta(hours=8))
    truncation_short: int = 300
    truncation_medium: int = 600
    truncation_long: int = 800
    truncation_xlong: int = 1000
    truncation_model_content: int = 300  # 模型调用消息内容截断长度
    truncation_system: int = 200  # System prompt 截断长度
    truncation_tool_id: int = 10  # Tool use ID 前缀长度


# Event type constants
class Events:
    USER_INPUT = "user_input"
    MODEL_CALL = "model_call"
    MODEL_OUTPUT = "model_output"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SESSION_SUMMARY = "session_summary"
    ERROR = "error"
    SUBAGENT_DISPATCH = "subagent_dispatch"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_MODEL_OUTPUT = "subagent_model_output"
    SUBAGENT_TOOL_CALL = "subagent_tool_call"
    SUBAGENT_TOOL_RESULT = "subagent_tool_result"
    SUBAGENT_COMPLETED = "subagent_completed"
    SUBAGENT_ERROR = "subagent_error"
    MICRO_COMPACT = "micro_compact"
    COMPACT_STARTED = "compact_started"
    COMPACT_COMPLETED = "compact_completed"
    COMPACT_MANUAL_TRIGGER = "compact_manual_trigger"
    TEAMMATE_INPUT = "teammate_input"


# ============================================================================
# 文件写入器（关注点分离）
# ============================================================================

class FileWriter(ABC):
    """文件写入器抽象基类"""

    @abstractmethod
    def write(self, data: str) -> None:
        """写入数据"""

    @abstractmethod
    def close(self) -> None:
        """关闭文件"""

    @abstractmethod
    def flush(self) -> None:
        """刷新缓冲区"""


class JSONLWriter(FileWriter):
    """JSONL 文件写入器"""

    def __init__(self, path: Path):
        self._handle = open(path, "a", encoding="utf-8", newline="\n")

    def write(self, data: str) -> None:
        self._handle.write(data + "\n")

    def close(self) -> None:
        if self._handle:
            self._handle.close()
            self._handle = None

    def flush(self) -> None:
        if self._handle:
            self._handle.flush()


class HTMLWriter(FileWriter):
    """HTML 文件写入器"""

    def __init__(self, path: Path, renderer: 'HtmlRenderer'):
        self._handle = open(path, "w", encoding="utf-8")
        self._renderer = renderer
        self._write_header()

    def _write_header(self) -> None:
        self._handle.write(self._renderer.render_header())

    def write(self, data: str) -> None:
        self._handle.write(data)

    def close(self) -> None:
        if self._handle:
            self._handle.write(self._renderer.render_footer())
            self._handle.close()
            self._handle = None

    def flush(self) -> None:
        if self._handle:
            self._handle.flush()


# ============================================================================
# HTML 渲染器（关注点分离 + 可测试）
# ============================================================================

class HtmlRenderer:
    """HTML 渲染器 - 独立、可测试"""

    def __init__(self, config: LoggerConfig, session_id: str):
        self.config = config
        self.session_id = session_id
        self._renderers: Dict[str, callable] = self._init_renderers()

    def _init_renderers(self) -> Dict[str, callable]:
        """初始化事件渲染器映射"""
        return {
            Events.USER_INPUT: self._render_user_input,
            Events.MODEL_CALL: self._render_model_call,
            Events.MODEL_OUTPUT: self._render_model_output,
            Events.TOOL_CALL: self._render_tool_call,
            Events.TOOL_RESULT: self._render_tool_result,
            Events.SESSION_SUMMARY: self._render_session_summary,
            Events.ERROR: self._render_error,
            Events.SUBAGENT_DISPATCH: self._render_subagent_dispatch,
            Events.SUBAGENT_STARTED: self._render_subagent_started,
            Events.SUBAGENT_MODEL_OUTPUT: self._render_subagent_model_output,
            Events.SUBAGENT_TOOL_CALL: self._render_subagent_tool_call,
            Events.SUBAGENT_TOOL_RESULT: self._render_subagent_tool_result,
            Events.SUBAGENT_COMPLETED: self._render_subagent_completed,
            Events.SUBAGENT_ERROR: self._render_subagent_error,
            Events.MICRO_COMPACT: self._render_micro_compact,
            Events.COMPACT_STARTED: self._render_compact_started,
            Events.COMPACT_COMPLETED: self._render_compact_completed,
            Events.COMPACT_MANUAL_TRIGGER: self._render_compact_manual_trigger,
            Events.TEAMMATE_INPUT: self._render_teammate_input,
        }

    def render_header(self) -> str:
        """渲染 HTML 头部"""
        now = datetime.now(self.config.timezone).strftime("%Y-%m-%d %H:%M:%S 北京时间")
        title = f"Agent Trace: {self.session_id}"

        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 24px; color: #111; }}
    h1 {{ font-size: 20px; margin: 0 0 6px; }}
    h2 {{ font-size: 16px; margin: 18px 0 8px; }}
    h3 {{ font-size: 14px; margin: 12px 0 6px; }}
    .meta {{ color: #555; font-size: 12px; }}
    .block {{ border: 1px solid #e4e4e7; border-radius: 8px; padding: 10px 12px; margin: 8px 0; background: #fafafa; }}
    pre {{ background: #0f172a; color: #f8fafc; padding: 10px 12px; border-radius: 8px; overflow-x: auto; }}
    code {{ font-family: ui-monospace, monospace; font-size: 12px; }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="meta">Started: {html.escape(now)}</div>
  </header>
  <main>
    <section class="timeline">
"""

    def render_footer(self) -> str:
        """渲染 HTML 尾部"""
        return """    </section>
  </main>
</body>
</html>
"""

    def render_event(self, event_obj: Dict[str, Any]) -> str:
        """渲染单个事件"""
        event = event_obj.get("event")
        renderer = self._renderers.get(event)

        if renderer:
            return renderer(event_obj)
        return ""

    # ---------------------------------------------------------------------
    # 事件渲染方法（私有，消除重复）
    # ---------------------------------------------------------------------

    def _render_user_input(self, event_obj: Dict[str, Any]) -> str:
        text = event_obj["payload"].get("text", "")
        return f"<h2>🧑 User Input</h2>\n<pre><code>{self._escape(text)}</code></pre>"

    def _render_teammate_input(self, event_obj: Dict[str, Any]) -> str:
        text = event_obj["payload"].get("text", "")
        return f"<h2>👥 Teammate Task</h2>\n<pre><code>{self._escape(text)}</code></pre>"

    def _render_model_call(self, event_obj: Dict[str, Any]) -> str:
        """渲染模型调用（包含 messages）"""
        payload = event_obj["payload"]
        messages = payload.get("messages", [])
        system = payload.get("system", "")
        tools = payload.get("tools", [])

        lines = ["<h2>🔵 Model Call</h2>"]

        # System prompt
        if system:
            lines.append(f"<div class='meta'>System: {self._escape(self._truncate(system, self.config.truncation_system))}</div>")

        # Tools count
        if tools:
            lines.append(f"<div class='meta'>Tools: {len(tools)} tools defined</div>")

        # Messages
        lines.append(f"<div class='meta'>Messages: {len(messages)} messages</div>")
        lines.append("<pre><code>")

        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            lines.append(f"[{i+1}] {role}:")

            # 处理不同类型的 content
            if isinstance(content, str):
                # 如果是字符串，直接截断
                truncated = self._truncate(content, self.config.truncation_model_content)
                lines.append(f"  {self._escape(truncated)}")
            elif isinstance(content, list):
                # 如果是列表（包含 text 和 tool_use blocks）
                for block in content:
                    if hasattr(block, "type"):
                        block_type = block.type
                        if block_type == "text":
                            text = block.text if hasattr(block, "text") else ""
                            truncated = self._truncate(text, self.config.truncation_model_content)
                            lines.append(f"  [{block_type}] {self._escape(truncated)}")
                        elif block_type == "tool_use":
                            name = block.name if hasattr(block, "name") else ""
                            input_str = str(block.input) if hasattr(block, "input") else "{}"
                            truncated_input = self._truncate(input_str, self.config.truncation_model_content)
                            lines.append(f"  [{block_type}] {self._escape(name)}({self._escape(truncated_input)})")
                        else:
                            lines.append(f"  [{block_type}] ...")
                    elif isinstance(block, dict):
                        block_type = block.get("type", "unknown")
                        if block_type == "text":
                            text = block.get("text", "")
                            truncated = self._truncate(text, self.config.truncation_model_content)
                            lines.append(f"  [{block_type}] {self._escape(truncated)}")
                        elif block_type == "tool_use":
                            # 工具调用：显示名称和参数
                            name = block.get("name", "")
                            input_str = str(block.get("input", ""))
                            truncated_input = self._truncate(input_str, self.config.truncation_model_content)
                            lines.append(f"  [{block_type}] {self._escape(name)}({self._escape(truncated_input)})")
                        elif block_type == "tool_result":
                            tool_use_id = block.get("tool_use_id", "")[:self.config.truncation_tool_id]
                            tool_name = block.get("tool_name", "")
                            result = str(block.get("content", ""))
                            truncated = self._truncate(result, self.config.truncation_model_content)
                            if tool_name and tool_name != "unknown":
                                lines.append(f"  [{block_type}] {self._escape(tool_name)}(id={tool_use_id}...) => {self._escape(truncated)}")
                            else:
                                lines.append(f"  [{block_type}] id={tool_use_id}... => {self._escape(truncated)}")
                        else:
                            lines.append(f"  [{block_type}] ...")

            lines.append("")  # 空行分隔

        lines.append("</code></pre>")
        return "\n".join(lines)

    def _render_model_output(self, event_obj: Dict[str, Any]) -> str:
        return self._render_model_output_generic(
            "🧠 Model Output",
            event_obj["payload"]
        )

    def _render_subagent_model_output(self, event_obj: Dict[str, Any]) -> str:
        step = event_obj["payload"].get("step", 0)
        return self._render_model_output_generic(
            f"🧠 Subagent Step {step}",
            event_obj["payload"]
        )

    def _render_model_output_generic(self, title: str, payload: Dict) -> str:
        """通用模型输出渲染（消除重复）"""
        lines = [f"<h2>{title}</h2>"]

        usage = payload.get("usage", {})
        if usage:
            tokens_info = f"Tokens: {usage.get('prompt_tokens', 0)} → {usage.get('completion_tokens', 0)} = {usage.get('total_tokens', 0)}"
            lines.append(f"<div class='meta'>{self._escape(tokens_info)}</div>")

        tool_calls = payload.get("tool_calls", [])
        if tool_calls:
            lines.append("<div class='meta'>Tool calls:</div>")
            calls_text = json.dumps(tool_calls, ensure_ascii=False, indent=2)
            lines.append(f"<pre><code>{self._escape(self._truncate(calls_text, self.config.truncation_long))}</code></pre>")

        content = payload.get("content", "")
        if content:
            lines.append("<div class='meta'>Content:</div>")
            lines.append(f"<pre><code>{self._escape(self._truncate(content, self.config.truncation_medium))}</code></pre>")

        return "\n".join(lines)

    def _render_tool_call(self, event_obj: Dict[str, Any]) -> str:
        return self._render_tool_call_generic(
            "🛠️ Tool Call",
            event_obj["payload"],
            with_step=False
        )

    def _render_subagent_tool_call(self, event_obj: Dict[str, Any]) -> str:
        return self._render_tool_call_generic(
            "🛠️ Subagent Tool Call",
            event_obj["payload"],
            with_step=True
        )

    def _render_tool_call_generic(self, title: str, payload: Dict, with_step: bool) -> str:
        """通用工具调用渲染（消除重复）"""
        tool = payload.get("tool", "")
        args_text = json.dumps(payload.get("args", {}), ensure_ascii=False)

        if with_step:
            step = payload.get("step", 0)
            title = f"{title} (Step {step})"

        return f"""<h2>{title}</h2>
<div class='meta'>Tool: {self._escape(tool)}</div>
<pre><code>{self._escape(self._truncate(args_text, self.config.truncation_medium))}</code></pre>"""

    def _render_tool_result(self, event_obj: Dict[str, Any]) -> str:
        return self._render_tool_result_generic(
            "👁️ Tool Result",
            event_obj["payload"],
            with_step=False
        )

    def _render_subagent_tool_result(self, event_obj: Dict[str, Any]) -> str:
        return self._render_tool_result_generic(
            "👁️ Subagent Tool Result",
            event_obj["payload"],
            with_step=True
        )

    def _render_tool_result_generic(self, title: str, payload: Dict, with_step: bool) -> str:
        """通用工具结果渲染（消除重复）"""
        tool = payload.get("tool", "")
        result = payload.get("result", "")

        if with_step:
            step = payload.get("step", 0)
            title = f"{title} (Step {step})"

        return f"""<h2>{title}</h2>
<div class='meta'>Tool: {self._escape(tool)}</div>
<pre><code>{self._escape(self._truncate(result, self.config.truncation_xlong))}</code></pre>"""

    def _render_session_summary(self, event_obj: Dict[str, Any]) -> str:
        summary_text = json.dumps(event_obj["payload"], ensure_ascii=False, indent=2)
        return f"<h2>📊 Session Summary</h2>\n<pre><code>{self._escape(summary_text)}</code></pre>"

    def _render_error(self, event_obj: Dict[str, Any]) -> str:
        error_text = json.dumps(event_obj["payload"], ensure_ascii=False)
        return f"<h2>❌ Error</h2>\n<pre><code>{self._escape(error_text)}</code></pre>"

    def _render_subagent_dispatch(self, event_obj: Dict[str, Any]) -> str:
        payload = event_obj["payload"]
        desc = payload.get("description", "")
        prompt = payload.get("prompt", "")
        return f"""<h2>🚀 Subagent Dispatch</h2>
<div class='meta'>Task: {self._escape(desc)}</div>
<pre><code>{self._escape(self._truncate(prompt, self.config.truncation_medium))}</code></pre>"""

    def _render_subagent_started(self, event_obj: Dict[str, Any]) -> str:
        payload = event_obj["payload"]
        parent_step = payload.get("parent_step", 0)
        prompt = payload.get("prompt", "")
        return f"""<h2>🔵 Subagent Started</h2>
<div class='meta'>Parent Step: {parent_step}</div>
<pre><code>{self._escape(self._truncate(prompt, self.config.truncation_short))}</code></pre>"""

    def _render_subagent_completed(self, event_obj: Dict[str, Any]) -> str:
        payload = event_obj["payload"]
        parent_step = payload.get("parent_step", 0)
        summary = payload.get("summary", "")
        return f"""<h2>✅ Subagent Completed</h2>
<div class='meta'>Parent Step: {parent_step}</div>
<pre><code>{self._escape(self._truncate(summary, self.config.truncation_long))}</code></pre>"""

    def _render_subagent_error(self, event_obj: Dict[str, Any]) -> str:
        payload = event_obj["payload"]
        tool = payload.get("tool", "")
        error = payload.get("error", "")
        step = payload.get("step", 0)
        return f"""<h2>❌ Subagent Error (Step {step})</h2>
<div class='meta'>Tool: {self._escape(tool)}</div>
<pre><code>{self._escape(error)}</code></pre>"""

    def _render_micro_compact(self, event_obj: Dict[str, Any]) -> str:
        payload = event_obj["payload"]
        compacted_count = payload.get("compacted_count", 0)
        tools = payload.get("tools", [])
        tools_text = json.dumps(tools, ensure_ascii=False)
        return f"""<h2>🔄 Micro Compact</h2>
<div class='meta'>Compacted {compacted_count} tool results</div>
<pre><code>{self._escape(self._truncate(tools_text, self.config.truncation_short))}</code></pre>"""

    def _render_compact_started(self, event_obj: Dict[str, Any]) -> str:
        payload = event_obj["payload"]
        trigger = payload.get("trigger", "auto")
        transcript_path = payload.get("transcript_path", "")
        messages_count = payload.get("messages_count", 0)
        return f"""<h2>📦 Compact Started</h2>
<div class='meta'>Trigger: {self._escape(trigger)} | Messages: {messages_count}</div>
<div class='meta'>Transcript: {self._escape(transcript_path)}</div>"""

    def _render_compact_completed(self, event_obj: Dict[str, Any]) -> str:
        payload = event_obj["payload"]
        trigger = payload.get("trigger", "auto")
        summary = payload.get("summary", "")
        usage = payload.get("usage", {})
        tokens_info = f"Tokens: {usage.get('prompt_tokens', 0)} → {usage.get('completion_tokens', 0)} = {usage.get('total_tokens', 0)}"
        return f"""<h2>✅ Compact Completed</h2>
<div class='meta'>Trigger: {self._escape(trigger)}</div>
<div class='meta'>{self._escape(tokens_info)}</div>
<pre><code>{self._escape(self._truncate(summary, self.config.truncation_long))}</code></pre>"""

    def _render_compact_manual_trigger(self, event_obj: Dict[str, Any]) -> str:
        payload = event_obj["payload"]
        focus = payload.get("focus", "")
        return f"""<h2>🎯 Manual Compact Triggered</h2>
<div class='meta'>Focus: {self._escape(focus)}</div>"""

    # ---------------------------------------------------------------------
    # 工具方法
    # ---------------------------------------------------------------------

    def _truncate(self, text: str, limit: int) -> str:
        if text is None:
            return ""
        s = str(text)
        if len(s) <= limit:
            return s
        return s[:limit] + "...(truncated)"

    def _escape(self, text: str) -> str:
        return html.escape(text or "")


# ============================================================================
# 统计追踪器（关注点分离）
# ============================================================================

@dataclass
class SessionStats:
    """会话统计"""
    total_steps: int = 0
    tools_used: int = 0
    total_tokens: int = 0


class StatsTracker:
    """统计追踪器"""

    def __init__(self):
        self._stats = SessionStats()
        self._lock = threading.Lock()

    def update(self, event: str, payload: Dict[str, Any], step: int) -> None:
        """更新统计"""
        with self._lock:
            if step > self._stats.total_steps:
                self._stats.total_steps = step

            if event == Events.TOOL_CALL:
                self._stats.tools_used += 1

            if event == Events.MODEL_OUTPUT:
                usage = payload.get("usage")
                if usage:
                    self._stats.total_tokens += usage.get("total_tokens", 0)

    @property
    def stats(self) -> SessionStats:
        """获取统计副本"""
        with self._lock:
            return SessionStats(
                total_steps=self._stats.total_steps,
                tools_used=self._stats.tools_used,
                total_tokens=self._stats.total_tokens,
            )


# ============================================================================
# 主日志类（重构后）
# ============================================================================

class AgentLogger:
    """Agent 执行轨迹记录器（重构版）

    改进：
    - 使用上下文管理器确保资源释放
    - 分离关注点：文件、渲染、统计独立
    - 支持自定义配置
    - 使用日志系统而非 print

    使用方式：
        with AgentLogger(session_id, trace_dir) as logger:
            logger.log_event(Events.USER_INPUT, {"text": "hello"})
            logger.log_event(Events.MODEL_OUTPUT, {"content": "..."})
    """

    def __init__(
        self,
        session_id: str,
        config: Optional[LoggerConfig] = None,
    ):
        self.session_id = session_id
        self.config = config or LoggerConfig()
        self.enabled = self.config.enabled

        self._stats = StatsTracker()
        self._lock = threading.Lock()
        self._jsonl_writer: Optional[JSONLWriter] = None
        self._html_writer: Optional[HTMLWriter] = None
        self._renderer: Optional[HtmlRenderer] = None
        self._logger = logging.getLogger(__name__)

        if self.enabled:
            self._init_writers()

    def _init_writers(self) -> None:
        """初始化文件写入器"""
        self.config.trace_dir.mkdir(parents=True, exist_ok=True)

        jsonl_path = self.config.trace_dir / f"trace-{self.session_id}.jsonl"
        html_path = self.config.trace_dir / f"trace-{self.session_id}.html"

        try:
            self._renderer = HtmlRenderer(self.config, self.session_id)
            self._jsonl_writer = JSONLWriter(jsonl_path)
            self._html_writer = HTMLWriter(html_path, self._renderer)
        except Exception as e:
            self._cleanup_writers()
            self._logger.error(f"[AgentLogger] 初始化失败: {e}")
            self.enabled = False

    def log_event(self, event: str, payload: Dict[str, Any], step: int = 0) -> None:
        """记录事件"""
        if not self.enabled:
            return

        try:
            event_obj = {
                "ts": datetime.now(self.config.timezone).isoformat(),
                "session_id": self.session_id,
                "step": step,
                "event": event,
                "payload": payload,
            }

            self._write_to_files(event_obj)
            self._stats.update(event, payload, step)

        except Exception as e:
            self._logger.error(f"[AgentLogger] 记录事件失败: {e}")

    def _write_to_files(self, event_obj: Dict[str, Any]) -> None:
        """写入到所有文件"""
        with self._lock:
            if self._jsonl_writer:
                line = json.dumps(event_obj, ensure_ascii=False)
                self._jsonl_writer.write(line)

            if self._html_writer and self._renderer:
                # 添加步骤包装
                step = event_obj.get("step", 0)
                html_content = self._render_step_wrapper(
                    self._renderer.render_event(event_obj),
                    step,
                    event_obj.get("ts", "")
                )
                self._html_writer.write(html_content)

    def _render_step_wrapper(self, content: str, step: int, ts: str) -> str:
        """渲染步骤包装器"""
        lines = []
        if step > 0:
            lines.append(f'<section class="block">')
            lines.append(f'<h3>Step {step}</h3>')
            lines.append(f'<div class="meta">Time: {html.escape(ts)}</div>')

        if content:
            lines.append(content)

        if step > 0:
            lines.append("</section>")

        return "\n".join(lines) + "\n" if lines else ""

    def flush(self) -> None:
        """刷新缓冲区到磁盘"""
        if not self.enabled:
            return
        try:
            with self._lock:
                if self._jsonl_writer:
                    self._jsonl_writer.flush()
                if self._html_writer:
                    self._html_writer.flush()
        except Exception as e:
            self._logger.error(f"[AgentLogger] 刷新失败: {e}")

    def _cleanup_writers(self) -> None:
        """清理写入器"""
        if self._jsonl_writer:
            self._jsonl_writer.close()
            self._jsonl_writer = None
        if self._html_writer:
            self._html_writer.close()
            self._html_writer = None

    def finalize(self) -> None:
        """结束会话"""
        if not self.enabled:
            return

        try:
            stats = self._stats.stats
            summary = {
                "total_steps": stats.total_steps,
                "tools_used": stats.tools_used,
                "total_tokens": stats.total_tokens,
            }
            self.log_event(Events.SESSION_SUMMARY, summary, step=0)

            self._cleanup_writers()

            jsonl_path = self.config.trace_dir / f"trace-{self.session_id}.jsonl"
            html_path = self.config.trace_dir / f"trace-{self.session_id}.html"
            self._logger.info(f"[AgentLogger] 轨迹已保存到 {jsonl_path}")
            self._logger.info(f"[AgentLogger] HTML报告已保存到 {html_path}")

        except Exception as e:
            self._logger.error(f"[AgentLogger] 结束会话失败: {e}")

    # ---------------------------------------------------------------------
    # 上下文管理器支持（向着稳定方向演进）
    # ---------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.finalize()
        return False


# ============================================================================
# 工厂函数
# ============================================================================

def create_agent_logger(
    trace_dir: str = "logs/traces",
    enabled: bool = True,
) -> AgentLogger:
    """创建 AgentLogger 实例的工厂函数"""
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    random_suffix = str(uuid.uuid4())[:8]
    session_id = f"s-{timestamp}-{random_suffix}"

    config = LoggerConfig(
        trace_dir=Path(trace_dir),
        enabled=enabled,
    )

    return AgentLogger(session_id, config)


# ============================================================================
# 便捷使用
# ============================================================================

@contextmanager
def trace_agent_session(trace_dir: str = "logs/traces"):
    """便捷的追踪上下文管理器"""
    logger = create_agent_logger(trace_dir)
    try:
        yield logger
    finally:
        logger.finalize()