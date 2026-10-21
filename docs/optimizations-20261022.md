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

### 複合故障（新增，tier `compound`；03:31–03:42，`logs/drills-20261022-013101.log`，同上環境）

兩個獨立故障同時發生，第一次修完健康檢查說「還沒好」，agent 要回頭再找第二個原因。各跑兩次：

| 牌 | 內容 | 兩次 | 牆鐘 | agent 的路徑 |
|---|---|---|---|---|
| compound-config-upstream-down | 前端站台設定壞＋後端停掉 | PASS, PASS | 76 s, 64 s | logs → restore_config → restart 前端 → 仍 Exited → docker_ps 看到後端停 → start 後端 → restart 前端 → 200/200 |
| compound-detached-paused | 後端凍結（Paused）＋脫離 edge-net | PASS, PASS | 47 s, 42 s | restart 後端（Paused 的 runbook）→ 仍 504 → curl 不通 → network_repair → 200/200 |
| compound-front-stopped-upstream-port | 前端停掉＋後端自己的設定改聽 :8080 | PASS, PASS | 52 s, 77 s | start 前端 → /api/ 502 → 讀後端設定、port_check 8080 → **write_config 把 proxy_pass 指到 :8080**（兩次都選改前端，沒選重建後端；兩種都算修好，`fix_tools` 已包含） |

注入：`.venv/bin/python scripts/faults.py inject <case>`；dashboard 要能注入它們需在 stage-up 前 `export DASH_EXTRA_CARDS=compound-config-upstream-down,compound-detached-paused,compound-front-stopped-upstream-port`
（五個舞台按鈕不變）。要不要換進第一幕是 `demo.md` 的決定；它們比單一故障慢 20–40 s，但故事更像真實事故。

### 一場事故的時間都去哪了（十場 drill 的拆解，`logs/oai-shim.log` START/USAGE 配對）

| 成本 | 中位 | 最大 | 可調？ |
|---|---|---|---|
| 模型 decode（所有輪次合計） | 14.1 s（754 completion tokens） | 20.6 s | 不可：≈ 0.58 s + tokens/70；思考在未決輪次 |
| 其中最後的報告輪 | 3.9 s | 6.0 s | 可（prompt 限字數），但會改舞台上看到的報告文字，沒動 |
| OpenClaw 啟動 | 2.8 s | 3.3 s | 不可 |
| 每次工具的框架往返 | ~0.5 s × 6 | — | 不可 |
| 工具端 | 4.4 s | **34.2 s** | **已修**：`docker_exec cat /var/log/nginx/error.log` 在 nginx 映像裡是連到 stderr 的連結，`cat` 卡到 30 s subprocess 上限——那場 65 s 的 upstream-500 不是模型在想，是 cat 在等。現在 8 s 就放手並告訴 agent 用 docker_logs |
| 審核 | 0（無 recreate）／1.0–1.5 s | 1.5 s | 已是 `REVIEW_THINK=0` 的數字 |
| prompt | 每輪 7.0–10.4k tokens，前綴 67–100% | — | prefill ≤ 0.5 s/輪，不是瓶頸；shim 現在會記 `cached_tokens`，前綴快取命中率終於量得到 |

watchdog 走的真實事故（02:26）：偵測 2–4 s → triage 1.2 s → 啟動 2.35 s → 3 輪 5.5/2.6/2.8 s → docker_start 1.4 s → 15.2 s 結案；
triage 讓第一輪 prompt 多 2.7k tokens，但少 2–3 輪（省 5–7 s）。

### 推論加速：DSpark speculative decoding（model card 的 DGX Spark 配方，04:35–04:42）

Lightning 的 model card 在 Spark 上推薦 DSpark drafter（`nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark`，1.35 GB，
`--speculative_config.num_speculative_tokens 3 --mamba-backend flashinfer`）；舞台配方沒用它。開第二個實例（:8002，util 0.20）跟現役
的（:8000）同一組 prompt 跑 `scripts/decode-bench.py`（temperature 0、thinking on、各 3 次取中位）：

