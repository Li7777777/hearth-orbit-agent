# Hearth Orbit Agent

炉火星环：一个面向家庭食物管理的 Django + AI Agent 应用。

它不只做“菜谱推荐”，而是尝试把一餐饭背后的整条轨道串起来：订单识别、食材库存、菜量统计、保存状态、食材消耗、外部菜谱同步，以及基于库存和历史的三餐智能规划。

> 当前项目仍处于本地开发阶段，默认以私有仓库方式维护。请不要提交 `.env`、数据库、上传图片、日志、缓存或外部下载数据。

## 核心能力

- 账号登录保护：业务页面默认需要登录访问。
- 首页仪表盘：展示订单、食材、菜量、菜谱和今日三餐计划。
- 订单截图 OCR：上传订单/小票图片，识别食材、数量和价格。
- 视觉模型兜底：OCR 不可用或置信度不足时，可接入 OpenAI-compatible 视觉模型辅助识别。
- 食材库存管理：维护分类、规格、单价、储存方式、入库日期、启用状态和图片。
- 保存与消耗状态：支持把食材标记为吃完、丢弃，并沉淀历史统计。
- 订单统计分析：按日期统计菜量、金额、热门食材和丢弃趋势。
- 菜谱管理：维护菜谱、用料、步骤、难度、份数、图片和小贴士。
- 外部菜谱同步：可从 CookLikeHOC 等外部 Markdown 菜谱源导入。
- 多 Agent 三餐推荐：根据库存、消耗历史、订单用量、价格和菜谱适配度生成早餐、午餐、晚餐建议。

## 技术栈

- Python 3.11+
- Django 5.2
- uv
- SQLite / MySQL
- PaddleOCR / PaddlePaddle
- Pillow
- python-dotenv
- Alpine.js
- HTMX
- Chart.js

## 项目结构

```text
.
├── apps/
│   ├── accounts/      # 登录、登出、全局登录保护
│   ├── dashboard/     # 首页仪表盘和三餐计划入口
│   ├── dishes/        # 食材分类、库存、匹配和状态管理
│   ├── ocr/           # OCR、视觉辅助、订单解析和确认
│   ├── orders/        # 订单、明细、每日统计和图表
│   └── recipes/       # 菜谱、外部同步、推荐历史和 meal agents
├── config/            # Django settings、urls、asgi、wsgi
├── static/            # 全局 CSS / JS
├── templates/         # 全局模板和组件
├── manage.py          # Django 管理入口
├── pyproject.toml     # uv 项目依赖
├── start.bat          # Windows 本地启动脚本
├── tests.py           # 综合测试入口
├── user.md            # 使用说明
└── uv.lock            # 依赖锁定文件
```

以下内容属于本地运行产物，不应进入仓库：

- `.env`
- `db.sqlite3`
- `media/`
- `staticfiles/`
- `.venv/`
- `.ruff_cache/`
- `.pytest_cache/`
- `.codex-run/`
- `external/CookLikeHOC-main/`
- `external/*.zip`
- `*.log`

## 快速启动

### 方式一：Windows 脚本

双击根目录的 `start.bat`，或在终端运行：

```bat
start.bat
```

脚本会执行依赖同步、数据库迁移并启动本地服务。

默认访问：

```text
http://127.0.0.1:8000/
```

### 方式二：手动启动

```powershell
uv sync
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver 127.0.0.1:8000
```

首次运行后需要创建超级用户，再登录访问业务页面。

## 环境变量

复制样例文件：

```powershell
Copy-Item .env.example .env
```

常用配置见 `.env.example`。其中 API Key 默认留空，只有启用视觉识别或 LLM Agent 复核时才需要填写。

关键安全约定：

- `.env` 必须只保留在本地。
- 生产或公网部署时必须设置强随机 `SECRET_KEY`。
- `DEBUG=false` 时必须显式配置 `ALLOWED_HOSTS`。
- 第三方模型 Key 不写入 README、代码、测试文件或提交历史。

## 异步三餐推荐

首页不会直接等待大模型。它会立即展示本地确定性方案和最近一次全大模型推荐快照；快照过期后，系统在后台无感刷新，并通过 HTMX 自动更新推荐区域。

刷新行为可通过 `.env` 调整：

```env
MEAL_PLAN_BACKGROUND_REFRESH_ENABLED=true
MEAL_PLAN_BACKGROUND_REFRESH_MINUTES=240
MEAL_PLAN_BACKGROUND_ERROR_RETRY_MINUTES=30
```

需要使用 Windows 任务计划或 cron 定时预热时，可独立运行：

```powershell
uv run python manage.py refresh_meal_plan --force
```

不加 `--force` 时，仅在快照过期后生成。模型响应慢或暂时失败不会拖慢首页，旧快照仍可继续展示；失败后默认冷却 30 分钟再自动重试，避免持续请求异常模型。

## 数据库

默认使用 SQLite：

```env
USE_MYSQL=false
```

本地数据库文件为 `db.sqlite3`，已经被 `.gitignore` 排除。

如需 MySQL：

```env
USE_MYSQL=true
DB_NAME=your_database
DB_USER=your_user
DB_PASSWORD=your_password
DB_HOST=127.0.0.1
DB_PORT=3306
```

## 外部菜谱源

项目支持从外部菜谱仓库同步 Markdown 菜谱。默认配置在 `.env.example` 中：

```env
COOKLIKEHOC_REPO_URL=https://github.com/Gar-b-age/CookLikeHOC
COOKLIKEHOC_ZIP_URL=https://codeload.github.com/Gar-b-age/CookLikeHOC/zip/refs/heads/main
COOKLIKEHOC_REPO_PATH=external/CookLikeHOC-main
```

外部下载目录和 zip 包是可再生成数据，默认不提交到仓库。

## 测试与检查

```powershell
uv run python manage.py check
uv run python manage.py test
uv run ruff check .
```

如果 PaddleOCR 或 PaddlePaddle 依赖在本机首次安装较慢，优先保证 Django 配置检查和核心业务测试通过。

## 私有仓库发布前检查

发布前建议确认：

```powershell
git status --short
git check-ignore .env db.sqlite3 media/ .codex-run/ external/CookLikeHOC-main/
```

提交中应只包含源码、迁移文件、模板、静态资源、文档和依赖锁定文件。

## 许可

暂未指定 License。私有仓库阶段默认保留所有权利。
