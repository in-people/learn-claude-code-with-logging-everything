# Anthropic Code Execution 沙箱机制详解

## 概述

`CodeExecutionToolResultBlock` 是 Anthropic Messages API 中用于返回代码执行结果的 Content Block 类型。本文深入分析其实现原理、执行环境和安全机制。

## 核心问题

1. **代码在哪里执行？**
2. **如何实现的？**
3. **安全原理是什么？**

---

## 执行位置：云端沙箱

代码在 **Anthropic 云端的沙箱环境** 中执行，而不是在你的本地机器上。

```
┌─────────────────┐         ┌──────────────────────┐
│  你的应用        │         │  Anthropic Cloud     │
│  (Client SDK)   │ ──────> │  Sandboxed Container │
└─────────────────┘         └──────────────────────┘
                                    │
                                    ▼
                            ┌──────────────┐
                            │  代码执行     │
                            │  (隔离环境)   │
                            └──────────────┘
```

### 关键区别

| 特性 | 普通 Tool Use (如 Bash) | Code Execution Tool |
|------|------------------------|---------------------|
| **执行位置** | 你的本地机器 | Anthropic 云端沙箱 |
| **安全性** | 你自己控制 | Anthropic 沙箱隔离 |
| **语言支持** | 任何 Shell 命令 | Python、JavaScript 等 |
| **结果获取** | 直接读取 stdout | 通过 API 返回结构化结果 |
| **文件访问** | 本地文件系统 | 沙箱内文件系统（可导出）|

---

## 数据结构分析

### SDK 源码位置

```
anthropic/types/
├── code_execution_tool_result_block.py      # 主结果块
├── code_execution_result_block.py           # 普通执行结果
├── encrypted_code_execution_result_block.py # 加密执行结果（PFC 模式）
├── code_execution_tool_result_block_content.py
└── code_execution_output_block.py           # 输出文件引用
```

### 1. CodeExecutionToolResultBlock

```python
# code_execution_tool_result_block.py
class CodeExecutionToolResultBlock(BaseModel):
    content: CodeExecutionToolResultBlockContent  # 执行结果内容
    tool_use_id: str                               # 工具调用 ID
    type: Literal["code_execution_tool_result"]    # 类型标识
```

这是最外层的容器，包含执行结果的统一接口。

### 2. CodeExecutionResultBlock（普通结果）

```python
# code_execution_result_block.py
class CodeExecutionResultBlock(BaseModel):
    content: List[CodeExecutionOutputBlock]  # 输出文件列表
    return_code: int                          # 进程返回码（0=成功）
    stderr: str                               # 标准错误输出
    stdout: str                               # 标准输出
    type: Literal["code_execution_result"]
```

**字段说明**：
- `stdout`: 程序的标准输出
- `stderr`: 程序的错误输出
- `return_code`: 进程退出码（0 表示成功，非 0 表示错误）
- `content`: 执行过程中生成的文件（如图表、数据文件等）

### 3. EncryptedCodeExecutionResultBlock（加密结果）

```python
# encrypted_code_execution_result_block.py
class EncryptedCodeExecutionResultBlock(BaseModel):
    content: List[CodeExecutionOutputBlock]
    encrypted_stdout: str    # 🔒 加密的标准输出
    return_code: int
    stderr: str              # 错误输出不加密
    type: Literal["encrypted_code_execution_result"]
```

**用途**：用于 PFC (Private File Compute) 模式，保护敏感代码执行结果。

### 4. CodeExecutionOutputBlock

```python
# code_execution_output_block.py
class CodeExecutionOutputBlock(BaseModel):
    file_id: str              # 文件 ID，可用于下载
    type: Literal["code_execution_output"]
```

表示代码执行生成的文件（如 matplotlib 图表、数据文件等）。

---

## 实现技术：Sandbox Runtime (srt)

### 什么是 srt？