| prompt | 現役 tok/s | DSpark tok/s | 加速 | chars/token（現役 → DSpark） |
|---|---|---|---|---|
| tool-turn（2.4k prompt，86 tokens） | 51.4 | **65.1** | +27% | 2.81 → 2.73 |
| report（1.8k prompt，700 tokens） | 67.4 | **100.3** | +49% | 3.93 → 3.92 |
| long-context（8.9k prompt，700 tokens） | 68.0 | **91.0** | +34% | 3.71 → 3.57 |

輸出密度一樣（上次 NGram 壞掉時 chars/token 崩到一個字；這次沒有；`report` 兩邊都被 bench 標 OUTPUT BROKEN 是那個 prompt 自己把 700 tokens
想完，A/B 相同）。容器 RSS 4.08 GiB 對 3.57 GiB（+0.5 GB），unified memory 多一份 draft 權重。

**drill 品質驗證（04:47–05:00，shim 指到 DSpark 實例，`logs/drills-20261022-024654.log`）**：五張舞台牌＋三個複合故障 **8/8 autonomous**，
made-worse 0、false health claims 0、evidence-first 100%、closure 100%（`scripts/score.py`）。牆鐘：nginx-stopped 17 s（原 24–25）、
container-removed 16 s（21）、upstream-500 24 s（28–33）、bad-config 38 s（30–33）、upstream-paused 46 s（35，多一次無關呼叫）、複合 38／57／95 s
（原 64–76／42–47／52–77）——牆鐘被模型變異蓋過，品質沒掉。**採用（Spark）**：`SPEC=dspark GPU_MEMORY_UTILIZATION=0.20 scripts/stage-up.sh`；
serve-vllm 的 generic 與 lightning 路徑都認 SPEC。Thor 的 NGC 映像沒驗過 DSpark／FlashInfer Mamba，不設就是原配方。

### 採用後的舞台（05:07，`SPEC=dspark GPU_MEMORY_UTILIZATION=0.20 scripts/stage-up.sh`）

preflight 與之前相同（只有 `wan`）；dashboard 注入 nginx-stopped → watchdog 開事故 4.9 s → resolved **14.9 s**（原 18.4 s）。
記憶體（`scripts/mem-profile.sh`，1 Hz，涵蓋 DSpark drill 尾段、兩個 Lightning 實例並存、評測、切換）：vllm-edge RSS 3.55 → 3.69 GiB
（DSpark +0.15）、rerank sidecar 3.34 GiB、ops-api 1.15–1.30 GiB（bge-small 與知識庫）、sandbox 0.41–0.91 GiB；兩個實例並存時
MemAvailable 最低 44 GiB，切回單一實例後 74 GiB。這台 vLLM 的 usage 不回 `prompt_tokens_details`（None），所以 shim 記的
`cached_tokens` 永遠不會出現；前綴快取命中率要從 vLLM 的 `/metrics` 計數器讀（下一輪）。

drill harness：`nemoclaw-drill.sh` 的 `TRIAGE` 改為預設開（與舞台 watchdog 一致；`TRIAGE=0` 回到裸告警）。

## 文獻對照：哪些頂會技術對我們「量得到」（Phase 0 判定，06:00）

對照標準是我們量到的瓶頸（一場事故：模型 decode 中位 14 s、OpenClaw 啟動 2.8 s、工具 4 s；統一記憶體 119 GB，舞台用掉 ~45 GB；
KV cache 在 util 0.20 時 53k tokens / 3.9 GiB）。引用請在上簡報前核對原文與 DOI。

