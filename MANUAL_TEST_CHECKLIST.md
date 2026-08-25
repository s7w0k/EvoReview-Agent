# EvoAgent 页面实测清单与流程

- 文档日期：2026-08-13（v2：扩充全场景测试用例与真实用户用例）
- 适用版本：WP1–WP10 全部完成后的当前代码
- 前置条件：自动化验证全部通过（见 `TEST_RESULTS.md` 第五节）

## 一、启动方式

默认启动（规则层审查）：

```powershell
cd d:\研究生\1\project\EvoAgent
python -m evoagent
```

开启语义层（AST 污点分析，覆盖 `SEM-*` 规则；默认暗开关关闭）：

```powershell
$env:EVOAGENT_STATIC_ANALYZER="ast"
python -m evoagent
```

浏览器打开 **http://127.0.0.1:8080/**（默认无需登录，`auth_required=false`；页面 6 个 Tab：运行总览 / 发起审查 / 任务中心 / Trace 回放 / Skills / 演进实验室）。

> 提示：用例 2、6、7 与场景 B、E、流程 4 涉及语义层 `SEM-*` 规则，需以"开启语义层"方式启动才能检出；其余用例两种模式均可命中。

## 二、页面实测清单

### 0. 准备阶段

- [ ] 服务启动无报错，控制台出现监听日志
- [ ] 打开页面，侧边栏"系统状态"显示已连接
- [ ] 若报 404/白屏，检查端口是否被占用

### 1. 运行总览

- [ ] 6 个统计卡片加载出数字（任务数、风险分布、误报率等）
- [ ] "最近任务"列表显示已提交的任务
- [ ] Agent 协作链：Security/Reliability 为绿色，LLM Agent 显示"未配置"（未配 `EVOAGENT_LLM_*` 时正常）

### 2. 发起审查（核心功能）

- [ ] 粘贴"用例 7（综合风险）"，不勾"放入异步队列"，提交 → 检出多条 finding，风险 high
- [ ] 再提交一次，勾选"异步队列" → 提示已入队
- [ ] 提交空 diff → 表单校验拦截（必填）
- [ ] 提交"用例 8（干净）" → 结论为"未发现可操作问题"，风险 low

### 3. 任务中心

- [ ] 任务列表能看到全部任务（SUCCESS/PENDING）
- [ ] 点击任务 → 右侧渲染 Markdown 报告（含 findings、Agent 协作链数据）
- [ ] 对 SUCCESS 且有可修复 finding 的任务，点击**创建修复分支** → 返回分支名（`evoagent/fix-pr-*`）
- [ ] 反馈表单：选"误报"+ 关联某条 finding + 填写说明 → 提交成功
- [ ] 再试一次"漏报"，填写规则 ID/文件/行号 → 提交成功，页面出现反馈历史
- [ ] "坏修复"类型同样可提交

### 4. Trace 回放

- [ ] 进入 Trace 回放 Tab，任务下拉自动加载并选中首个任务
- [ ] 左侧"协作摘要"显示协议/对话轮次/消息数/重试、Agent 状态、Finding 来源
- [ ] 右侧"时间线"显示状态机事件（PLANNING→EXECUTING→REVIEWING→SUCCESS）+ Agent 协作消息
- [ ] 点击任意消息条目可展开查看完整 JSON，可收起

### 5. Skills

- [ ] LLM Review Agent 卡片显示 Provider 状态（未配置显示"未配置"）
- [ ] 动态 Skills 卡片列表渲染（名称/版本/来源）
- [ ] 点"重新扫描 Skills" → 列表刷新，控制台无异常

### 6. 演进实验室

- [ ] "评测就绪状态"显示（未配 LLM 时通常显示待配置/deferred，属预期）
- [ ] 在任务中心提交过反馈后，点**从反馈生成候选** → 出现结果提示
- [ ] 提交候选提示词（skill_name=llm-review）→ 回放评测结果展示
- [ ] 失败案例列表渲染

### 7. 收尾

- [ ] 服务 Ctrl+C 停止，控制台正常退出无堆栈

## 三、分场景测试用例（覆盖全部规则）

### 规则覆盖对照

