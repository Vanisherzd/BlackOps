# Thor 上的模型與 Tier 實驗報告（2026-10-21）

2026-10-21 晚上，Thor 上實測（`~/tierexp/` 的量測腳本、`results.jsonl`）。

機器：Jetson AGX Thor T5000，122 GB 統一記憶體（沒有 swap），R38.4 / JetPack 7.1，sm_110，nvpmodel MAXN。

## 1. 各組合狀態

| 標籤 | 角色 | 模型 | 引擎 / 映像 | 狀態 | 首次啟動 | 重啟 | 備註 |
|---|---|---|---|---|---|---|---|
| bolt-24gb | Tier-2（原計畫） | Nemotron-3.5-Bolt-74B-A9B-24GB | vLLM：b12x 映像 / NGC 26.01 / nightly | ❌ 無法執行 | – | – | block 量化權重需要 b12x，只支援 SM12x（GB10）；NGC 26.01 的 transformers 太舊 |
| nano4b-vllm | Tier-1 候選 | Nemotron-3-Nano-4B-FP8 | vLLM / NGC 26.01 | ✅ smoke OK | 163 s | – | 5 張舞台卡 4/5（bad-config 失敗）；第二幕彩排 122 s 結案 |
| nano30-vllm | Tier-2 候選 | Nemotron-3-Nano-30B-A3B-NVFP4 | vLLM / NGC 26.01 + FlashInfer FP4 MoE | ✅ smoke OK | 約 90 分（FlashInfer 為 sm_110 JIT 編譯） | 112 s | 權重 18.65 GiB；需 VLLM_USE_FLASHINFER_MOE_FP4=1；快取需掛載出容器 |
| nano30-trt | Tier-2 候選 | Nemotron-3-Nano-30B-A3B-NVFP4 | TensorRT-LLM 1.3.0rc13（serve-trt.sh） | ❌ 無法執行 | – | – | 權重載入成功（約 10 s），第一次推論時 FP4 fused LayerNorm 報 no kernel image（ws_layernorm_fp4_traits.cu 沒有 sm_110） |
| lightning | Tier-1 候選 | Nemotron-3.5-Lightning-30B-A3B-NVFP4 | vLLM 0.32 nightly（77154ca）+ Marlin MoE | ✅ 舞台卡 5/5（建議） | 437 s（含小型 FlashInfer JIT） | 約 100 s | 權重 17.82 GiB；需 --moe-backend marlin --reasoning-parser nemotron_v3；NGC 26.01 沒有這個 parser |
| C1-lightning+4b | Tier 組合 | T2 Lightning（0.30）+ T1 Nano-4B-FP8（0.12） | vLLM nightly + NGC 26.01 | ✅ 兩邊 smoke OK | T2 173 s + T1 270 s | – | 同時開著系統剩 61 GB；另量 nvpmodel 120W |
| C2-nano30+4b | Tier 組合 | T2 Nano30（0.30）+ T1 Nano-4B-FP8（0.12） | NGC 26.01（兩邊） | ✅ 兩邊 smoke OK | T2 112 s + T1 56 s | – | 同時開著系統剩 61 GB |
| C3-super+4b | Tier 組合 | T2 Super（0.62）+ T1 Nano-4B-FP8（0.08） | vLLM nightly | ❌ 放不下 | – | – | Super 0.62 載入權重後沒有空間給 KV cache |
| super | Tier-2 候選 | Nemotron-3-Super-120B-A12B-NVFP4 | vLLM 0.32 nightly（77154ca）+ Marlin（MoE 與 NVFP4 GEMM） | ✅ smoke OK，但太慢、太佔記憶體 | 715 s（權重 182 s + profiling 264 s，含 fp4_gemm JIT 約 3 分） | – | 權重 69.59 GiB；gpu-memory-utilization 0.72，KV 13.5 GiB；開著時系統只剩 13 GB 可用 |