| 技術（代表作） | 解決的問題 | 對本案 | 判定 |
|---|---|---|---|
| **Palu**：KV cache 低秩投影壓縮（Chang, …, Kai-Chiang Wu，ICLR 2025）；KIVI 2-bit KV（Liu et al., ICML 2024） | attention 模型的 KV 記憶體 | Lightning 是 Mamba-2/MoE 混合、KV 已 fp8、KV 只佔 3.9 GiB、記憶體還剩 70 GB：壓一半省 2 GiB，瓶頸不在這；要改 vLLM kernel | **不做**（記下來給 Thor 64 GB 版本或純 transformer 模型時） |
| Speculative decoding（Leviathan et al., ICML 2023；EAGLE, Li et al., ICML 2024；DSpark/DFlash/MTP，Nemotron 3.5 card） | decode 慢（我們最大的成本） | **已做**：DSpark +27–49% tok/s、8/8 drill。下一步 A/B **MTP**（內建多 token 頭，0 額外權重、少 1.35 GB），並量兩者是否保住 prefix cache | **做**（下一輪） |
| Prefix／prompt caching（SGLang RadixAttention，Zheng et al., NeurIPS 2024；vLLM APC） | 每輪重算 7–10k 固定前綴 | 已開，但 DSpark 實例命中 0%；plain 實例待量（機器安靜時） | **查**（DSpark 是否與 APC 互斥） |
| Cross-encoder rerank（Nogueira & Cho 2019；NVIDIA Nemotron rerank） | BM25 排錯 | **已做**：top-1 類別對 0.70 → 0.78 | 做完，調參中（網格） |
| Corrective／Self-RAG 的「判斷再用」（Asai et al., ICLR 2024；Yan et al., 2024） | 檢索到不相關東西就該棄權 | reranker 分數當棄權依據已量：無效（0.05 門檻就砍掉一半對的）。可試 LLM 判官（Lightning no-think 一次 ~0.5 s） | **候選**（要先有相關性標註，不是類別標籤） |
| ReAct／Reflexion（Yao et al., ICLR 2023；Shinn et al., NeurIPS 2023） | 工具迴圈與自我修正 | 已是：工具迴圈＋reviewer 兩把鑰匙＋健康檢查附在每個動作後 | 做完 |
| 查詢改寫（HyDE, Gao et al., ACL 2023） | agent 用裸「504」查知識庫 | 斷詞後為空、什麼都配不到；可用事故 trace 的最後一行錯誤補查詢。drill 裡 agent 從沒呼叫這工具，量不到效果 | **候選**（無法離線量） |

**結論**：本案的加速與品質槓桿在 speculative decoding、prefix caching、rerank；KV 壓縮是別的瓶頸的解。
新增兩個機房形狀的故障：`upstream-dependency-timeout`（後端自己的設定把 /api/ 轉給一個永遠不回的依賴，20 s connect timeout，
兩個容器都 Up、前端 504、log 寫著死掉的依賴位址；只有重建能清）與 `front-hosts-poisoned`（前端 /etc/hosts 多了一行把後端解析到死位址，
log 乾淨、容器 Up）。第一版 fail-slow 用 CPU 配額 2% 注入過：靜態 nginx 照樣在時限內回應，什麼都沒壞，所以換成「依賴逾時」。
drill 結果見下輪。

### kb-eval 端到端（Lightning，05:12–05:41，`docs/kb-eval-lightning.md`）

63 個 held-out benchmark 事故、一次性診斷、thinking off、ratio 8 與 32，兩臂（只給遙測／再加 `search_incident_knowledge` 的回傳）：

| arm | ratio | strict | lenient | scenario | service |
|---|---|---|---|---|---|
| none | 8x | 6% | 6% | 29% | 40% |
| rag | 8x | 6% | 8% | 17% | 38% |
| none | 32x | 3% | 5% | 22% | 35% |
| rag | 32x | 5% | 5% | 14% | 33% |

