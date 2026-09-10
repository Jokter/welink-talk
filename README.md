# WeLink ZCode Bridge

通过 WeLink 私聊或专用群，在手机上与电脑中的 ZCode 交互。第一版仅包含对话、模型切换和会话重置。所有有效消息都必须以 `/` 开头，其他消息会被忽略。

## 使用方式

1. 安装 Python 3.10+、`welink-cli` 和 ZCode，并确保三者都可在电脑上正常使用。
2. PowerShell 进入项目目录，执行：

   ```powershell
   .\setup.ps1
   ```

3. 编辑生成的 `config.json`：
   - `private_chats`：允许交互的私聊账号。
   - `group_chats`：专用群 ID、允许发送者和触发前缀。
   - `zcode.command`：你当前 ZCode 版本真实可用的非交互命令。
   - `zcode.models`：手机上允许选择的模型。
4. 启动：

   ```powershell
   .\start.ps1
   ```

首次启动只记录最近消息，不执行旧消息。启动后再从手机发送新消息。

## 手机命令

私聊或群聊中，以 `/` 开头的内容会发送给 ZCode：

```text
/帮我分析当前项目的目录结构
```

控制命令：

```text
/模型 列表            查看模型
/模型 当前            查看当前模型
/模型 切换 2          按序号切换模型
/模型 切换 GLM-5.3    按名称切换模型
/新对话               清除当前会话上下文
/帮助                 查看帮助
```

每个私聊或群聊分别保存模型和最近六轮上下文。切换模型时自动开始新会话。

## 只回传最终结果

桥接程序只读取 ZCode 的标准输出，不会把标准错误中的运行日志发到手机。对于 JSON/JSONL 输出，只提取 `final`、`answer`、`result` 等最终结果字段；文本中的 `<think>...</think>` 和 reasoning 代码块也会被移除。

最可靠的方式仍然是让 `zcode.command` 使用 ZCode 自带的 JSON 和 final-only/print 模式。不同版本参数可能不同，请以你电脑上 ZCode 的帮助信息为准。

## ZCode 命令模板

命令的每个参数必须作为数组中的独立元素：

```json
"command": [
  "C:\\Path\\To\\zcode.exe",
  "--model",
  "{model}",
  "--output-format",
  "json"
]
```

支持两个占位符：

- `{model}`：当前会话选择的模型。
- `{prompt}`：完整问题。如果 `prompt_via_stdin` 为 `true`，通常不需要把 `{prompt}` 放进命令。

## 当前边界

- CLI 文档没有实时消息订阅能力，因此采用每5秒查询一次历史消息。
- 当前版本一次处理一个问题，适合个人使用。
- 需要使用单独的 WeLink 机器人账号运行，否则可能无法正确区分自己发出的回复。
- ZCode 的命令行参数尚未统一公开，必须填入你电脑上实际可执行的命令。