## 2. 記憶體與功耗（實測）

基準（只有 shim、ops_api、NemoClaw、dashboard 等服務，沒有推論服務）：MemAvailable **115.2 GB**，閒置功耗 VIN **25.1 W**。

| 標籤 | 服務佔用記憶體 (GB) | 負載下峰值 (GB) | 閒置 W | 推論 W | 推論比基準多 W | tok/s（工具呼叫 / 事故報告 / 長 log） | J/token | 輸出正常 |
|---|---|---|---|---|---|---|---|---|
| nano30-vllm | 57.7 | 58.2 | 25.1 | 58.0 | 32.9 | 18.6 / 36.0 / 34.7 | 1.97 | 是 |
| lightning-vllm | 57.4 | 57.7 | 25.0 | 66.1 | 41.0 | 39.7 / 53.9 / 54.8 | 1.28 | 是（report 思考超過 700 tokens 未作答） |
| super-vllm | 102.4 | 102.8 | 25.6 | 73.7 | 48.6 | 13.0 / 16.0 / 14.1 | 4.93 | 是 |
| C1-lightning+4b-T2 | 53.8 | 54.4 | 25.5 | 63.6 | 38.5 | 39.8 / 56.8 / 54.0 | 1.22 | 是（report 思考超過 700 tokens 未作答） |
| C1-lightning+4b-T1 | 54.1 | 54.4 | 25.4 | 63.9 | 38.8 | 29.6 / 33.8 / 38.2 | 2.24 | 是 |
| C1-lightning+4b-T2-120W | 53.8 | 54.3 | 25.5 | 63.5 | 38.4 | 38.9 / 52.3 / 63.7 | 1.11 | 是（report 思考超過 700 tokens 未作答） |
| C1-lightning+4b-T1-120W | 54.1 | 54.5 | 25.8 | 72.4 | 47.3 | 32.4 / 44.6 / 37.5 | 1.81 | 是 |
| C2-nano30+4b-T2 | 54.5 | 54.7 | 26.6 | 55.0 | 29.9 | 37.3 / 36.0 / 43.6 | 1.85 | 是 |
| C2-nano30+4b-T1 | 54.1 | 54.8 | 26.1 | 68.8 | 43.7 | 29.8 / 47.3 / 42.1 | 2.17 | 是 |

- 服務佔用記憶體 = 基準 MemAvailable − 服務 ready 後的 MemAvailable；負載下峰值取跑 decode-bench 時 MemAvailable 的最低點。
- 功耗是 tegrastats 的 `VIN`（模組總輸入），power.py 每 0.5 秒記錄一次；閒置取 30 秒平均，推論取整段 decode-bench 平均。
- tok/s 來自 `scripts/decode-bench.py`：三種 agent 實際會遇到的 prompt，各 3 次取中位數，thinking 開、最多 700 tokens。
- J/token = 推論平均瓦數 × decode-bench 總秒數 ÷ 生成的 token 總數（含 prefill 時間，偏保守）。

## 3. 結論與建議

**建議組合：Tier-2 = Nemotron-3.5-Lightning-30B-A3B-NVFP4 單獨跑（vLLM nightly + Marlin MoE，預留 0.30），不開 Tier-1，不做 nvpmodel 連動。**

| 指標 | Lightning 單獨（0.30） | 對照 |
|---|---|---|
| 記憶體 | 權重 17.8 GiB，服務約佔 37 GB，系統剩約 80 GB | Super 單獨剩 13 GB；Lightning+4B 剩 61 GB |
| 功耗 | 閒置 25 W（跟沒開模型一樣）、推論約 64 W | Nano30 58 W、Super 74 W |
| 每 token 能耗 | **約 1.2 J** | Nano30 1.9 J、4B 2.2 J、Super 4.9 J |
| 速度 | 工具呼叫回合 2.3 秒；40–57 tok/s | Nano30 19 秒、Super 35 秒 |
| 舞台卡（NemoClaw 路徑） | **5/5**，made-worse 0；第二幕 upstream-500 **95 秒** | 4B 4/5；Bolt（Spark）5 卡中位 31–106 秒 |
| 重啟時間 | 約 100 秒（快取在主機上） | Nano30 首次 90 分 / 之後 112 秒 |

