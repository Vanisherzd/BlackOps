# 三條分支整合：推論改用 vLLM（2026-10-20）

整合分支：`integrate-vllm`，目標是 `main`。

決定：**推論改用 vLLM，不再用 TensorRT-LLM**。依據是團隊在 2026-10-20 的溝通。agent、NemoClaw、真實工具、安全審核、知識庫、demo 外殼全部保留。

## 1. 三條分支各自帶進來什麼

| 分支 | 內容 | 在整合版的角色 |
|---|---|---|
| `kb-retrieval-v2`（vanli） | agent 迴圈、NemoClaw 路徑、20 個真實工具、guards 與三把鑰匙、KB v3/v4、shim 的健康保護網與分情況思考、計分卡與 28 題故障 | **主幹**，原封不動 |
| `feat/blackbox-bolt-vllm` | `deploy/vllm/`（Bolt 74B-A9B 的 vLLM 映像與啟動腳本）、`blackbox/`（推論 gateway、mock 工具、最小 agent 迴圈、benchmark）、`tests/`（33 個單元測試） | **推論層**用 `deploy/vllm/`；`blackbox/` 留作評測與單元測試工具 |
| `demo-infra`（我們） | dashboard、watchdog、審批、Tier、功耗、歸檔、斷網、看板、夜跑 | demo 外殼，原封不動 |

git：`integrate-vllm` 從 `demo-infra` 分出，`demo-infra` 已包含 `kb-retrieval-v2` 到 48b16f9 為止的內容。merge `feat/blackbox-bolt-vllm` 時只有 `.gitignore` 衝突，兩邊的內容都保留。bolt 分支沒有改動 `scripts/` 底下任何檔案。

## 2. 換了什麼

```
之前：NemoClaw → oai_shim :8001 → trtllm-serve :8000（Nano-30B-A3B-NVFP4）
之後：NemoClaw → oai_shim :8001 → vLLM :8000（Bolt-74B-A9B, served as "edge-agent"）
```

- `scripts/serve-vllm.sh`：新的預設推論啟動腳本。
  - 它呼叫 `deploy/vllm/start-bolt.sh`，覆寫三個設定：容器名稱改成 `vllm-edge`、模型名稱改成 `edge-agent`、port 改成 **8000**、`MAX_NUM_SEQS=2`。
  - 所有既有的客戶端原本就打 `:8000/edge-agent`，所以 shim、agent.py、reviewer.py、kb-eval、preflight、NemoClaw **一行都不用改**。
  - `trtllm-edge` 還在跑時，它會拒絕啟動，因為兩個推論服務放不進同一塊記憶體。
- `deploy/vllm/start-bolt.sh`：在 Jetson 上自動改用 `--runtime nvidia`，Spark 上照舊用 `--gpus all`。
- `scripts/serve-tier1.sh`：Tier-1 也改用 vLLM，開在 `:8010`，預設占 0.12 的記憶體。
- `config/inference.env.example`：gateway 預設改成指向整合版的推論服務（`:8000/edge-agent`）。bolt 分支自己的獨立 baseline（`make bolt-up`）仍然是 `:8200/bolt`。
- **legacy，保留不刪**：`scripts/serve-trt.sh`、`trt/nano*.yaml`。到目前為止已發表的數據，都是在這套 TRT 上量的：計分卡、四批盲測、剪枝、壓縮曲線。
- **移除**：`demo-infra` 上的 TRT Thor 設定 `trt/thor-*.yaml`、`trt/tier1.yaml`，從沒用過。

### 為什麼 shim 保留

- shim 的 TRT 專屬處理（訊息正規化、tool call salvage）在 vLLM 上沒有壞處，也繼續提供兩項 vLLM 沒有的功能：健康保護網（`189de86`），以及分情況開關思考。
- 10/17 vanli 已經實際讓 shim 接 Bolt（`ef4e6c4`），結果見過的題目 20/20、盲測 8/15；Nano 是 29/30、6/15。

## 3. 已驗證／未驗證