與 9/30 的 Nano（strict 22–24%、RAG 在雜訊內）比：Lightning 在這個封閉標籤的一次性診斷題上**更差**（它是為工具迴圈與思考訓練的，
thinking off 的一句話診斷不是它的強項），RAG 一樣沒有幫助、scenario 準確率還掉。結論不變：這個 KB 的價值在工具迴圈裡
（runbook 與卡片跟著 docker_logs 走），不在一次性診斷；頂會式的 RAG 技巧（rerank、棄權）要在能量到的地方量：我們量的是
社群層的 held-out 與探針，不是這個 benchmark。

### MTP vs DSpark（配對量測，06:25–06:29，同一時刻同一負載：兩個 Lightning 實例＋rerank 評測在跑，絕對值偏低，只看相對）

| | DSpark（:8000） | MTP（:8002，`SPEC=mtp`，2 個推測 token） |
|---|---|---|
| tool-turn tok/s | 31.8 | 31.3 |
| report tok/s | 45.4 | 42.4 |
| long-context tok/s | 42.9 | 40.2 |
| prefix cache 命中（同一組重複 prompt） | **0%**（39k queries） | **21.6%**（8,448/39,132） |
| 容器 RSS | 3.71 GiB | 3.60 GiB |
| 額外權重 | DSpark drafter 1.35 GB | 無 |

DSpark decode 快 2–7%；MTP 保住前綴快取、少一份 drafter。MTP 第一次啟動失敗是暫時的（第二次順利，架構 NemotronHMTPModel）。
Gate 3 看 drill 牆鐘：MTP 的 8 場 drill 跑中。

**Gate 3 判定（07:48）**：MTP 的 8 場 drill 8/8 PASS，但牆鐘 34／78／27／78／53／231／80／97 s 對 DSpark 的 17／46／16／38／24／38／57／95 s
（兩組都在機器有其他負載時跑，不是乾淨對照，但方向一致：MTP 沒有比較快）。MTTR ≤ DSpark 不成立 → **留 DSpark**，MTP 實例停掉；
`SPEC=mtp` 保留給 Thor 試（不用 drafter、保住 prefix cache）。

### 新增故障（續）

`compound-triple`（前端設定壞＋後端停＋後端改埠，三層三因）、`upstream-log-flood-500`（upstream-500 埋在 3,000 行 heartbeat
通知裡，考「在雜訊裡找那一行」）。目錄現在 36 個案例、10 個 tier；全目錄 regression（`scripts/regression.sh`）排在引擎決定之後。

## Agent 框架：今晚併入 main 的四個旗標（全部預設關或與現況等價）

| 項目 | 旗標 | 做什麼 | 驗證狀態 |
|---|---|---|---|
| `evidence_bundle` 工具 | 無（唯讀工具，直接列出） | 一呼叫平行收齊：兩個健康 URL、docker_ps、兩個容器的 log（含 runbook）、站台設定、8880、磁碟；6000 字上限、一筆 trace | 單元測試通過；drill 行為待 regression（agent 自己決定用不用） |
| 平行專家診斷 `parallel_diagnosis` | `AGENT_MODE=adaptive` | service／platform 兩個**唯讀**專家（no-think、≤4 輪、各自工具子集、越界即拒）並行＋知識查詢；合併出一致／衝突／待查；動作仍只走原工具＋reviewer＋審批。觸發是確定性的：動作後健康仍紅的那一行 | 單元測試通過；要用 compound／datacenter drill 比 single（Gate 2：成功率或 RC 正確率或 MTTR 要變好，made-worse 0） |
| 已驗證事故記憶 | `INCIDENT_MEMORY`（預設開；recall 空時無影響） | watchdog 在 resolved 時把症狀、動作順序、原因、驗證寫進 `logs/incident-memory.jsonl`；`search_incident_knowledge` 先回「本 stack 自己修過」 | 單元測試通過；第一筆記憶要等真實事故結案 |
| 資源 governor | `RESOURCE_GOVERNOR=1` | MemAvailable 門檻決定 specialist 並行數、reranker 開關、語料 v1/v2 | 單元測試通過 |

