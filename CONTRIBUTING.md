# 贡献与开发规范

本项目采用 uv 管理 Python 环境，Django 作为 Web 框架。提交代码前请确保本地检查通过，并尽量让业务逻辑沉到服务层，视图只负责请求解析、权限、消息和响应。

## 本地开发

```powershell
uv sync --dev
uv run python manage.py migrate
uv run python manage.py runserver 127.0.0.1:8000
```

首次运行需要创建账号：

```powershell
uv run python manage.py createsuperuser
```

## 提交前检查

```powershell
uv run python manage.py check
uv run python manage.py test tests
uv run ruff check .
```

## 代码约定

- 环境差异通过 `.env` 管理，新增配置要同步更新 `.env.example`。
- 不在代码中写死生产密钥、域名、数据库密码或本机绝对路径。
- 涉及订单、统计、食材累计量的副作用统一放在 `apps.orders.services`。
- Django 视图保持薄层：参数解析、调用服务、返回模板或重定向。
- 数据库结构变更必须提交迁移文件。
- 删除、停用、批量操作只能通过 POST 执行。
- 新增业务规则需要补充测试，至少覆盖正常路径和一个边界路径。
- 外部数据源同步逻辑应保持幂等，重复执行不应产生重复菜谱。

## 测试策略

- 认证和权限：确认未登录用户会被重定向。
- 数据一致性：订单保存、删除、统计更新必须同时验证。
- OCR parser：新增识别规则时补充最小输入样例。
- 菜谱同步：新增解析规则时使用临时目录构造 Markdown 样例。
