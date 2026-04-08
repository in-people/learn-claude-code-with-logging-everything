# s01: The Agent Loop (智能体循环)

`[ s01 ] s02 > s03 > s04 > s05 > s06 | s07 > s08 > s09 > s10 > s11 > s12`

> *"One loop & Bash is all you need"* -- 一个工具 + 一个循环 = 一个智能体。

## todo
todo: 添加云端沙箱运行代码的功能！
## 问题

语言模型能推理代码, 但碰不到真实世界 -- 不能读文件、跑测试、看报错。没有循环, 每次工具调用你都得手动把结果粘回去。你自己就是那个循环。

## 解决方案

```
+--------+      +-------+      +---------+
|  User  | ---> |  LLM  | ---> |  Tool   |
| prompt |      |       |      | execute |
+--------+      +---+---+      +----+----+
                    ^                |
                    |   tool_result  |
                    +----------------+
                    (loop until stop_reason != "tool_use")
```

一个退出条件控制整个流程。循环持续运行, 直到模型不再调用工具。

## 工作原理

1. 用户 prompt 作为第一条消息。

```python
messages.append({"role": "user", "content": query})
```

2. 将消息和**工具定义**一起发给 LLM。

```python
response = client.messages.create(
    model=MODEL, system=SYSTEM, messages=messages,
    tools=TOOLS, max_tokens=8000,
)
```

3. 追加助手响应。检查 `stop_reason` -- 如果模型没有调用工具, 结束。

```python
messages.append({"role": "assistant", "content": response.content})
if response.stop_reason != "tool_use":
    return
```

4. 执行每个工具调用, 收集结果, 作为 user 消息追加。回到第 2 步。

```python
results = []
for block in response.content:
    if block.type == "tool_use":
        output = run_bash(block.input["command"])
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
        })
messages.append({"role": "user", "content": results})
```

组装为一个完整函数:

```python
def agent_loop(query):
    messages = [{"role": "user", "content": query}]
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = run_bash(block.input["command"])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})
```

不到 30 行, 这就是整个智能体。后面 11 个章节都在这个**循环上叠加机制** -- **循环本身始终不变**。

## 变更内容

| 组件         | 之前 | 之后                          |
| ------------ | ---- | ----------------------------- |
| Agent loop   | (无) | `while True` + stop_reason  |
| Tools        | (无) | `bash` (单一工具)           |
| Messages     | (无) | 累积式消息列表                |
| Control flow | (无) | `stop_reason != "tool_use"` |

## Content Block 类型

Anthropic Messages API 的 `response.content` 是一个 ContentBlock 列表，包含多种类型的 block。根据 SDK 源码 (`anthropic/types/content_block.py`)，共有 **12 种** content block 类型：

| block.type | 类名 | 用途 |
|-----------|------|------|
| `"text"` | `TextBlock` | 普通文本内容，支持 citations |
| `"tool_use"` | `ToolUseBlock` | 模型调用工具 |
| `"thinking"` | `ThinkingBlock` | 模型思考过程（扩展思考模式） |
| `"redacted_thinking"` | `RedactedThinkingBlock` | 被编辑的思考内容 |
| `"server_tool_use"` | `ServerToolUseBlock` | Anthropic 内置工具调用 |
| `"web_search_tool_result"` | `WebSearchToolResultBlock` | 网络搜索工具结果 |
| `"web_fetch_tool_result"` | `WebFetchToolResultBlock` | 网络获取工具结果 |
| `"code_execution_tool_result"` | `CodeExecutionToolResultBlock` | 代码执行工具结果 |
| `"bash_code_execution_tool_result"` | `BashCodeExecutionToolResultBlock` | Bash 代码执行结果 |
| `"text_editor_code_execution_tool_result"` | `TextEditorCodeExecutionToolResultBlock` | 文本编辑器执行结果 |
| `"tool_search_tool_result"` | `ToolSearchToolResultBlock` | 工具搜索结果 |
| `"container_upload"` | `ContainerUploadBlock` | 文件上传到容器 |

在本教程的基础 Agent 中，我们主要处理 **`text`** 和 **`tool_use`** 两种类型：

```python
for block in response.content:
    if block.type == "tool_use":
        # 处理工具调用
        output = run_bash(block.input["command"])
    elif block.type == "text":
        # 处理文本内容
        text = block.text
```

如果要支持扩展思考模式或其他高级功能，需要处理 `thinking`、`redacted_thinking` 等类型。

## 试一试

```sh
cd learn-claude-code
python agents/s01_agent_loop.py
```

试试这些 prompt (英文 prompt 对 LLM 效果更好, 也可以用中文):

1. `Create a file called hello.py that prints "Hello, World!"`
创建一个名为 hello.py 的文件，打印 "Hello, World!"
2. `List all Python files in this directory`
列出当前目录下的所有 Python 文件
3. `What is the current git branch?`
当前的 git 分支是什么？
4. `Create a directory called test_output and write 3 files in it`
创建一个名为 test_output 的目录，并在其中写入 3 个文件