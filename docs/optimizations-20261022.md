# 賽前最後一夜的優化與量測（2026-10-22，DGX Spark，Lightning）

全部在 `main` 上，每項一個 commit，量測都在 Spark（Lightning NVFP4，vLLM nightly-77154ca，gpu util 0.20）上做；Thor 早上
`git pull --ff-only && scripts/stage-up.sh` 就會帶到。預設行為只改了 stage-up 的兩個旋鈕；其餘是可關閉的開關或等價替換。

## 改了什麼

| 項目 | 變更 | 預設 | 量測依據 |
|---|---|---|---|
| 審核代理 `scripts/reviewer.py` | `REVIEW_THINK=0` 時只跑不思考的那一輪（原本思考＋不思考兩輪） | **不變**（兩輪） | 思考輪 15.3 s（1,044 completion tokens）佔 upstream-500 34 s 的近半；code key 已經決定充分條件時，模型只需判相關／矛盾 |
| 動作工具 `scripts/agent.py` | `docker_start`／`docker_restart`／`recreate_stack` 後的 `time.sleep(2)` 改成 `settle()`：每 250 ms 輪詢兩個健康 URL，兩個都 200 就回，最多 2 s | 等價替換 | docker_start 3.1 s、docker_restart 2.4–3.3 s 牆鐘，多半是那 2 s |
| `scripts/stage-up.sh` | watchdog `WATCH_INTERVAL` 預設 2 s（watchdog 自己的預設仍 5；仍要連續兩次失敗才開事故） | **改** | 故障到 agent 第一個 token 之間最大的固定成本是 5–10 s 的偵測 |
| `scripts/stage-up.sh` | Lightning `GPU_MEMORY_UTILIZATION` 預設 0.30 → 0.25 | **改** | 權重 17.8 GiB；KV 在 0.25 仍可放 41 條完整 32k 序列（0.20 放 16 條，0.18 是地板）；無 swap 的機器多留餘裕 |
| 知識工具 | `search_incident_knowledge` 在沒有修復卡命中、症狀不屬本 stack 時，附最多兩筆 Server Fault／Unix & Linux 已接受答案摘錄（含署名與連結） | 開（`KB_COMMUNITY=0` 關） | 見 `docs/kb-community-20261022.md` |

沒做的：把 `search_incident_knowledge` 從 sandbox 的工具面拿掉（知識卡的簡短版文字會叫 agent 去呼叫它，拿掉會自相矛盾；
省下的 token 在前綴快取裡幾乎量不到）。

## 量測

### 五張牌，REVIEW_THINK=0（01:52–02:00，`logs/drills-20261022-005251.log`，watchdog 停、`APPROVAL=auto GUARDRAILS=1 KB_RETRIEVAL=v4`）

| 牌 | 注入→DRILL_DONE | 今晚稍早（兩輪審核） | 審核 ms | 備註 |
|---|---|---|---|---|
| upstream-500 | 33 s | 37 s | 1,052 | 同一套呼叫序列，recreate 由 code key 的充分條件放行 |
| upstream-500 | 65 s | — | 1,041 | 模型在 docker_ps 之後想了 32 s 才下一步（模型變異，不是審核） |
| upstream-500 | 33 s | 34 s | 1,540 | |
| nginx-stopped | 24 s | 19 s | — | 沒有需審核的動作 |
| bad-config | 30 s | 52 s | — | |

審核三次都 APPROVE，理由與兩輪版相同（「充分條件成立：上游已重啟且 /api/ 仍失敗」）。5/5 PASS。

### 五張牌，社群參考層載入（02:17–02:2x，`logs/drills-20261022-011755.log`，同上環境）

（P4，見下表；`logs/ops-api.log` 裡沒有任何 `search_incident_knowledge` 呼叫，與過去所有 drill 一致，所以這一層對舞台路徑是零影響，量測如此，不是推論。）

| 牌 | 注入→DRILL_DONE | 結果 |
|---|---|---|
| nginx-stopped | 25 s | PASS |
| upstream-paused | 35 s | PASS |
| container-removed | 21 s | PASS |
| bad-config | 33 s | PASS |
| upstream-500 | 28 s | PASS |

### 舞台路徑端到端（02:26，stage-up 之後：dashboard 注入 → watchdog 2 s 輪詢 → agent → resolved，`APPROVAL=manual`）

`POST /api/action {"action":"inject","case":"nginx-stopped"}` 到 watchdog 開事故 **4.0 s**（兩次失敗輪詢 × 2 s；舊預設 5 s 時是
10–12 s），到 `close … resolved` **18.4 s**（watchdog 自己量的事故時長 15.2 s）。agent 的呼叫：check_health → docker_ps →
docker_logs ×2 → docker_ps → check_health → docker_start，共 14 s。preflight 與今晚稍早相同：只有 `wan` FAIL（Spark 沒有免密碼
sudo，Thor 有）。

## 給隊友

- Thor 早上：`cd ~/BlackOps && git pull --ff-only && scripts/stage-up.sh`，再跑一次 `demo.md` 的流程。
- 想把審核再快 12 s：在 stage-up 的 ops-api 那行環境加 `REVIEW_THINK=0`。今晚 3/3 判決相同，但只有 upstream-500 這張牌需要審核，樣本小，所以沒有改預設。
- 評審問「runbook 以外的故障怎麼辦」：agent 問 `search_incident_knowledge` 會拿到有出處的社群修復假設（CC BY-SA，標明未在本機驗證）；可以現場用
  `.venv/bin/python scripts/kb-community.py probe "NVRM: Xid 79, GPU has fallen off the bus"` 示範。排名品質是 BM25 等級，罕見症狀可能只是同類別的弱匹配。
- 已知未修：`scripts/eval-hard-repairs.py` 的 v3 是 7/8（10/21 語料合併後就是如此，對應 `tests/agent/test_repair_knowledge.py` 的 3 個失敗）；
  survey 提到的 `permission denied` 誤推仍在（SSH 句會配到一張 K8s crashloop 卡）。