**[Sandbox Runtime (srt)](https://github.com/anthropic-experimental/sandbox-runtime)** 是 Anthropic 开源的轻量级沙箱工具：

> "A lightweight sandboxing tool for enforcing filesystem and network restrictions on arbitrary processes at the OS level, without requiring a container."

### 核心特性

- ✅ **OS 级别沙箱** - 不需要 Docker 等容器技术
- ✅ **文件系统限制** - 只能访问指定目录
- ✅ **网络限制** - 可控的网络访问规则
- ✅ **资源限制** - CPU、内存、执行时间限制
- ✅ **轻量级** - 比 Docker 容器更轻量，启动更快
- ✅ **跨平台** - 支持 Linux、macOS、Windows

### 工作流程

```mermaid
用户请求代码执行
    │
    ▼
Anthropic API 接收请求
    │
    ▼
启动沙箱容器 (srt)
    │
    ├─→ 设置文件系统限制
    ├─→ 设置网络规则
    └─→ 设置资源限制
    │
    ▼
在沙箱中执行代码
    │
    ├─→ stdout: 标准输出
    ├─→ stderr: 错误输出
    ├─→ return_code: 退出码
    └─→ files: 生成的文件
    │
    ▼
结果处理 & 加密 (如果需要)
    │
    ▼
返回结构化结果 (CodeExecutionResultBlock)
```

---

## 安全架构

### 多层防护

```
┌─────────────────────────────────────────────────────────┐
│           Anthropic Cloud Infrastructure                 │
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │   Layer 1: 网络边界                             │    │
│  │   - DDoS 防护                                   │    │
│  │   - 访问控制                                    │    │
│  └────────────────────────────────────────────────┘    │
│                          │                              │
│  ┌────────────────────────────────────────────────┐    │
│  │   Layer 2: API Gateway                         │    │
│  │   - 请求验证                                    │    │
│  │   - 权限检查                                    │    │
│  │   - 速率限制                                    │    │
│  └────────────────────────────────────────────────┘    │
│                          │                              │
│  ┌────────────────────────────────────────────────┐    │
│  │   Layer 3: Sandboxed Execution Environment     │    │
│  │                                                │    │
│  │  ┌──────────────────────────────────────────┐ │    │
│  │  │  User Code (Python/JS/Other)             │ │    │
│  │  └──────────────────────────────────────────┘ │    │
│  │                      │                         │    │
│  │                      ▼                         │    │
│  │  ┌──────────────────────────────────────────┐ │    │
│  │  │  Sandbox Runtime (srt)                   │ │    │
│  │  │  ┌────────────────────────────────────┐  │ │    │
│  │  │  │ Filesystem Restrictions            │  │ │    │
│  │  │  │ - 只读访问                          │  │ │    │
│  │  │  │ - 临时写入目录                      │  │ │    │
│  │  │  │ - 禁止访问敏感路径                  │  │ │    │
│  │  │  └────────────────────────────────────┘  │ │    │
│  │  │  ┌────────────────────────────────────┐  │ │    │
│  │  │  │ Network Restrictions               │  │ │    │
│  │  │  │ - 默认禁止所有出站                  │  │ │    │
│  │  │  │ - 白名单机制                        │  │ │    │
│  │  │  └────────────────────────────────────┘  │ │    │
│  │  │  ┌────────────────────────────────────┐  │ │    │
│  │  │  │ Resource Limits                    │  │ │    │
│  │  │  │ - CPU 时间限制                      │  │ │    │
│  │  │  │ - 内存限制                          │  │ │    │
│  │  │  │ - 执行超时                          │  │ │    │
│  │  │  └────────────────────────────────────┘  │ │    │
│  │  └──────────────────────────────────────────┘ │    │
│  └────────────────────────────────────────────────┘    │
│                          │                              │
│  ┌────────────────────────────────────────────────┐    │
│  │   Layer 4: Result Processing                   │    │
│  │   - 输出大小限制                                │    │
│  │   - 敏感信息过滤                                │    │
│  │   - 加密（PFC 模式）                            │    │
│  └────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

### 安全保证

1. **隔离性**：代码执行环境与 Anthropic 的其他服务完全隔离
2. **资源限制**：防止无限循环消耗资源
3. **文件系统隔离**：无法访问宿主机文件系统
4. **网络隔离**：默认无法访问外部网络
5. **时间限制**：超时自动终止

---

## API 使用示例

### 基本使用

```python
from anthropic import Anthropic

client = Anthropic()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": "计算斐波那契数列的前 20 项"
    }],
    tools=[{
        "type": "code_execution",
        "disabled": False
    }]
)

# 处理代码执行结果
for block in response.content:
    if block.type == "code_execution_tool_result":
        result = block.content

        # 普通执行结果
        if hasattr(result, "stdout"):
            print(f"输出:\n{result.stdout}")
            if result.stderr:
                print(f"错误:\n{result.stderr}")
            print(f"退出码: {result.return_code}")

            # 处理生成的文件
            for output in result.content:
                file_id = output.file_id
                # 可以通过 file_id 下载文件

        # 加密执行结果（PFC 模式）
        elif hasattr(result, "encrypted_stdout"):
            print("加密输出（需要解密）")
```

### 完整示例：数据分析

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=4096,
    messages=[{
        "role": "user",
        "content": """
        使用 matplotlib 创建一个正弦波图表，
        保存为图片，并返回统计信息
        """
    }],
    tools=[{
        "type": "code_execution",
        "disabled": False
    }]
)

for block in response.content:
    if block.type == "code_execution_tool_result":
        result = block.content

        # 打印执行输出
        print(result.stdout)

        # 获取生成的图片文件
        for output in result.content:
            if output.type == "code_execution_output":
                file_id = output.file_id
                print(f"生成的文件 ID: {file_id}")
                # 可通过 API 下载此文件
```

---

## Content Block 类型对比

在 `response.content` 中，`code_execution_tool_result` 与其他类型的区别：

