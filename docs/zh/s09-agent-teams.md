# s09: Agent Teams (智能体团队)

`s01 > s02 > s03 > s04 > s05 > s06 | s07 > s08 > [ s09 ] s10 > s11 > s12`

> *"任务太大一个人干不完, 要能分给队友"* -- 持久化队友 + JSONL 邮箱。

## 问题

子智能体 (s04) 是一次性的: 生成、干活、返回摘要、消亡。没有身份, 没有跨调用的记忆。后台任务 (s08) 能跑 shell 命令, 但做不了 LLM 引导的决策。

真正的团队协作需要三样东西: (1) 能跨多轮对话存活的持久智能体, (2) 身份和生命周期管理, (3) 智能体之间的通信通道。

## 解决方案

```
Teammate lifecycle:
  spawn -> WORKING -> IDLE -> WORKING -> ... -> SHUTDOWN

Communication:
  .team/
    config.json           <- team roster + statuses
    inbox/
      alice.jsonl         <- append-only, drain-on-read
      bob.jsonl
      lead.jsonl

              +--------+    send("alice","bob","...")    +--------+
              | alice  | -----------------------------> |  bob   |
              | loop   |    bob.jsonl << {json_line}    |  loop  |
              +--------+                                +--------+
                   ^                                         |
                   |        BUS.read_inbox("alice")          |
                   +---- alice.jsonl -> read + drain ---------+
```

## 工作原理

1. TeammateManager 通过 config.json 维护团队名册。

```python
class TeammateManager:
    def __init__(self, team_dir: Path):
        self.dir = team_dir
        self.dir.mkdir(exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.config = self._load_config()
        self.threads = {}
```

```json
{
  "team_name": "default",
  "members": [
    {
      "name": "alice",
      "role": "coder",
      "status": "idle"
    },
    {
      "name": "bob",
      "role": "tester",
      "status": "idle"
    }
  ]
}
```

2. `spawn()` 创建队友并在线程中启动 agent loop。  spawn中文翻译：生成、派生

```python
def spawn(self, name: str, role: str, prompt: str) -> str:
    member = {"name": name, "role": role, "status": "working"}
    self.config["members"].append(member)
    self._save_config()
    # 创建一个线程对象
    # _teammate_loop 指定线程启动后要执行的目标函数
    # args传递给目标函数的参数
    thread = threading.Thread(
        target=self._teammate_loop,
        args=(name, role, prompt), daemon=True)
    thread.start()
    return f"Spawned teammate '{name}' (role: {role})"
```

3. MessageBus: append-only 的 JSONL 收件箱。`send()` 追加一行; `read_inbox()` 读取全部并清空。

```python
class MessageBus:
    def send(self, sender, to, content, msg_type="message", extra=None):
        msg = {"type": msg_type, "from": sender,
               "content": content, "timestamp": time.time()}
        if extra:
            msg.update(extra)
        with open(self.dir / f"{to}.jsonl", "a") as f:
            f.write(json.dumps(msg) + "\n")

    def read_inbox(self, name):
        path = self.dir / f"{name}.jsonl"
        if not path.exists(): return "[]"
        # 读取全部内容。 按行分割成列表。 将每行JSON字符串解析为Python对象
        msgs = [json.loads(l) for l in path.read_text().strip().splitlines() if l]
        path.write_text("")  # drain
        return json.dumps(msgs, indent=2)
```

4. 每个队友在每次 LLM 调用前检查收件箱, 将消息注入上下文。

```python
def _teammate_loop(self, name, role, prompt):
    messages = [{"role": "user", "content": prompt}]
    for _ in range(50):
        inbox = BUS.read_inbox(name)
        if inbox != "[]":
            messages.append({"role": "user",
                "content": f"<inbox>{inbox}</inbox>"})
            messages.append({"role": "assistant",
                "content": "Noted inbox messages."})
        response = client.messages.create(...)
        if response.stop_reason != "tool_use":
            break
        # execute tools, append results...
    self._find_member(name)["status"] = "idle"
```

## 相对 s08 的变更

| 组件       | 之前 (s08) | 之后 (s09)                 |
| ---------- | ---------- | -------------------------- |
| Tools      | 6          | 9 (+spawn/send/read_inbox) |
| 智能体数量 | 单一       | 领导 + N 个队友            |
| 持久化     | 无         | config.json + JSONL 收件箱 |
| 线程       | 后台命令   | 每线程完整 agent loop      |
| 生命周期   | 一次性     | idle -> working -> idle    |
| 通信       | 无         | message + broadcast        |

## 试一试

```sh
cd learn-claude-code
python agents/s09_agent_teams.py
```

试试这些 prompt (英文 prompt 对 LLM 效果更好, 也可以用中文):

1. `Spawn alice (coder) and bob (tester). Have alice send bob a message.`
生成角色并通讯：生成了 Alice（程序员）和 Bob（测试员），并让 Alice 给 Bob 发了消息
2. `Broadcast "status update: phase 1 complete" to all teammates`
全员广播：向所有队友广播“状态更新：第一阶段完成”
3. `Check the lead inbox for any messages`
检查收件箱：查看主管（Lead）的收件箱有没有新消息
4. 输入 `/team` 查看团队名册和状态
5. 输入 `/inbox` 手动检查领导的收件箱

## Agent执行过程剖析

* `Spawn alice (coder) and bob (tester). Have alice send bob a message.`

让alice开发一个python程序，解决汉诺塔问题。


让bob评审alice开发的用来解决汉诺塔问题的python代码

这一步Agent没有搞出来，失智了。 在不停的循环。

> bash: ./.team/inbox/lead.jsonl
> bash: (no output)
> bash: (no output)
> read_inbox: []
> list_teammates: Team: default
> alice (Python Developer): idle
> bob (tester): idle
> send_message: Sent message to bob
> bash: (no output)
> read_inbox: []
> bash: (no output)
> read_inbox: []
> bash: 总用量 76
> -rw-rw-r-- 1 10346371@zte.intra 10346371@zte.intra  3318 3月   8 09:32 hanoi.py
> drwxrwxr-x 7 10346371@zte.intra 10346371@zte.intra  4096 3月   7 23:05 web
> -rw-rw-r-- 1 10346371@zte.intra 10346371
> bash: (no output)
> read_inbox: []
> bash: (no output)
>

分析：Alice没有记忆。 每次执行完Alice邮箱会被清空，导致下一次执行的时候，缺少上下文。


* `Broadcast "status update: phase 1 complete" to all teammates`
* `Check the lead inbox for any messages`
* 输入 `/team` 查看团队名册和状态
* 输入 `/inbox` 手动检查领导的收件箱