| 用例 | 目标规则 | 层级 | 是否可自动修复 |
|---|---|---|---|
| 用例 1 | SEC-EVAL | 规则层 | 否 |
| 用例 2 | SEC-SUBPROCESS-SHELL + SEM-SHELL-INJECTION + SEM-TAINTED-SUBPROCESS | 规则层+语义层* | shell 可修复 |
| 用例 3 | SEC-HARDCODED-SECRET（×2） | 规则层 | 可修复 |
| 用例 4 | SEC-SQL-CONCAT | 规则层（需单行拼接） | 否 |
| 用例 5 | REL-EMPTY-EXCEPT + REL-DEBUG-PRINT | 规则层 | debug print 可修复 |
| 用例 6 | SEM-UNCLOSED-RESOURCE | 语义层*（规则层漏报） | 否 |
| 用例 7 | SEC-EVAL + SEC-HARDCODED-SECRET + SEC-SUBPROCESS-SHELL + SEM-TAINTED-SUBPROCESS + SEM-UNCLOSED-RESOURCE | 多层 | 部分可修复 |
| 用例 8 | 干净样本 | — | — |

> *语义层规则需 `EVOAGENT_STATIC_ANALYZER=ast` 开启（见"一、启动方式"）。

### 用例 1：动态代码执行注入（eval）

预期：`SEC-EVAL` critical，风险 high。

```diff
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,4 +1,10 @@
 import json
 
+def execute(request):
+    expression = request.get("expr")
+    result = eval(expression)
+    return {"result": result}
+
 def handler(event):
     return json.dumps(event)
```

### 用例 2：Shell 命令拼接注入

预期：`SEC-SUBPROCESS-SHELL`（规则层）+ `SEM-SHELL-INJECTION`/`SEM-TAINTED-SUBPROCESS`（语义层*），风险 high。

```diff
diff --git a/deploy.py b/deploy.py
--- a/deploy.py
+++ b/deploy.py
@@ -1,3 +1,10 @@
 import subprocess
 
+def deploy(branch):
+    command = "git push origin " + branch
+    subprocess.run(command, shell=True)
+
 def tag():
     return "v1.0"
```

### 用例 3：硬编码凭据

预期：`SEC-HARDCODED-SECRET`（×2）high，**可创建修复分支**。

```diff
diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -1,3 +1,10 @@
 import os
 
+PASSWORD = "s3cr3t-p@ss"
+API_KEY = "sk-1234567890abcdef"
+
 def load():
     return os.environ.get("APP_ENV", "dev")
```

### 用例 4：SQL 字符串拼接注入

预期：`SEC-SQL-CONCAT` high。注意需**单行**内 `execute(...)` 直接拼接（规则逐行匹配，多行拆分不会命中）。

```diff
diff --git a/search.py b/search.py
--- a/search.py
+++ b/search.py
@@ -1,5 +1,12 @@
 import sqlite3
 
+def query(keyword):
+    return sqlite3.connect("db.sqlite").execute("SELECT * FROM items WHERE name = '" + keyword + "'")
+
 def index():
     return "ok"
```

### 用例 5：裸 except 吞异常 + 调试输出

预期：`REL-EMPTY-EXCEPT` medium + `REL-DEBUG-PRINT` low（**可修复**）。

```diff
diff --git a/worker.py b/worker.py
--- a/worker.py
+++ b/worker.py
@@ -1,3 +1,11 @@
 import logging
 
+def process(payload):
+    try:
+        result = payload["data"]
+    except:
+        print("failed to process")
+        result = None
+    return result
+
 def main():
     logging.info("start")
```

### 用例 6：文件句柄未关闭（仅语义层命中）

预期：`SEM-UNCLOSED-RESOURCE` medium（语义层*，规则层无法检出，验证语义层能力）。

```diff
diff --git a/upload.py b/upload.py
--- a/upload.py
+++ b/upload.py
@@ -1,3 +1,10 @@
 import os
 
+def save(blob):
+    handle = open("/tmp/upload.bin", "wb")
+    handle.write(blob)
+    return os.path.getsize("/tmp/upload.bin")
+
 def cleanup():
     return True
```

### 用例 7：综合高风险（全场景一例覆盖）

预期：`SEC-EVAL` + `SEC-HARDCODED-SECRET` + `SEC-SUBPROCESS-SHELL`（规则层）+ `SEM-TAINTED-SUBPROCESS` + `SEM-UNCLOSED-RESOURCE`（语义层*），共 5 条，风险 high。

```diff
diff --git a/runner.py b/runner.py
--- a/runner.py
+++ b/runner.py
@@ -1,3 +1,13 @@
 import subprocess
 
+TOKEN = "ghp_xxx123456789"
+
+def exec_code(code):
+    return eval(code)
+
+def run(cmd):
+    subprocess.run("sh -c " + cmd, shell=True)
+
+def read_log():
+    f = open("/var/log/app.log")
+    return f.read()
```

