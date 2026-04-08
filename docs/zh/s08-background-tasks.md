# s08: Background Tasks (后台任务)

`s01 > s02 > s03 > s04 > s05 > s06 | s07 > [ s08 ] s09 > s10 > s11 > s12`

> *"**慢操作**丢后台, agent 继续想下一步"* -- 后台线程跑命令, 完成后注入通知。

## 问题

有些命令要跑好几分钟: `npm install`、`pytest`、`docker build`。阻塞式循环下模型只能干等。用户说 "装依赖, 顺便建个配置文件", 智能体却只能一个一个来。

## 解决方案

```
Main thread                Background thread
+-----------------+        +-----------------+
| agent loop      |        | subprocess runs |
| ...             |        | ...             |
| [LLM call] <---+------- | enqueue(result) |
|  ^drain queue   |        +-----------------+
+-----------------+

Timeline:
Agent --[spawn A]--[spawn B]--[other work]----
             |          |
             v          v
          [A runs]   [B runs]      (parallel)
             |          |
             +-- results injected before next LLM call --+
```

## 工作原理

1. BackgroundManager 用**线程安全**的**通知队列**追踪任务。

```python
class BackgroundManager:
    def __init__(self):
        self.tasks = {}
        self._notification_queue = [] # 通知队列
        self._lock = threading.Lock()
```

2. `run()` 启动守护线程, 立即返回。

```python
def run(self, command: str) -> str:
    task_id = str(uuid.uuid4())[:8]
    self.tasks[task_id] = {"status": "running", "command": command}
    thread = threading.Thread(
        target=self._execute, args=(task_id, command), daemon=True)
    thread.start()
    return f"Background task {task_id} started"
```

### 代码详解

```python
task_id = str(uuid.uuid4())[:8]
```

- 生成8位随机任务ID（如 `"a3b5c7d2"`）
- 使用UUID确保唯一性

```python
self.tasks[task_id] = {"status": "running", "command": command}
```

- 在内存中创建任务记录
- 状态初始化为 `"running"`

```python
thread = threading.Thread(
    target=self._execute,
    args=(task_id, command),
    daemon=True
)
```

- 创建新线程，执行 `_execute` 方法
- 传入任务ID和命令作为参数
- `daemon=True`: 主线程退出时子线程自动终止

```python
thread.start()
```

- 启动后台线程，**立即返回**

```python
return f"Background task {task_id} started"
```

- 立即返回给agent，不等待命令执行完成

### 执行流程

```
主线程                    后台线程
│                        │
│ 1. 生成task_id          │
│ 2. 创建任务记录         │
│ 3. 启动线程 ───────────>│ 4. 执行命令
│ 5. 立即返回 │              5. 捕获输出
│                        │ 6. 更新状态
│                        │ 7. 推送通知队列
```

### 关键特性

- **非阻塞**: 立即返回，agent可继续工作
- **并行**: 多个后台任务可同时运行
- **隔离**: 每个任务在独立线程中执行
- **可追踪**: 通过task_id查询状态和结果

这就是"即发即忘"模式的核心实现。

3. 子进程完成后, 结果进入通知队列。

```python
def _execute(self, task_id, command):
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
            capture_output=True, text=True, timeout=300)
        output = (r.stdout + r.stderr).strip()[:50000]
    except subprocess.TimeoutExpired:
        output = "Error: Timeout (300s)"
    with self._lock:  # 获取线程锁，保证队列操作原子性
        self._notification_queue.append({
            "task_id": task_id, "result": output[:500]})
```

4. 每次 LLM 调用前排空通知队列。

```python
def agent_loop(messages: list):
    while True:
        notifs = BG.drain_notifications()
        if notifs:
            notif_text = "\n".join(
                f"[bg:{n['task_id']}] {n['result']}" for n in notifs)  # 拼接后台任务的执行结果
            messages.append({"role": "user",
                "content": f"<background-results>\n{notif_text}\n"
                           f"</background-results>"})
            messages.append({"role": "assistant",
                "content": "Noted background results."})
        response = client.messages.create(...)
```

循环保持单线程。只有子进程 I/O 被并行化。

## 相对 s07 的变更

| 组件     | 之前 (s07) | 之后 (s08)                        |
| -------- | ---------- | --------------------------------- |
| Tools    | 8          | 6 (基础 + background_run + check) |
| 执行方式 | 仅阻塞     | 阻塞 + 后台线程                   |
| 通知机制 | 无         | 每轮排空的队列                    |
| 并发     | 无         | 守护线程                          |

## 试一试

```sh
cd learn-claude-code
python agents/s08_background_tasks.py
```

试试这些 prompt (英文 prompt 对 LLM 效果更好, 也可以用中文):

1. `Run "sleep 5 && echo done" in the background, then create a file while it runs`
后台运行 sleep 5 && echo done 这个命令，然后趁着它运行的时候，去创建一个文件
2. `Start 3 background tasks: "sleep 2", "sleep 4", "sleep 6". Check their status.`
启动 3 个后台任务：分别是 "sleep 2"、"sleep 4" 和 "sleep 6"。然后检查一下它们的状态。
3. `Run pytest in the background and keep working on other things`
在后台运行 pytest，同时继续做其他事情（即不阻塞当前终端）