理由：

1. **記憶體**：Lightning 權重只有 18 GiB，用 0.30 的預留就有 14 GiB KV（agent 只需要同時 2 個 32k 請求），系統還留 80 GB 給 NemoClaw、sandbox、dashboard，離沒有 swap 的危險區很遠。
2. **功耗**：它是這幾顆裡每 token 最省的（1.2 J），閒置時不額外耗電。加一個 4B 當 Tier-1 只會多佔 13 GB，而且 4B 每 token 更耗能（2.2 J），把例行回合交給 4B 反而費電。
3. **nvpmodel 連動沒有節能效果**：推論只到 60–70 W，120W 模式只壓低 GPU 時脈上限，實測功耗不變。90W / 70W 模式要重開機，不能跟著 Tier 切。
4. **能力**：5 張舞台卡全過，第二幕比 Spark 上的 Bolt 還快。弱點是 bad-config（340 秒、32 次呼叫，中途 5 次被規則拒絕），第一幕抽卡最好避開它，或照 demo.md 的建議換成 upstream-down。

依照 demo.md §7 的規則（11:00 檢查點），**Tier 與 nvpmodel 砍掉，只保留人工審批**；能耗改用本報告的 J/token 數據講。

**對簡報與文件的影響**：Tier-2 從 Bolt 改成 Lightning（Bolt 在 Thor 上無法執行）；計分卡要改用 Thor 上 Lightning 的實測；沒有 MIG（JetPack 7.1）；「Tier 加 nvpmodel 省電」的說法拿不出數據，建議改成「在邊緣裝置上選對模型：同樣修好 5 張卡，Lightning 每 token 能耗只有 Super 的四分之一」。

**備案**：Nano30（vLLM NGC 26.01 + FlashInfer FP4，快取已建好，重啟 112 秒）可當 Tier-2 備援，但比較慢。Super 只能單獨跑、太慢，不建議上台。

## 4. 實驗過程的發現