Watchdog 本來就在：2 s 巡檢兩個健康 URL、連兩次失敗開事故、triage 第一眼、派 sandbox agent（量到 4 s 偵測、15 s 結案）。
正在加的是「更多感官」（`WATCH_SIGNALS=1`，Codex 進行中）：容器狀態與重啟次數、nginx `[emerg]/[crit]`、磁碟／inode 門檻、docker events
（die／oom／kill）——讓「表面健康但在壞」的狀況在健康 URL 倒下之前就被看到。

## 外部建議（GPT 的 04:06 路線圖）可行性審查（06:50）

逐條對照我們量到的數字，不照單全收：

| 建議 | 判定 | 依據／做法 |
|---|---|---|
| 158k + rerank 重量（Recall@20、MRR、nDCG@5、P50/P95） | **做**（進行中） | 7 站 FTS5 BM25 與 +rerank 評測跑中；MRR／nDCG 只有「類別」標籤可用，不是逐篇相關性——指標名稱一律冠 `category_`，不假裝是真相關性 |
| Python 倒排表 → SQLite FTS5 | **做完** | `COMMUNITY_BACKEND=fts5`；Mac 上 v1：建 0.2 s、開 25 ms、2–7 ms/查詢；159k 建 279 MB。閘門＝recall@20 不降、rerank 後準確率不降、RSS 降 |
| Adaptive Specialist Delegation（三個唯讀 specialist＋單一 mutation owner） | **有條件做**（旗標 `AGENT_MODE`，預設 single） | 瓶頸是模型 decode（14 s/事故）；specialist = 更多 LLM 呼叫，max-num-seqs 2 只能雙路並行。只在 deep path 觸發（第一次修復後健康仍紅、無 runbook、同工具重複、兩次 policy 拒絕）；用 compound／datacenter drill 量 MTTR 與 RC 正確率，沒變好就不開 |
| 不用 LLM 自報 confidence，用確定性觸發 | **同意** | 我們本來就有：health 附在每個動作後、reviewer 兩把鑰匙、runbook gate |
| 平行證據蒐集 `evidence_bundle` | **做**（Codex 進行中） | 舞台的 watchdog 告警已帶 triage，第一輪收益比 GPT 想的小；價值在動作之後一呼叫重收證據 |
| Fast／Deep path router | **同意，即 specialist 的觸發邏輯** | 不另造一層 |
| Memory Governor | **做最小版**（Codex 進行中） | 記憶體不是瓶頸（MemAvailable 74 GB）；governor 只決定 specialist 並行數、reranker 開關、語料 v1/v2 |
| Verified Incident Memory（Tier 1） | **有條件做** | repo 已有 `stack_experience`（17 筆 drill 驗證過的修法，`kb-cards.py experience`）——擴充成事故結案自動寫入＋檢索第一層，而不是另建一套 |
| Prefix cache 不當主戰場；MTP vs DSpark 決定 | **同意** | MTP A/B 跑中 |
| 兩個 datacenter 故障各跑 ≥3 | **做** | 依賴逾時 2/2 PASS（220 s、295 s：每次 /api/ 探測都卡 20 s）、hosts 2/2 PASS（73 s、9 min）；regression 腳本（Codex）會湊滿 3 |
| 多 agent 共用一份模型 | **同意** | 同一個 vLLM，只多 session |
| 10–11 點設 gate、12 點後不加功能、14 點內部 freeze | **同意** | 以 Gate 1 RAG、Gate 2 multi-agent、Gate 3 MTP 三道為準 |

## Repo 狀態（06:05）

GitHub 只剩 `main`（刪掉已併入的 `docs/rag-rerank-20261021`）。Spark 刪掉已併入的 `fix/thor-power-rail`、`fix/ops-tool-safety-20261021`、
`wip/kb-cards-runbooks-20261021`、`results/bolt-vs-nano-hard` 與兩個乾淨的 worktree。**保留** `~/edge-agent-results`（分支 `feat/bolt-opt`）：
裡面有 93 行未 commit 的改動（AGENTS.md 的「三行結案格式」、agent.py／faults.py／reviewer.py），不是我的，沒動；diff 存在
Spark 的 `logs/edge-agent-results-uncommitted.patch`，要併就從那裡起。

