# Thor 接手清單（2026-10-22 09:45，Spark 這邊暫停）

## 1. 拉程式

```bash
cd ~/BlackOps && git status --short          # 先看有沒有本地改動；有就 stash 或 commit
git remote set-url origin https://github.com/Vanisherzd/BlackoutOps.git
git pull --ff-only origin main               # 目前 main = dfa2f1b 之後
```

## 2. 起舞台（兩種配方，先試第一種）

```bash
# Thor 定案（13:07 量過：DSpark 在 Thor 無增益，不用）：基本配方 + reranker sidecar + 審核不思考
scripts/serve-rerank.sh   # GPU_MEMORY_UTILIZATION=0.03 預設可用
RERANK_URL=http://127.0.0.1:8020/rerank REVIEW_THINK=0 GPU_MEMORY_UTILIZATION=0.25 scripts/stage-up.sh
```

- 起來後跑 `scripts/preflight.sh`（stage-up 結尾會印 PREFLIGHT）；`wan` 一項在沒 passwordless sudo 的機器上本來就 FAIL。
- rerank sidecar 選配：`scripts/serve-rerank.sh`（需要 `nvidia/llama-nemotron-rerank-1b-v2` 在 HF 快取），起了就 `export RERANK_URL=http://127.0.0.1:8020/rerank` 再 stage-up；沒有也能跑（社群層退回 BM25 排序＋判官）。
- DSpark、k=2、MTP 都**只在 Spark 量過**，Thor 數字要自己量：`scripts/decode-bench.py`、`scripts/prefix-cache-stats.py`。

## 3. 今天改了什麼（10/21 之後）

| 項目 | 預設 | 關掉 | commit |
|---|---|---|---|
| 故障目錄 49 案、八類（`ops` tier 9 案） | — | — | `1ac26df` `23a0505` `1703e50` |
| 修復優先於替換：`write_container_file`／`repair_image_file`，replace 要等修檔失敗 | `REPAIR_FIRST=1` | `REPAIR_FIRST=0` 回舊行為 | `828c049` `b1f83d8` |
| `self_heal` 不再提供給 agent | 隱藏 | `SELF_HEAL_TOOL=1` | `8a051bc` |
| dashboard 軌跡白話化、結案報告卡、全深色 | — | — | `753ceee` `20dec1c` `d6a9808` |
| dashboard「隨機真實故障」按鈕（整個目錄抽一個） | 要 `DASH_ACTIONS=1` | — | `cc43e2d` |
| 社群知識層由工具層推送（未被 runbook 涵蓋的症狀才推） | `COMMUNITY_PUSH=1` | `COMMUNITY_PUSH=0` | `1ac26df` `b7933b5` `7d309ff` |
| 知識工具預設：159k 語料 FTS5 → rerank → 判官 | `COMMUNITY_JUDGE=1` | `COMMUNITY_JUDGE=0`、`KB_COMMUNITY_V1=1` | `996fe23` |
| 症狀抽取防洪水、`docker_logs` 附「上次啟動以來的 distinct 錯誤行」 | 永遠 | — | `7d309ff` |
| DSpark `SPEC_TOKENS` 預設 2 | `SPEC=dspark` 時 | `SPEC_TOKENS=3` | `aadbf74` |
| 健康探測 3 s、內容檢查、`/proc` 程序表、`ss`／`getent`／`ip … show` 唯讀 | 永遠 | `HEALTH_CONTENT=0` | 昨夜多筆 |
| 審核代理不思考 | 舞台仍預設思考 | `REVIEW_THINK=0` 更快（1–1.5 s） | — |
| adaptive 多代理 | 關 | `AGENT_MODE=adaptive`（Gate 2 沒過，demo 別開） | — |
| 訊號層 watcher | 關 | `WATCH_SIGNALS=1`（偵測有、結案慢，demo 別開） | — |

## 4. Spark 上還在跑的東西

`logs/full-after-ab.sh`：全目錄 45 案 ×1（最終配置），約 10:50 結束，結束時自動 `stage-up` 還原舞台。
結果在 Spark 的 `logs/regression-<時間>/summary.md`、`logs/full-after-ab.out`。要提早停：

```bash
pgrep -af "full-after-ab|regression.sh|nemoclaw-drill"   # 用 pid 殺，不要 pkill -f
kill <pids>; nemoclaw edge-agent gateway restart; .venv/bin/python scripts/faults.py reset; bash logs/rag-ab-restore.sh
```

## 5. 已知狀態（別被數字嚇到，也別過度宣稱）

- 全目錄 regression（昨夜 36 案）：自主 30/32、made-worse 0、謊報 0；sigstop 工具層修後 2/2。45 案版本等 Spark 跑完。
- **Thor 本機 round 1（10:22–11:18，基本配方）：10/12、made-worse 0、謊報 0**；rlimit 與 webroot-wiped 沒過。修好後 OpenClaw 會 continuation 多跑幾輪（after-green），`self_heal` 已從 agent 工具清單拿掉（commit `8a051bc`）。
- VM 靶機：`scripts/vm-victim.sh`＋`VM_VICTIM=1` 工具組＋`scripts/vm-drill.sh`（需 Thor 裝 `qemu-system-arm qemu-utils`）。
- 修復優先於替換已驗：upstream-500／wrong-content／index-deleted 3/3，0 次替換容器（Thor 12:24）。dashboard 可注入全部 49 案（分八類下拉）＋隨機；結案橫幅顯示本場耗電 Wh 與模型 tokens。
- `front-rlimit-nofile`（worker fd 上限）目前 0/4：agent 一直改前端 default.conf、從不重建；已知難案，誠實列。
- RAG A/B：推送開 7/10 對關 8/10、兩臂 made-worse 0；10 場只推 4 次、0 次被用上 → **RAG 對 drill 的幫助還沒證實**，閘門已修、全目錄重測中（`docs/kb-community-20261022.md` Phase 4b）。
- 所有數字與定義：`docs/results-summary-20261022.md`；決策紀錄：`docs/optimizations-20261022.md`。

## 6. 上台前 10 分鐘

```bash
.venv/bin/python scripts/faults.py reset && .venv/bin/python scripts/faults.py verify   # / 200 /api/ 200 PASS
scripts/preflight.sh
# dashboard: 五張牌各按一次看 inject→resolved；再按一次「隨機真實故障」
```