| block.type | 来源 | 用途 |
|-----------|------|------|
| `"text"` | 模型生成 | 普通文本回复 |
| `"tool_use"` | 模型生成 | 调用工具（包括 code_execution） |
| `"code_execution_tool_result"` | 工具执行 | 返回代码执行结果 |
| `"thinking"` | 模型生成 | 扩展思考过程 |
| `"redacted_thinking"` | 模型生成 | 被编辑的思考 |

**注意**：`code_execution_tool_result` 不会出现在模型的 **输出** 中，它只出现在 **工具执行后的 user 消息** 中。

---

## 与普通 Bash 工具的区别

### Bash Tool（本地执行）

```python
# 定义
tools = [{
    "name": "bash",
    "description": "Run shell command",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string"}
        }
    }
}]

# 执行流程
User Request → Model → bash tool → 本地 Shell → 返回结果
```

**特点**：
- ✅ 完全控制执行环境
- ✅ 可访问本地文件系统
- ✅ 可访问本地网络
- ❌ 安全风险由你承担
- ❌ 需要自己实现沙箱

### Code Execution Tool（云端沙箱）

```python
# 定义
tools = [{
    "type": "code_execution"
}]

# 执行流程
User Request → Model → Anthropic Cloud → 沙箱执行 → 返回结果
```

**特点**：
- ✅ Anthropic 管理安全
- ✅ 开箱即用的沙箱
- ✅ 支持多种编程语言
- ❌ 无法访问你的本地文件
- ❌ 网络访问受限

---

## 最佳实践

### 1. 选择合适的工具

```python
# ✅ 适合 Code Execution Tool
- 数据分析和可视化
- 算法验证
- 数学计算
- 生成示例代码

# ✅ 适合 Bash Tool
- 文件系统操作
- Git 操作
- 运行测试
- 本地服务管理
```

### 2. 处理执行错误

```python
if result.return_code != 0:
    print(f"执行失败，退出码: {result.return_code}")
    print(f"错误输出: {result.stderr}")
    # 根据错误类型进行重试或报告
```

### 3. 资源限制

```python
# 设置合理的超时
response = client.messages.create(
    model="claude-sonnet-4-6",
    messages=[...],
    tools=[{
        "type": "code_execution"
    }],
    max_tokens=1024,
)
```

### 4. 安全注意事项

- ⚠️ 不要在代码中硬编码敏感信息
- ⚠️ 注意沙箱的文件系统限制
- ⚠️ 网络访问受限，避免依赖外部 API
- ✅ 使用 PFC 模式处理敏感数据

---

## 技术细节

### srt 实现原理

**操作系统级隔离**：

1. **Linux**: 使用 `seccomp-bpf` 系统调用过滤、`cgroups` 资源限制、`namespaces` 隔离
2. **macOS**: 使用 `sandbox` 框架（Seatbelt）
3. **Windows**: 使用 Job Objects 和 Windows Sandbox API

**不需要容器**：srt 直接使用操作系统原生的沙箱机制，比 Docker 更轻量。

### 配置示例

```json
{
  "filesystem": {
    "readonly": ["/usr", "/lib"],
    "writeable": ["/tmp"],
    "blocked": ["/etc", "/var"]
  },
  "network": {
    "outbound": "deny",
    "exceptions": ["https://api.example.com"]
  },
  "resources": {
    "cpu_time": 5,
    "memory": "512MB",
    "timeout": 30
  }
}
```

---

## 相关资源

### 官方文档
- [Code Execution Tool - Claude API Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool)
- [Making Claude Code more secure and autonomous with sandboxing - Anthropic Engineering Blog](https://www.anthropic.com/engineering/claude-code-sandboxing)
- [Sandboxing - Claude Code Docs](https://code.claude.com/docs/en/sandboxing)

### 开源项目
- [Sandbox Runtime (srt) - GitHub](https://github.com/anthropic-experimental/sandbox-runtime)

### SDK 源码
- `anthropic/types/code_execution_tool_result_block.py`
- `anthropic/types/code_execution_result_block.py`
- `anthropic/types/encrypted_code_execution_result_block.py`
- `anthropic/types/code_execution_output_block.py`

---

## 总结

**`code_execution_tool_result` 的本质**：

1. **执行位置**：Anthropic 云端沙箱（不在本地）
2. **核心技术**：sandbox-runtime (srt) - OS 级别隔离
3. **安全保证**：文件系统/网络限制、资源隔离、多层防护
4. **结果返回**：结构化的 `CodeExecutionResultBlock`（stdout/stderr/return_code/files）
5. **设计目的**：让 Claude 安全地执行代码而不影响你的系统

**适用场景**：
- ✅ 数据分析和可视化
- ✅ 算法验证和数学计算
- ✅ 生成和测试代码
- ✅ 不需要访问本地资源的计算任务

**不适用场景**：
- ❌ 需要访问本地文件系统
- ❌ 需要调用本地服务
- ❌ 需要不受限的网络访问

---

*文档版本：v1.0*
*最后更新：2026-03-29*