1. **Bolt 24GB 在 Thor 上不能跑。** 它的 dense 層是 block 量化，vLLM 規定只能用 b12x kernel，而 b12x 只為 SM12x（Spark 的 GB10）編譯；Thor 是 sm_110。隨身碟裡的自製映像（退路 A）也一樣。Bolt BF16（150 GB）比 Thor 記憶體還大。
2. **Nano 30B NVFP4 在 vLLM 上可以跑，但第一次啟動很貴。** FlashInfer 沒有替 sm_110 預編 MoE kernel，要現場 JIT 編譯 253 個 CUTLASS 目標檔，約 90 分鐘。修正 `serve-vllm.sh` 把三個快取目錄（`~/.cache/flashinfer`、`~/.cache/flashinfer-gen`、`~/.cache/vllm-compile`）掛到主機後，重啟只要 **112 秒**、0 個重編。
3. **編譯不能無上限平行。** 不限制時同時有 49 個 nvcc 工作，MemAvailable 從 78 GB 掉到 25 GB；Thor 沒有 swap，耗盡就整台卡死。限制 `MAX_JOBS=4` 時 MemAvailable 穩定在 50–60 GB。
4. **Thor 的記憶體統計會把下載檔案的快取算進 used。** 啟動推論服務前先 `drop_caches`，否則 vLLM 會誤判可用記憶體；編譯行程結束時釋放記憶體，也曾讓 vLLM 的記憶體 profiling 斷言失敗。
5. **TensorRT-LLM 1.3.0rc13 不能在 Thor 上跑 Nano NVFP4。** 主函式庫有 sm_110 kernel，但 FP4 的 fused LayerNorm（`ws_layernorm_fp4_traits.cu`）沒有，第一次推論就報 `no kernel image is available`。NVIDIA 給 Thor 的 TensorRT 路線是另一套 TensorRT Edge-LLM，比賽前換不過去。所以「改回 TRT 沿用舊計分卡」行不通，推論層維持 vLLM。
6. **Nano 30B / vLLM 閒置時不耗電。** 服務開著但沒有請求時 VIN 25.1 W，和完全沒有推論服務時一樣；推論時 58 W。記憶體則是啟動就預留（`gpu-memory-utilization 0.45` ≈ 59 GB），不管有沒有在推論。
7. **Lightning 30B 比 Nano 30B 快、也更省能。** 同樣 0.45 的記憶體預留、同樣的 decode-bench：tok/s 39.7 / 53.9 / 54.8 對 18.6 / 36.0 / 34.7；推論功耗 66 W 對 58 W，但每 token 能耗約 1.28 J 對 1.97 J（少約 35%）。工具呼叫回合差最多：Lightning 93 tokens、2.3 秒就決定，Nano 想了 351 tokens、18.9 秒。
   - 注意：「事故報告」題 Lightning 的 700 tokens 全花在思考、還沒開始回答就被截斷（decode-bench 標成 OUTPUT BROKEN，但不是亂碼，chars/token 3.55 正常）。開放式寫作它會想得比較久，要用舞台卡實測 agent 端到端時間才算數。
   - Marlin MoE 是 vLLM 預編好的 kernel，不像 Nano 走 FlashInfer FP4 MoE 要 JIT 90 分鐘；Lightning 首次啟動 7 分鐘。
8. **服務佔用的記憶體主要由預留比例決定，不是模型大小。** 兩顆 30B 權重都約 18 GiB，但 0.45 的設定讓 vLLM 啟動就佔走約 58 GB（其餘當 KV cache，Lightning 有 31.5 GiB KV、可以同時服務 130 個 32k 請求，遠超 agent 需要）。做 Tier 時應該把兩邊的比例壓低。
9. **Super 120B 在 Thor 上跑得起來，但不適合當 Tier-2。** 權重 69.6 GiB，用 0.72 的預留時系統只剩 13 GB 可用（記憶體保護門檻 12 GB）。速度 13.0 / 16.0 / 14.1 tok/s，工具呼叫回合 35 秒（Lightning 2.3 秒），每 token 約 4.9 J，是 Lightning 的近 4 倍。第二幕 agent 通常 6–10 個回合，光推論就會超過 2.5 分鐘的時間預算。
10. **Super 不能跟任何 Tier-1 同時開。** 實測 C3（Super 預留 0.62 + 4B 預留 0.08）：Super 載入 69.6 GiB 權重後報 `No available memory for the cache blocks`，0.62 連 KV cache 都放不下，至少要約 0.68。再加上 4B 的 0.08，系統只剩約 4 GB，沒有 swap 的 Thor 不能這樣跑。「Tier-1 Lightning + Tier-2 Super」更不可能（Lightning 權重就 18 GB）。Super 在 Thor 上只能單獨開。
11. **兩個推論服務同時開（T2 預留 0.30 + T1 4B 預留 0.12）總共約佔 54 GB**，比單一模型用 0.45 的 58 GB 還少，系統剩約 61 GB。Lightning 在 0.30 下仍有 13.9 GiB KV，Nano30 有 8 GiB，對 agent（同時 2 個請求、32k context）都很夠。
12. **在 Thor 上，4B 當 Tier-1 反而比 Tier-2 更耗能。** 4B 是 FP8 dense，每 token 約 2.2 J；Lightning 是 NVFP4 MoE（每 token 只啟用約 3B 參數），每 token 約 1.2 J，而且速度也比 4B 快（工具呼叫 39.8 對 29.6 tok/s）。Nano30 當 T2 是 1.85 J。Tier 設計的前提「小模型比較省」在這組模型上不成立：把例行回合交給 4B 不會省電。
13. **nvpmodel 120W 在推論時沒有可量測的節能效果。** C1 的 Lightning 推論：MAXN 63.6 W、120W 63.5 W，速度也差不多；4B 的兩次量測（63.9 / 72.4 W）差異屬於量測雜訊。推論功耗只有 60–70 W，遠低於 120 W 上限，而 120W 模式只把 GPU 時脈上限從 1575 降到 1386 MHz，這個負載受限於記憶體頻寬。Tier 連動 nvpmodel 的節能故事在 Thor 上拿不出數據支撐。
14. **nvpmodel：MAXN(0) ↔ 120W(1) 可即時切換（約 30 ms，GPU 上限 1575 → 1386 MHz）；90W(2)、70W(3) 需要重開機。** Tier 連動改用 `NVP_T1=1 NVP_T2=0`。