### 用例 8：干净样本（不应产生 finding）

预期：无 finding，风险 low，摘要为"未发现可操作问题"。

```diff
diff --git a/utils.py b/utils.py
--- a/utils.py
+++ b/utils.py
@@ -1,4 +1,9 @@
 import re
 
+def is_valid_name(value):
+    return bool(re.fullmatch(r"[a-z0-9_-]+", value))
+
 def normalize(text):
     return text.strip().lower()
```

## 四、真实用户用例（模拟真实 PR 场景）

每个用例请按"发起审查 → 任务中心查看 → （按需）Trace 回放"完整走一遍。

### 场景 A：用户注册接口新增动态执行（认证模块）

- 背景：开发者为快速实现"表达式计算"功能，在注册回调里直接 `eval` 请求参数。
- 测试重点：应检出 `SEC-EVAL` critical；在 Trace 回放中确认 security-agent 提出该 finding 且证据链完整。

```diff
diff --git a/api/auth.py b/api/auth.py
--- a/api/auth.py
+++ b/api/auth.py
@@ -1,4 +1,11 @@
 from flask import request
 
+def register():
+    data = request.get_json()
+    bonus = eval(data["bonus_rule"])
+    return {"status": "ok", "bonus": bonus}
+
 def login():
     return "login"
```

### 场景 B：CI 部署脚本拼接 shell 命令

- 背景：部署脚本用字符串拼接构造 `git push` 命令。
- 测试重点：规则层 `SEC-SUBPROCESS-SHELL` 与语义层 `SEM-SHELL-INJECTION`/`SEM-TAINTED-SUBPROCESS`（需开启语义层）是否同时出现；同位置 finding 是否被聚类合并为一条。

```diff
diff --git a/ci/deploy.py b/ci/deploy.py
--- a/ci/deploy.py
+++ b/ci/deploy.py
@@ -1,3 +1,10 @@
 import subprocess
 
+def push(branch_name):
+    script = "git push origin " + branch_name
+    subprocess.call(script, shell=True)
+
 def build():
     return True
```

### 场景 C：数据库密码硬编码（可修复路径）

- 背景：临时调试将生产库密码写死进配置。
- 测试重点：检出 `SEC-HARDCODED-SECRET` 后，任务中心点**创建修复分支**，确认返回 `evoagent/fix-pr-*` 分支名。

```diff
diff --git a/settings/db.py b/settings/db.py
--- a/settings/db.py
+++ b/settings/db.py
@@ -1,3 +1,10 @@
 import os
 
+DATABASE_URL = "postgres://admin:prodpass123@db.internal:5432/orders"
+
 def connect():
     return os.environ.get("DATABASE_URL", DATABASE_URL)
```

### 场景 D：搜索接口 SQL 拼接

- 背景：搜索功能直接拼接用户关键词进 SQL。
- 测试重点：检出 `SEC-SQL-CONCAT`（需单行内 execute 直接拼接）；提交"误报"反馈并关联该 finding，验证反馈进入演进实验室候选队列。

```diff
diff --git a/services/search.py b/services/search.py
--- a/services/search.py
+++ b/services/search.py
@@ -1,4 +1,11 @@
 import psycopg
 
+def search_items(term):
+    return psycopg.connect("db").execute("SELECT * FROM items WHERE title LIKE '%" + term + "%'")
+
 def recent():
     return []
```

### 场景 E：文件上传句柄泄漏

- 背景：上传处理打开文件后未关闭，可能耗尽文件描述符。
- 测试重点：验证**语义层**独立检出 `SEM-UNCLOSED-RESOURCE`（需开启语义层；规则层无此能力）。

```diff
diff --git a/handlers/upload.py b/handlers/upload.py
--- a/handlers/upload.py
+++ b/handlers/upload.py
@@ -1,3 +1,10 @@
 import os
 
+def store_upload(chunk):
+    fh = open("/data/chunks/tmp.bin", "ab")
+    fh.write(chunk)
+    return len(chunk)
+
 def list_files():
     return os.listdir("/data")
```

### 场景 F：异常处理被删除导致静默失败

- 背景：重构时把具体异常换成裸 `except`，并加 print 调试。
- 测试重点：检出 `REL-EMPTY-EXCEPT` + `REL-DEBUG-PRINT`；对 debug print 尝试修复分支。

