# Confluence Publisher

通过 Agent Skill 读取或发布 Confluence 文档，支持 Codex 和 Claude Code。按“空间名称 + 页面目录层级”定位，不需要用户查找空间 Key 或页面 ID。

## 功能

- 列出指定目录的直接子页面
- 将单个页面读取为 Markdown 或 Confluence Storage XHTML
- 递归导出指定目录下的页面
- 将 Markdown 或 Storage XHTML 发布为页面
- 更新同一目录下的同名页面

## 环境要求

- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/)
- 可以访问目标 Confluence 的 Personal Access Token（PAT）

## 安装 Skill

获取完整的 `confluence-publisher` 目录，并确认包含：

```text
confluence-publisher/
├── SKILL.md
├── README.md
├── agents/openai.yaml
└── scripts/
    ├── publish.py
    └── test_publish.py
```

### Codex

个人安装，对当前用户的所有任务生效：

```bash
mkdir -p ~/.codex/skills
cp -R /path/to/confluence-publisher ~/.codex/skills/confluence-publisher
```

项目安装，仅对当前项目生效：

```bash
mkdir -p /path/to/project/.agents/skills
cp -R /path/to/confluence-publisher /path/to/project/.agents/skills/confluence-publisher
```

Codex 使用 `agents/openai.yaml` 展示 Skill 名称和默认提示。

### Claude Code

个人安装，对当前用户的所有项目生效：

```bash
mkdir -p ~/.claude/skills
cp -R /path/to/confluence-publisher ~/.claude/skills/confluence-publisher
```

项目安装，仅对当前项目生效：

```bash
mkdir -p /path/to/project/.claude/skills
cp -R /path/to/confluence-publisher /path/to/project/.claude/skills/confluence-publisher
```

Claude Code 使用 `SKILL.md` 和 `scripts/`；`agents/openai.yaml` 是 Codex 元数据，Claude Code 会忽略它。安装后新建会话；如果 Skill 未被发现，重启对应客户端。

## 配置

推荐将配置保存在 `~/.config/confluence-publisher/.env`：

```dotenv
CONFLUENCE_BASE_URL=https://wiki.example.com
CONFLUENCE_TOKEN=替换为你的PAT
```

限制配置文件权限：

```bash
chmod 600 ~/.config/confluence-publisher/.env
```

也可以直接设置同名环境变量。环境变量优先于配置文件。不要把 PAT 写入 Skill、提交到 Git 或发送到对话中。

## 在 Codex 中使用

直接在请求中指定 `$confluence-publisher`、空间名称、目录层级和操作。例如：

```text
使用 $confluence-publisher，检查 Confluence 连接是否正常。
```

```text
使用 $confluence-publisher，列出空间“示例空间”下“一级目录 > 二级目录”的文档。
```

```text
使用 $confluence-publisher，读取空间“示例空间”下“一级目录 > 二级目录 > 示例页面”。
```

```text
使用 $confluence-publisher，递归读取空间“示例空间”下“一级目录 > 二级目录”的文档，最多读取 20 页。
```

```text
使用 $confluence-publisher，把“/path/to/document.md”发布到空间“示例空间”的“一级目录 > 二级目录”下，标题为“示例文档”。
```

目录层级必须从上到下书写，并与 Confluence 页面标题完全一致。

## 命令行使用

以下命令适合调试或脱离 Agent 单独运行。根据安装位置设置 Skill 路径：

```bash
# Codex 个人安装
SKILL_DIR="$HOME/.codex/skills/confluence-publisher"

# Claude Code 个人安装
# SKILL_DIR="$HOME/.claude/skills/confluence-publisher"
```

验证连接，不写入页面：

```bash
uv run --script "$SKILL_DIR/scripts/publish.py" check
```

列出目录的直接子页面：

```bash
uv run --script "$SKILL_DIR/scripts/publish.py" list \
  --space "示例空间" \
  --parent "一级目录" \
  --parent "二级目录"
```

读取一个直接子页面，默认输出 Markdown：

```bash
uv run --script "$SKILL_DIR/scripts/publish.py" read \
  --space "示例空间" \
  --parent "一级目录" \
  --parent "二级目录" \
  --title "示例页面"
```

递归读取目录下的页面并保存为 JSON：

```bash
uv run --script "$SKILL_DIR/scripts/publish.py" read-tree \
  --space "示例空间" \
  --parent "一级目录" \
  --parent "二级目录" \
  --max-pages 50 \
  --output "/tmp/confluence-tree.json"
```

发布 Markdown 文档：

```bash
uv run --script "$SKILL_DIR/scripts/publish.py" publish \
  --space "示例空间" \
  --parent "一级目录" \
  --parent "二级目录" \
  --title "示例文档" \
  --input "/path/to/document.md"
```

读取或发布原始 Confluence Storage XHTML 时添加：

```text
--format storage
```

所有命令将 JSON 写入标准输出；失败时返回非零退出码，并将错误 JSON 写入标准错误。

## 行为与限制

- 空间名称、目录标题和页面标题使用精确匹配。
- `read` 只读取指定目录的直接子页面。
- `read-tree` 读取目录的所有后代页面，不包含目录页面本身，并受 `--max-pages` 限制。
- 默认 Markdown 转换可能无法完整保留复杂宏；需要精确保留时使用 `--format storage`。
- 发布同一父页面下的同名页面时执行更新并递增版本号。
- 如果同名页面位于其他目录，命令会停止，不会自动移动页面。
- Skill 不删除页面。

## 迁移给其他用户

1. 打包或共享整个 `confluence-publisher` 目录，但排除 `.env`、`__pycache__` 和 `*.pyc`。
2. 安装 `uv`。
3. 按使用的客户端选择 Codex 或 Claude Code 安装目录。
4. 在新机器创建外部 `.env` 配置，并使用接收者自己的 PAT。
5. 运行 `check` 验证连接。

不要随 Skill 复制 PAT。
