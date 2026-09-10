# WeLink Pi Bridge

将 WeLink 账号 `p_xiaoluban` 作为 Pi coding agent 的聊天入口。指定用户通过私聊 `p_xiaoluban`，即可像普通聊天一样调用电脑中的 Pi。

账号关系：

- 接收账号：电脑端 WeLink 和 `welink-cli` 登录控制用户，例如 `w00789509`。
- 对话账号：`p_xiaoluban`。
- 接收方向：`welink-cli` 从 `w00789509` 的会话历史读取发给 `p_xiaoluban` 的消息。
- 回复方向：桥接程序直接调用 `send_welink_message` MCP，由 `p_xiaoluban` 回复 `w00789509`。

## 快速开始

环境要求：

- Windows PowerShell 5 或 PowerShell 7。
- Python 3.10+。
- Node.js 和 npm。
- `welink-cli`。
- WeLink PC 客户端已登录控制用户，例如 `w00789509`。
- Pi 的 `mcp.json` 已配置 `welink-msg`，且其中的 `WELINK_TOKEN` 属于 `p_xiaoluban`。

进入项目目录后执行：

```powershell
.\setup.ps1
```

也可以直接指定允许使用机器人的用户：

```powershell
.\setup.ps1 -AllowedUserAccount w00789509
```

向导会自动：

1. 刷新控制用户的 WeLink Token，并识别当前 UID。
2. 配置允许与机器人交互的用户，例如 `w00789509`。
3. 检查 Pi；未安装时询问是否自动安装。
4. 调用 `pi --list-models` 读取已认证的模型。
5. 读取 Pi 的默认模型。
6. 自动发现 Pi 的 `welink-msg` stdio MCP，并确认其提供 `send_welink_message`。
7. 配置允许的工作区和默认目录。
8. 生成只保存在本机的 `config.json`。
9. 询问是否立即启动桥接服务。

Pi 的安装命令为：

```powershell
npm install -g --ignore-scripts @earendil-works/pi-coding-agent
```

如果还没有登录模型提供方，先运行：

```powershell
pi
```

然后在 Pi 中执行：

```text
/login
```

完成后退出 Pi，再重新执行 `.\setup.ps1`。

## Pi 调用方式

桥接程序使用 Pi 官方非交互打印模式：

```text
pi --model {model} --no-session --no-approve -p
```

完整问题通过标准输入传入。Pi 只把最终回复写入标准输出，因此手机不会看到思考过程、工具调用过程或终端界面。

- `--model`：选择当前会话模型。
- `--no-session`：不创建 Pi 会话文件；最近对话由桥接程序管理。
- `--no-approve`：不加载未经信任的项目本地扩展和配置。
- `-p`：输出最终回复后退出。

## 使用方式

```text
帮我分析当前项目的目录结构
刚才提到的第二个问题怎么修改
直接修改并给我最终结果
```

不需要 `/` 前缀，也不提供 `/help`、`/model`、`/dir`、`/new` 等聊天命令。模型和工作目录在电脑端通过安装向导配置。桥接程序保存最近10轮上下文，因此可以连续追问；程序重启后历史仍保存在 `state.json` 中。

## 启动与停止

完成配置后启动：

```powershell
.\start.ps1
```

每次轮询会读取最近5条消息，再按发送者、接收者和消息 ID 过滤。首次启动会处理其中尚未去重的用户消息，之后不会重复执行同一条消息。

程序以 `w00789509` 的登录身份，每5秒查询它与 `p_xiaoluban` 会话中的最近5条消息，并同时校验 `sender=w00789509` 和 `receiver=p_xiaoluban`。Pi 只生成最终答案；回复不经过 Pi 决策或 `welink-cli`，而由桥接程序直接调用 MCP，以 `p_xiaoluban` 身份发送。

停止服务：

```text
Ctrl+C
```

## WeLink Token 续期

程序启动时刷新一次控制用户 Token，之后默认每20分钟刷新。消息查询失败时还会强制刷新并重试一次。只要 WeLink PC 保持登录控制用户，一般不需要再次扫码。`p_xiaoluban` 的发送 Token 继续保存在原 Pi MCP 配置中，不会复制到本项目配置。

## 本地文件

- `config.json`：本机配置，不提交 Git。
- `state.json`：消息去重、模型和对话状态，不提交 Git。
- `bridge.log`：运行错误，不提交 Git。

## 安全边界

Pi 默认拥有启动用户的文件、进程和网络权限。请只将可信任的 WeLink 账号配置为控制用户。当前默认使用 `--no-approve`，避免自动加载项目本地扩展；Pi 自带的文件和命令工具仍具有当前 Windows 用户权限。