| 項目 | 狀態 |
|---|---|
| merge 後 33 個單元測試（mock server） | ✅ 33/33 |
| 各 Python 腳本可以編譯 | ✅ |
| Spark 上用 `serve-vllm.sh` 啟動 Bolt 並通過 `/health` | ✅ 約 5 分鐘到 healthy；smoke OK（2026-10-20 23:50） |
| Bolt 加上整套 agent，host 迴圈，9 案 × 3 | ✅ **26/27**（唯一失敗是 upstream-detached 第 1 輪；這案在 TRT 上兩個版本也都失敗過） |
| Bolt 加上整套 agent，**NemoClaw 路徑**，5 張舞台卡 × 3 | ✅ **15/15 autonomous**，made-worse 0、謊報 0、evidence-first 100%、closure 100%（`~/edge-agent/logs/drills-20261021-003040.log`） |
| vllm 分支的 tool_eval，打 :8000 | 26/27，跟組員 10/17 在 :8200 量到的結果一樣；只錯 `unsupported_rotate_cert`（遇到不支援的請求時沒有好好結束）。腳本回報 FAIL，是因為它把「沒有全部通過」視為失敗 |
| 分情況思考、健康保護網在 Bolt 上的效果 | ⬜ 原本是在 Nano/TRT 上量的 |
| Thor 上跑 vLLM | ⬜ Bolt 映像是為 GB10（sm121）建的，Thor 能不能跑未知；退路是 jetson-containers 的 vLLM |
| 64 GB Thor 放不放得下 Bolt | ⬜ 權重 24 GB；放不下就改用 Nano-30B-A3B-NVFP4 跑在 vLLM 上 |

### 舞台卡的中位時間：Bolt 與 Nano 比較（NemoClaw 路徑）

| 卡 | Bolt／vLLM（2026-10-21，每卡 3 次） | Nano／TRT（`docs/scorecard.md`） |
|---|---|---|
| nginx-stopped | 40 s | 44 s |
| container-removed | 31 s | 30 s |
| upstream-paused | 46 s | 46 s |
| bad-config | 66 s | 79 s |
| upstream-500 | **106 s** | 48–65 s |

upstream-500 變慢：Bolt 在重啟之後、重建之前，多查了幾步。第二幕有 2 分鐘，加上人工核准仍然放得下，但比較緊。可以考慮開 `SHIM_ADAPTIVE=1 TRIAGE=1` 再量一次。驗證的完整輸出在 `~/edge-agent-demo/logs/validate-vllm-20261020-235009/`。

## 4. 對簡報和評分的影響（要改的地方）

- 企劃書 §4.6 把 **TensorRT-LLM** 列為「核心」整合項目，§8.3 表四是「Ollama vs TensorRT-LLM／NIM」。換成 vLLM 之後：
  - 整合表改成 Nemotron（Bolt 74B-A9B 加小模型）、NemoClaw／OpenShell、NeMo Guardrails（如果有用）。推論引擎寫 vLLM，說明「推論後端可替換：TensorRT-LLM 為早期基準，vLLM 為部署版」。
  - 表四可以直接改成「TRT-LLM + Nano vs vLLM + Bolt」。兩邊都有實測數據：vanli 10/17 的同一組 drill、Bolt 分支的 perf 數據（C=1 時 TTFT 742 ms、32.8 tok/s）。
- **MoE 論述更強**：Bolt 是 74B 總參數、每個 token 只啟用 9B，比 Nano 的 30B/3B 更能說明「大模型當突發負載」。
- 揭露頁：bolt 分支的推論層是賽前成果，要列進去。

## 5. 接下來

1. ✅ 驗證完成，無退步（見 §3）。Bolt 目前以 `vllm-edge` 這個容器名稱、`edge-agent` 這個模型名稱，留在 :8000 上服務。
2. 開 PR：`integrate-vllm` → `main`。
3. 用 Bolt 重新量盲測與分情況思考。原本的數據是在 Nano 上量的。
3. 10/21 在 Thor 上驗證 vLLM。
