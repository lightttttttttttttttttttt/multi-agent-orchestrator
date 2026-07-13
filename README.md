# multi-agent-orchestrator (`ma`)

Factory phần mềm multi-model chạy local, điều phối qua [9Router](http://127.0.0.1:20128).

`ma` **không** bắt một chat đóng nhiều vai.  
Mỗi stage gọi model riêng, lưu state bằng SQLite, sửa code trong git worktree tách biệt, verify bằng lệnh thật, chỉ merge khi qua đủ gate cứng.

---

## Làm được gì

```text
preflight
  → design (Sol)
  → critique (Grok)
  → judgment (Sol)          # sinh task DAG JSON
  → implement (DeepSeek/GLM) # worktree theo task, chạy song song theo wave
  → verify                  # test máy thật
  → audit (Sol)             # phải bắt đầu bằng APPROVE
  → report (Gemini)
  → merge/push tùy chọn     # có thể bắt human approve
```

### Điểm chính

- **Gọi model tách request theo role** qua 9Router local
- **Timeout 15s × 3**, rồi fallback / fail cứng
- **Blacklist model chết** trong cùng 1 run
- **Task DAG** có `allowed_files`, `depends_on`, `verify_command`
- **Git worktree theo task** + file lock (kể cả cross-process)
- **Worker song song** (`--workers 0` auto tối đa 4)
- **Secret scan** trước khi apply/merge
- **Budget** (`--max-calls`, `--max-tokens`)
- **Usage/cost ledger** (ưu tiên token provider, fallback ước lượng)
- **Telegram notify** khi fail
- **Event stream** + `watch` / `cancel` hard (kill PID)
- **Human approval** trước merge/push
- **Job queue** multi-worker (`enqueue` / `worker`)
- **GitHub Action** self-hosted sẵn template

---

## Yêu cầu

- Python **3.11+**
- Git
- **9Router** local tại `http://127.0.0.1:20128`
- Windows / Linux / macOS (hard cancel trên Windows dùng `taskkill`)

Tuỳ chọn:
- Biến môi trường Telegram để báo fail
- `MA_QUEUE_TOKEN` cho queue multi-worker
- Self-hosted GitHub runner gắn label `ma`

---

## Cài đặt

```bash
git clone https://github.com/lightttttttttttttttttttt/multi-agent-orchestrator.git
cd multi-agent-orchestrator
python -m pip install -e .
ma doctor --no-probe
```

- Package: `quang-multi-agent`
- CLI: `ma`

---

## Dùng nhanh

```bash
# kiểm tra hệ thống
ma doctor

# ship 1 phát
ma ship C:/path/to/repo "Thêm subtract(a, b) vào calc.py" \
  --verify "python -m unittest -v" \
  --workers 0

# terminal khác: xem progress
ma watch PROJECT_ID --follow

# huỷ cứng (kill process tree trên Windows)
ma cancel PROJECT_ID

# huỷ mềm (chỉ chặn giữa các stage)
ma cancel PROJECT_ID --soft
```

### Merge an toàn

```bash
# cần Sol APPROVE; thêm human approval nếu bật flag này
ma ship C:/path/to/repo "goal" --verify "pytest -q" --merge --require-approval

# cấp quyền human, rồi merge
ma approve PROJECT_ID
ma merge PROJECT_ID --require-approval

# push cũng bắt approval
ma merge PROJECT_ID --push --require-approval
```

---

## Bản đồ lệnh

| Lệnh | Việc |
|---|---|
| `ma doctor` | Preflight 9Router / key / git / `~/.ma` |
| `ma ship` | Chạy full pipeline |
| `ma init` / `run` / `status` / `show` | Vòng đời project |
| `ma implement` / `verify` / `audit` | Chạy stage thủ công |
| `ma merge` | Fast-forward sau APPROVE |
| `ma watch` | Xem event JSONL |
| `ma cancel` | Huỷ soft/hard |
| `ma approve` | Human gate cho merge/push |
| `ma report` | Xuất report markdown/json |
| `ma usage` | Xem token/cost |
| `ma clean` | Dọn worktree |
| `ma enqueue` / `worker` / `queue` / `job` | Queue multi-worker |

```bash
ma --help
```

---

## Route model mặc định

| Stage | Primary | Fallback |
|---|---|---|
| design / judgment / audit | `Ntt_Codex10tr/gpt-5.6-sol` | `nttcodex/gpt-5.6-sol` |
| critique | `nttcodex/grok-4.5-high` | Sol |
| implementation | `nttcodex/deepseek-v4-pro` | `nttcodex/glm-5.2` |
| report | `gemini/gemini-3-flash-preview` | Gemini 2.5 → Sol |

Mỗi call: **timeout 15s, tối đa 3 lần**, rồi nhảy fallback.  
HTTP 200 mà content rỗng = fail.

Cấu hình nằm ở `ma/defaults.py`.

---

## Gate an toàn (không bỏ qua)

1. **Preflight** — 9Router up, API key, git
2. **Content rỗng** bị chặn
3. Worker implement chỉ được trả **unified diff**
4. **Secret scan** trên patch/diff
5. Chỉ đụng file trong **`allowed_files`**
6. **Verify máy** phải exit code `0`
7. **Sol audit** phải bắt đầu bằng `APPROVE`
8. **Budget** có thể dừng run
9. **Human approval** khi `--require-approval` / `--push`
10. **Không auto-merge mặc định** — merge phải bật tay

`ma` không được bịa test xanh hay claim success khi chưa có output tool.

---

## Format task DAG

Stage judgment nên kết thúc bằng JSON array:

```json
[
  {
    "id": "T_CALC",
    "goal": "Thêm subtract(a, b) trả về a - b",
    "allowed_files": ["calc.py"],
    "verify_command": "python -m unittest -v",
    "depends_on": []
  },
  {
    "id": "T_TEXT",
    "goal": "Thêm helper whisper(s)",
    "allowed_files": ["textutil.py"],
    "verify_command": "python -m unittest -v",
    "depends_on": []
  }
]
```

Quy tắc:
- Task độc lập, không trùng file → chạy song song theo wave
- Trùng `allowed_files` hoặc có dependency → wave sau
- Patch đụng file ngoài scope → fail

---

## State & artifact

| Path | Nội dung |
|---|---|
| `~/.ma/state.sqlite` | Project, stage, task, evidence |
| `~/.ma/events/<id>.jsonl` | Event progress |
| `~/.ma/events/<id>.pid` | PID process ship |
| `~/.ma/events/<id>.cancel` | Marker huỷ |
| `~/.ma/approvals/` | File human approval |
| `~/.ma/queue.sqlite` | Job queue |
| `~/.ma/usage.sqlite` | Token/cost |
| `~/.ma/rates.json` | Override giá USD/1M token |
| `~/.ma/file_locks.sqlite` | File lock cross-process |
| `<repo>/.ma/reports/<id>.md` | Report sau ship |
| worktree sibling | `*-ma-<project>*` |

---

## Queue multi-worker

```bash
# auth tuỳ chọn
export MA_QUEUE_TOKEN=super-secret
# hoặc ghi ~/.ma/queue.token

ma enqueue C:/path/to/repo "goal" --verify "pytest -q" --token "$MA_QUEUE_TOKEN"
ma worker --once --token "$MA_QUEUE_TOKEN"
ma queue
ma job JOB_ID
```

Nếu đã set token thì enqueue/claim/complete bắt buộc đúng token.  
Chưa set = open mode local.

---

## GitHub Action (self-hosted)

Workflow: [`.github/workflows/ma-ship.yml`](.github/workflows/ma-ship.yml)

Dùng **self-hosted Windows runner** với labels:

```text
[self-hosted, Windows, ma]
```

để runner chạm được 9Router local (`localhost:20128`).

Hỗ trợ:
- `workflow_dispatch` với goal / verify / merge
- PR title làm goal

---

## Flag ship hay dùng

```bash
ma ship REPO "goal" \
  --verify "pytest -q" \
  --workers 0 \
  --max-calls 40 \
  --max-tokens 200000 \
  --max-replans 1 \
  --project-id EXISTING_ID \
  --merge \
  --require-approval \
  --push
```

| Flag | Ý nghĩa |
|---|---|
| `--workers 0` | Auto worker theo wave (≤ 4) |
| `--max-calls` | Giới hạn số call |
| `--max-tokens` | Giới hạn token |
| `--max-replans` | Số lần Sol replan khi task fail |
| `--project-id` | Resume project cũ |
| `--merge` | Merge sau APPROVE |
| `--require-approval` | Cần `ma approve` trước |
| `--push` | Push sau merge (kéo theo approval) |

---

## Cost / usage

```bash
ma usage
ma usage PROJECT_ID
```

- Ưu tiên `usage.prompt_tokens` / `completion_tokens` từ provider
- Không có thì ước lượng `chars/4`
- Sửa giá ở `~/.ma/rates.json`

Ví dụ:

```json
{
  "default": {"in": 0.5, "out": 1.5},
  "sol": {"in": 5.0, "out": 15.0},
  "deepseek": {"in": 0.3, "out": 0.8},
  "glm": {"in": 0.4, "out": 1.0},
  "grok": {"in": 2.0, "out": 6.0},
  "gemini": {"in": 0.2, "out": 0.6}
}
```

Cost là **ước lượng** nếu provider không trả usage thật.

---

## Test

```bash
python -m unittest discover -s tests -v
```

Suite cover: retry router, gate, DAG wave, lock, secret, usage, queue auth, approval, event/cancel, worktree, ops.

---

## Quan điểm thiết kế

- Model mạnh quyết định / audit
- Model rẻ viết diff có scope
- Máy verify
- Người approve merge/push khi cần
- Cấm success rỗng
- Fail thì báo, dừng, không liều

Đây là **software factory local**, không phải chat toy.

---

## Trạng thái

Tool cá nhân để ship code multi-model qua 9Router local.  
Còn edge: planning prompt dài, rate-limit provider, setup self-hosted runner.

---

## Mental model gọn

```text
Sol lên plan + chấm + audit
Grok red-team
DeepSeek/GLM code trong worktree riêng
pytest/unittest chốt sự thật
ma chỉ ship khi qua gate
```
