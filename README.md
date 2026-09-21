# 国开行智能客户分析 Agent

两层结构的智能客户分析 Agent：上层 3 个问题工作流（Q1 单客户诊断 / Q2 名单+融资 / Q3 关联图谱），下层 10 个原子能力（C1..C10，统一 Provider 接口）。数据本期走 MockProvider，未来一键切换 DBProvider，业务层零改动。

详见 [开发计划.md](开发计划.md)。

---

## M0 框架地基（已交付）

本里程碑只做**可运行的骨架**，不含具体取数与业务编排：

- **LLM 客户端** `backend/llm/client.py` —— vLLM / OpenAI 兼容，chat / stream / 思考开关 / 重试
- **Provider 抽象 + registry** `backend/capabilities/` —— C1..C10 接口契约，`mock` / `db` 按 `.env` 分发
- **FastAPI 骨架** `backend/app.py` —— `/health`、`/llm/ping` 等端点

### 目录

```
backend/
├── app.py                    # FastAPI 入口
├── config.py                 # .env 配置（pydantic-settings）
├── llm/client.py             # Qwen 兼容 LLM 客户端
└── capabilities/
    ├── base.py               # Provider 抽象 + DTO
    ├── registry.py           # mock/db 分发
    ├── mock_provider.py      # 读 mockdata/*.yaml（M1 落地假数据）
    └── db_provider.py        # 接库契约（本期留 TODO）
tests/test_smoke.py           # 不外呼 LLM 的装配自检
```

---

## 启动（服务器 `/data/liuqingting/guokahang/`）

```bash
# 1. 依赖
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置（首次）
cp .env.example .env          # 按需改 LLM_BASE_URL / LLM_MODEL / 端口

# 3. 冒烟自检（不外呼 LLM）
python -m pytest tests/ -v

# 4. 起服务
python -m backend.app
```

### 验证端点

```bash
# 本地就绪度（不打 LLM）
curl http://127.0.0.1:8000/health

# LLM 连通（真实打一次 vLLM）
curl http://127.0.0.1:8000/llm/ping

# 非流式直通
curl -X POST http://127.0.0.1:8000/llm/echo \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"你好，用一句话自我介绍"}'

# SSE 流式
curl -N -X POST http://127.0.0.1:8000/llm/stream \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"讲个一句话笑话"}'
```

`/health` 返回 `status:ok` 且 `/llm/ping` 返回 `ok:true` 即 M0 跑通。

---

## 配置项（`.env`）

| 键 | 默认 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `http://10.200.3.10:8025/v1` | vLLM OpenAI 兼容地址 |
| `LLM_MODEL` | `/data/gc_llm/nlp/Qwen3.6-27B` | 模型路径 |
| `LLM_MAX_TOKENS` | `2048` | 推理模型需给足，否则正文被思考截断 |
| `LLM_TEMPERATURE` | `0.3` | 抽取低温；综合建议调用方可上调 |
| `LLM_TIMEOUT` / `LLM_RETRIES` | `120` / `2` | analyze 超时与重试 |
| `PROVIDER_MODE` | `mock` | `mock` \| `db`，registry 分发 |
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8000` | 服务监听 |
