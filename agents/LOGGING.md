# Agent 日志系统使用说明

## 概述

为 `s01_agent_loop.py` 添加了完整的日志系统，参考 `f:/Agent/MyCodeAgent` 项目的设计。

## 功能特性

### 1. 双格式输出
- **JSONL 文件**: 机器可读的完整执行轨迹
- **HTML 文件**: 人类可读的美化报告

### 2. 记录的事件类型
- `user_input`: 用户输入
- `model_output`: 模型输出（包含 token 使用量）
- `tool_call`: 工具调用
- `tool_result`: 工具执行结果
- `error`: 错误信息
- `session_summary`: 会话统计摘要

### 3. 统计信息
- 总步骤数
- 工具调用次数
- 累计 token 使用量

## 文件结构

```
agents/
├── agent_logger.py           # 日志系统实现
├── s01_agent_loop.py         # 集成了日志的 Agent
└── logs/                     # 日志输出目录
    └── traces/               # 轨迹文件
        ├── trace-s-20260329-123456-abcd.jsonl   # JSONL 格式
        └── trace-s-20260329-123456-abcd.html    # HTML 报告
```

## 使用方法

### 基本使用

```bash
# 运行带日志的 Agent
python agents/s01_agent_loop.py

# 退出时输入: q 或 exit
```

### 代码集成

```python
from agent_logger import create_agent_logger

# 创建日志记录器
trace_logger = create_agent_logger(
    trace_dir="logs/traces",
    enabled=True,
)

# 记录事件
trace_logger.log_event("user_input", {"text": "hello world"})

# 结束会话
trace_logger.finalize()
```

### 禁用日志

```python
trace_logger = create_agent_logger(
    trace_dir="logs/traces",
    enabled=False,  # 设置为 False 禁用日志
)
```

## 日志文件示例

### JSONL 格式

```json
{"ts": "2026-03-29T12:34:56.123456Z", "session_id": "s-20260329-123456-abcd", "step": 1, "event": "user_input", "payload": {"text": "列出当前目录的文件"}}
{"ts": "2026-03-29T12:34:57.234567Z", "session_id": "s-20260329-123456-abcd", "step": 1, "event": "model_output", "payload": {"content": "...", "usage": {...}, "tool_calls": [...]}}
{"ts": "2026-03-29T12:34:58.345678Z", "session_id": "s-20260329-123456-abcd", "step": 1, "event": "tool_call", "payload": {"tool": "bash", "args": {"command": "ls"}}}
{"ts": "2026-03-29T12:34:59.456789Z", "session_id": "s-20260329-123456-abcd", "step": 1, "event": "tool_result", "payload": {"tool": "bash", "result": "file1.py\nfile2.py\n"}}
```

### HTML 报告

打开 HTML 文件可以看到结构化的执行过程：
- 时间线视图
- 每个步骤的详细信息
- 彩色语法高亮
- Token 使用统计

## 日志分析

### 使用 jq 分析 JSONL

```bash
# 统计总 token 使用量
jq 'select(.event == "session_summary") | .payload.total_tokens' logs/traces/trace-*.jsonl

# 查看所有工具调用
jq 'select(.event == "tool_call")' logs/traces/trace-*.jsonl

# 查看错误信息
jq 'select(.event == "error")' logs/traces/trace-*.jsonl
```

### 使用 Python 分析

```python
import json

# 读取轨迹文件
with open("logs/traces/trace-s-20260329-123456-abcd.jsonl") as f:
    events = [json.loads(line) for line in f]

# 统计
tool_calls = [e for e in events if e["event"] == "tool_call"]
print(f"总工具调用次数: {len(tool_calls)}")
```

## 配置选项

### 日志目录

```python
trace_logger = create_agent_logger(
    trace_dir="custom/path/to/logs",  # 自定义路径
)
```

### 基础日志级别

```python
from agent_logger import setup_basic_logger

logger = setup_basic_logger("my_agent", level="DEBUG")  # DEBUG, INFO, WARNING, ERROR
```

## 注意事项

1. **日志文件大小**: 每次运行会创建新的日志文件，建议定期清理
2. **敏感信息**: 日志可能包含命令输出，注意不要记录敏感数据
3. **性能**: 日志记录对性能影响很小，但可以随时禁用
4. **线程安全**: 日志系统使用线程锁，支持多线程环境

## 参考

- 原始设计: `f:/Agent/MyCodeAgent/core/context_engine/trace_logger.py`
- 敏感数据处理: `f:/Agent/MyCodeAgent/core/context_engine/trace_sanitizer.py`

## 扩展建议

如需更高级的功能，可以考虑：
1. 添加敏感数据过滤（参考 TraceSanitizer）
2. 支持日志轮转（按大小或时间）
3. 添加日志压缩功能
4. 集成到其他 session（s02-s12）