## 給隊友

- Thor 早上：`cd ~/BlackOps && git pull --ff-only && scripts/stage-up.sh`，再跑一次 `demo.md` 的流程。
- 想把審核再快 12 s：在 stage-up 的 ops-api 那行環境加 `REVIEW_THINK=0`。今晚 3/3 判決相同，但只有 upstream-500 這張牌需要審核，樣本小，所以沒有改預設。
- Reranker（選配，量過）：`scripts/serve-rerank.sh` 起一個 vLLM sidecar（:8020，GPU 4%，46 s healthy），ops_api 環境加
  `RERANK_URL=http://127.0.0.1:8020/rerank`，知識工具的社群參考 top-1 類別錯從 29% 降到 22%，每次多 145 ms；沒設或 sidecar 掛了就自動回 BM25。
  stage-up 不會啟動它（旗標預設關）。數字與門檻表在 `docs/kb-community-20261022.md` Gate B。
- 評審問「runbook 以外的故障怎麼辦」：agent 問 `search_incident_knowledge` 會拿到有出處的社群修復假設（CC BY-SA，標明未在本機驗證）；可以現場用
  `.venv/bin/python scripts/kb-community.py probe "NVRM: Xid 79, GPU has fallen off the bus"` 示範。排名品質是 BM25 等級，罕見症狀可能只是同類別的弱匹配。
- v4 修復卡的 phrase gate（survey §4 D 組，分支 `ragd` 已併入 main）：22 句固定探針 `scripts/kb-gate-probe.py`——漏推 0/8 → **6/8**
  （inode、OOMKilled/137、CrashLoopBackOff 各補兩個雙字 phrase；`500 internal server error` 結構上到不了 bar 8.0，沒改 bar）；
  `permission denied` 誤推 3 → **0**（`kb/cards/pack_overrides.json` 用 `exec permission denied` 覆蓋那張 K8s 卡的 v4 版本）；
  8 句對照句、`kb-cards.py eval` 的 drill 查詢、`corpus_v3.jsonl` 都逐位元不變。只影響 `KB_RETRIEVAL=v4`；舞台的 v3 不受影響。
- 已知未修：`scripts/eval-hard-repairs.py` 在這個 HEAD 上 v3 7/8、v4 7/8（10/21 語料合併後就是如此，對應 `tests/agent/test_repair_knowledge.py`
  的 3 個失敗），不是今晚造成的。
- **磁碟**：DGX 根磁碟 03:10 100%（剩 1.3 GB；00:50 還有 26 GB，新長的 25 GB 在可讀目錄找不到，最可能在 `/var/lib/docker`）。
  依使用者指示 04:00 清掉沒用的（`logs/cleanup-1.sh`、`cleanup-2.sh`）：TRT-LLM 1.3.0rc13 映像 35.6 GB（無容器、路線已退役）、Bolt 衍生映像、
  4 個殘缺 HF 下載 10.3 GB、13 個舊 VS Code server 8 GB、懸空匿名 volume 4.5 GB，以及 9 個退役／從未用於本案的 HF 模型
  （Nano-30B-BF16 63 GB、Qwen3.6-FP8 37.5、Bolt 24、Qwen3.8 23、gemma-4 23、Qwen3-8B 16、SD 1.5、Qwen3.6-NVFP4 殘檔、diffusiongemma 等）共 141 GB
  → **242 GB 可用**。保留：Lightning、Nano-30B-NVFP4、Nano-4B-FP8、bge-small。沒碰隊友的 `logs/prune-calib`（17 GB）、`~/workspace`、CVAT／ctai 容器與 volume、
  `autoresearch_uv_cache`（仍被容器引用）。`~/.cache/vllm-bolt` 有 root 擁有的檔案刪不掉（<1 GB）。