```diff
diff --git a/workers/job.py b/workers/job.py
--- a/workers/job.py
+++ b/workers/job.py
@@ -1,4 +1,12 @@
 import json
 
+def run_job(message):
+    try:
+        job = json.loads(message["payload"])
+    except:
+        print("parse failed")
+        return None
+    return job
+
 def ack():
     pass
```

### 场景 G：调试日志泄露 Token

- 背景：调试期间把完整 Token 打印到日志。
- 测试重点：检出 `REL-DEBUG-PRINT`；在任务中心提交"漏报"反馈（补充规则 `SEC-TOKEN-LEAK`），验证演进实验室可生成候选。

```diff
diff --git a/middleware/auth_token.py b/middleware/auth_token.py
--- a/middleware/auth_token.py
+++ b/middleware/auth_token.py
@@ -1,4 +1,10 @@
 import logging
 
+def attach(headers):
+    token = headers.get("Authorization")
+    print("token =", token)
+    return {"Authorization": token}
+
 def verify():
     return True
```

### 场景 H：干净的重构 PR（不触发规则）

- 背景：抽取公共校验函数，无风险。
- 测试重点：应零 finding、low risk，确认无"误报"。

```diff
diff --git a/lib/validate.py b/lib/validate.py
--- a/lib/validate.py
+++ b/lib/validate.py
@@ -1,4 +1,9 @@
 import re
 
+def email_valid(value):
+    return bool(re.match(r"[^@]+@[^@]+\.[^@]+$", value))
+
 def phone_valid(value):
     return value.isdigit()
```

## 五、全功能串联测试流程（真实用户路径）

### 流程 1：审查闭环 + Trace 回放（核心链路）

1. 发起审查：粘贴**用例 7（综合）**，不勾异步，提交。
2. 任务中心：点开任务，确认报告含 5 条左右 finding（开启语义层时）、风险 high、协作链数据（7 角色 16 消息）。
3. Trace 回放：选中该任务，确认时间线 20 条左右（4 事件 + 16 消息），展开 ASSIGNMENT/ARBITRATION_DECISION 查看 JSON。
4. 运行总览：确认统计卡片、最近任务、Agent 协作链更新。

### 流程 2：异步 + 修复分支 + 坏修复反馈

1. 发起审查：粘贴**用例 3（硬编码凭据）**，勾选"异步队列"，提交。
2. 等待任务转 SUCCESS（刷新任务中心）。
3. 点**创建修复分支**，确认返回 `evoagent/fix-pr-*` 分支名与修复规则列表。
4. 对修复结果提交"坏修复"反馈并关联该 finding，确认进入演进候选队列。

### 流程 3：反馈 → 演进闭环

1. 用**场景 D（SQL 拼接）**提交审查。
2. 任务中心对该 finding 提交"误报"反馈（填写说明）。
3. 演进实验室：点**从反馈生成候选**，确认结果提示。
4. 提交候选提示词（skill_name=llm-review），确认回放评测结果展示；失败案例列表出现刚提交的反馈。

### 流程 4：语义层独立能力验证

> 前置：以"开启语义层"方式启动服务（见"一、启动方式"）。

1. 用**用例 6（未关闭资源）**提交审查。
2. 确认报告含 `SEM-UNCLOSED-RESOURCE`，且该 finding 来源为语义层（analyzer=ast）。
3. Trace 回放：确认 finding_sources 归属正确。

### 流程 5：干净样本与误报防护

1. 用**用例 8（干净）**提交审查 → 零 finding。
2. 用**场景 H（干净重构）**提交审查 → 零 finding。
3. Skills 页点"重新扫描"；运行总览确认成功率统计未异常。

## 六、可选：旁路 API 验证（PowerShell）

```powershell
# 健康检查
Invoke-RestMethod http://127.0.0.1:8080/health/ready

# 异步提交
Invoke-RestMethod -Method Post http://127.0.0.1:8080/v1/reviews?async=true `
  -ContentType "application/json" `
  -Body '{"repository":"acme/demo-app","diff":"--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-ok\n+eval(x)","pull_request":1}'
```

## 七、验收标准

- 上述清单勾选项全部通过即视为页面实测完成。
- 用例 1–8 的预期规则与实际检出一致（至少命中目标规则，允许附带相关 finding）。
- 任一勾选项失败时，记录复现步骤、浏览器控制台报错与服务端日志后反馈。