## 5. 舞台卡（Lightning，Thor，2026-10-21 18:02–18:16，`logs/drills-20261021-180239.log`）

| 卡 | 結果 | 秒數 | 工具呼叫 | 備註 |
|---|---|---|---|---|
| nginx-stopped | ✅ | 25 | 5 | |
| container-removed | ✅ | 27 | 5 | |
| bad-config | ✅ | 340 | 32 | 5 次被規則拒絕（沒讀 runbook 就想改設定、設定檔混入 OpenClaw 記憶文字），最後修好 |
| upstream-paused | ✅ | 26 | 3 | |
| upstream-500 | ✅ | 95 | 5 | 第二幕的卡 |

headline：autonomous resolution 5/5（100%）；made-worse 0；false health claims 0；evidence-first 100%；closure 100%。

## 6. 舞台流程驗證（Thor，Lightning，2026-10-21 晚上）

**第二幕 × 3**（dashboard 注入 upstream-500 → watchdog 叫醒 agent → 核准卡 → dashboard 核准）：**3/3**，81.9、127.1、87.0 s；核准卡在注入後 78–123 s 出現。

**完整舞台流程**（照 `docs/demo.md` §3.2 的間隔：第一幕 → 結案後 20 s 斷網 → 再 20 s 第二幕 → 核准 → 恢復外網 → 觀察 75 s），全部修正之後：

| 輪次 | 第一幕 | 斷網 | 第二幕 | 結案後 75 s |
|---|---|---|---|---|
| 1 | nginx-stopped 22.0 s | 外部 curl 被擋 | 152.1 s（核准卡 +144 s，2 次 `denied:`），峰值 84.9 W | 一次空白內容的 `write_config` 被「系統健康」規則擋下；agent 60 s 後被停；沒有新事故；功耗回到 23 W |
| 2 | container-removed 20.8 s | 外部 curl 被擋 | 108.1 s（核准卡 +103 s），峰值 101.7 W | 0 次殘留呼叫，0 個 agent，功耗回到 25 W |

修正前的第一輪：第二幕結案 30 秒後，OpenClaw 延續了第一幕的 session，Lightning 寫壞 `default.conf`，前端掛掉、看板轉紅。這是加上「系統健康時拒絕改動」與 watchdog 結案寬限的原因。

**Guardrails**：`test-guardrails.py` 連續 3 次 **23/23**（第二次判斷換個寫法之後；Lightning 對某一行注入固定判 No，加上「Log line:」前綴就判 Yes）。

**5 張舞台卡（合併 #8 之後）**：nginx-stopped、container-removed、bad-config（**52 s**，#8 的新 runbook 之前是 340 s）、upstream-paused 都 PASS；upstream-500 因為舞台設定 `APPROVAL=manual` 沒有人按核准，180 s 逾時被拒絕（fail-closed，設計如此），跑 drill 要用 `APPROVAL=auto`。
