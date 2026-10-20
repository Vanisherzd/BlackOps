Blackout Ops 決賽 Demo 執行手冊
2026年10月14日 · @peijen
現場配置與網路拓撲
Thor 與 victim 筆電留在座位，用一條網路線直連；台上筆電經會場網路看 Thor 的 dashboard。「斷網」只封鎖往公網的流量，所以監控和台上畫面都不受影響，這正好對應 L1 情境。
victim 筆電不接會場網路，只看得到 Thor；Thor 有兩個以上的 RJ45 口，一個接 victim、一個接會場。
設備
位置
介面與位址
跑什麼
Jetson Thor
座位
1GbE 口設 10.0.0.1/24 直連 victim；另一個口接會場網路（DHCP）
推論、NemoClaw Agent、ops_api、watchdog、dashboard、審批端點
victim 筆電
座位
USB 網卡設 10.0.0.2/24，不接會場網路
edge-victim、edge-victim-app、廠務看板頁
簡報筆電
台上
會場網路，能用有線就不用 Wi-Fi
投影片、dashboard 操作、本機回放資料
後台隊友筆電
座位
會場網路；與台上聯絡走手機熱點
ssh 進 Thor 處理狀況
完整 Demo 流程
簡報 6.5 分鐘，含兩幕現場演示，Agent 實際運作約 3 分鐘。Thor 留在座位，台上用筆電經會場網路開 dashboard 操作，座位留一名後台隊友。
上台前檢查（換場前 15 分鐘，後台隊友執行）
[ ] scripts/preflight.sh 全綠：兩個推論 server、shim、ops_api、dashboard、watchdog、功耗記錄器、NemoClaw Inference: healthy
[ ] 兩顆模型各完成一次預熱推論（preflight 內建），台上絕不出現冷啟動
[ ] faults.py reset 後 verify 為 PASS，廠務看板頁是綠的
[ ] Tier 指示為 Tier-1，nvpmodel 在巡檢模式
[ ] WAN 狀態 online，對外連線計數器歸零
[ ] 審批模式切回 manual（夜跑用的是 auto）
[ ] 15 分鐘內完整跑過一場 upstream-500，結果 PASS
[ ] 台上筆電已開好 dashboard 分頁，回放資料已同步到筆電本機
[ ] 後台隊友與主講人用手機熱點互通，不依賴會場網路
時間表
時間
段落
台上動作
畫面上看到
主講重點
0:00–0:45
開場
講投影片，dashboard 縮在角落
看板綠燈、功耗平線、Tier-1、WAN online
監控者不能與被監控者共命運。座位上那台 Thor 是維運節點，旁邊那台筆電就是被監控的系統
0:45–1:00
抽卡
請評審從 4 張卡挑一張：nginx-stopped、container-removed、bad-config、upstream-paused
—
不是寫好的劇本，故障由評審決定
1:00–2:00
第一幕
按「注入」，之後不碰任何按鍵
看板變紅 → watchdog 偵測並開事故 → 軌跡區逐步出現工具、證據、runbook → 看板恢復綠燈
沒人叫它修，它只收到一句警報。功耗幾乎沒動：簡單事故 Tier-1 就處理完，不叫醒 30B
2:00–2:30
斷網
按「切斷外網」，在終端視窗跑一次 curl 打外部 API
curl 失敗、WAN 燈號 offline、審批通道切到「本機」、對外連線計數維持 0
模擬 ISP 中斷，內網照常，這是 L1；在廠務氣隙網段這是常態
2:30–5:00
第二幕
按「注入 upstream-500」，審批卡跳出時按「核准」
見下方四個階段
一個事故、四層調整：計畫、模型、硬體、通道。Self-Healing ≠ Root Shell
5:00–6:00
數據
投影片
夜跑結果：有調度 vs 全程 Tier-2 的單次診斷能耗、Tier-1 承接比例、計分卡
算力是 Agent 自己要管的資源
6:00–6:30
收尾
投影片
既有成果揭露、Observe / Reason / Recover locally
—
第二幕的四個階段
1. 計畫層：/api/ 回 500。Agent 讀 log、拿到 runbook，先做最小動作 docker_restart edge-victim-app，驗證後仍是 500，原假設被推翻。
2. 模型層 ＋ 硬體層：假設被推翻觸發升級。Tier 指示變成 Tier-2，nvpmodel 拉到高效能模式，功耗曲線出現尖峰。
3. 雙鑰匙 ＋ 通道層：Agent 提出 recreate_stack replace。程式鑰匙確認「重啟過且仍失敗」，審核代理引用證據判 APPROVE，審批卡跳出，通道標示「本機（外網不可用）」。
4. 收尾：台上按核准，容器重建，/api/ 回 200，看板恢復綠燈，Tier 與功耗降回巡檢狀態。
第一幕超過 90 秒還沒結束，就直接往下切斷網，不要等；Agent 在斷網狀態下修完反而更有說服力。
失敗預案
原則：現場出狀況時照實說，然後切到能證明同一件事的備援；所有回放畫面都標示 REPLAY。
狀況
怎麼發現
台上動作
後台動作
台上連不到 Thor
dashboard 超過 5 秒沒更新
切到筆電本機的回放模式，口頭說明會場網路斷線
檢查座位到會場的網路線，恢復後通知主講
第一幕 Agent 卡住
軌跡區 30 秒沒有新步驟，或總時間超過 90 秒
直接進「斷網」段落，不等
結束該場並 faults.py reset
Agent 判斷升級而非修復
軌跡出現 finish status=escalate
照實講：它沒把系統弄得更糟，而是交還給人。這本身就是計分卡的一類結果
reset，準備第二幕
第二幕審核代理 REJECT
軌跡出現 review-rejected
唸出它引用的那行證據，說明這正是雙鑰匙在運作；再切回放展示核准路徑
不要重試，避免拖時間
推論 server 掛掉
dashboard 的 Tier 區變紅
全程改用回放
重啟 serve 腳本，約需 2 分鐘
「切斷外網」無效
curl 仍然成功
跳過此段，口述 L1 情境，第二幕照做
檢查 wan.sh status
nvpmodel 沒切換
功耗曲線沒有尖峰
口頭帶過，以夜跑數據頁補
無
時間不夠
主講看計時器
砍第一幕，保住第二幕
無
後台隊友與主講人的聯絡走手機熱點或手機訊息，不走會場網路。
器材清單
主辦只提供 Thor 主機、螢幕、鍵盤、滑鼠，以下全部自備。負責人欄位請自行填寫。
類別
品項
數量
用途
負責人
網路
Cat6 網路線 1 m
2
Thor ↔ victim 筆電直連、備用

網路
Cat6 網路線 5–10 m
2
Thor 接會場網路；台上筆電有線接會場（若有提供）

網路
USB-C 轉 RJ45 gigabit 網卡
3
victim 筆電、台上筆電、備用

網路
5 埠 gigabit 非網管 switch
1
備用；直連出問題或要多接一台時用

網路
線材標籤
1 卷
標明「監控網段」「會場／WAN」，避免拔錯線

被監控系統
victim 筆電（原生 Ubuntu 24.04）
1
跑兩個 nginx 容器與廠務看板頁

被監控系統
可開機 Ubuntu USB
1
victim 筆電出問題時，用任一台筆電開機頂替

台上
簡報筆電
1
投影片＋dashboard 操作，存有回放資料

台上
備援筆電
1
同樣存有投影片與回放資料

台上
HDMI 線、USB-C 轉 HDMI
各 2
接投影機

後台
隊友筆電
1
經監控網段或會場網路 ssh 進 Thor

後台
手機熱點
1
後台與台上聯絡，不依賴會場網路

儲存
USB 3.2 SSD 1 TB（主）
1
離線包，內容見下

儲存
USB SSD（備份）
1
離線包第二份

電源
多孔延長線 3 m
2
Thor、victim 筆電、隊友筆電

電源
所有筆電充電器
全部
兩天長時間運作

量測（選配）
插座式電力計
1
Thor 整機功耗交叉驗證；若電源是插牆式才用得上

要先向主辦確認： 三款機型都是端子台直流輸入（KT78／KT68 為 9–48 VDC，PE3000N 為 12–60 VDC），「只提供主機」是否包含電源供應器。另外確認 sudo 權限、可否自帶 switch，以及台上能否用有線連到會場網路。
離線包內容：
• Thor 可用的推論映像：TensorRT-LLM Jetson 版或 TensorRT Edge-LLM，加上 jetson-containers vLLM r38 作退路
• 模型：Nemotron-3-Nano-30B-A3B-NVFP4、Nemotron-3-Nano-4B、bge-small-en-v1.5（HF cache tar）
• nginx:latest 映像兩種架構：amd64 給 victim 筆電，arm64 給「victim 回到 Thor 本機」的退路
• uv 快取與 uv.lock 對應的 wheels、repo 完整副本
• 回放素材：Spark 上的歷史演練紀錄，比賽中再補上 Thor 的真實紀錄
Repo 修改清單
共 14 項。P0 是 demo 跑得起來的最低要求，P1 是第二幕的四層調整，P2 是加分。「何時做」依揭露策略分：賽前只做基礎設施，審批閘門與能耗治理留在黑客松期間完成，與初賽簡報第 12 頁一致。
#
優先
檔案
改什麼
服務哪一段
驗收
何時做
1
P0
scripts/victim.py（新）、faults.py、agent.py
遠端 victim：docker 指令走 DOCKER_HOST=ssh://，健康檢查改打 victim IP，設定檔讀寫經 ssh
全場
VICTIM_HOST=10.0.0.2 下九案 faults.py run 全過
賽前
2
P0
skills/ops/SKILL.md、nemoclaw/workspace/AGENTS.md、TOOLS.md、runbooks/upstream-502.md
拿掉寫死的 127.0.0.1:8880，一律叫 Agent 用 check_health
全場
grep -rn 8880 只剩 victim.py
賽前
3
P0
faults.py、victim/board/（新）
廠務看板頁 /board/；write_config 拒絕拿掉 /board/ 的設定
全場的視覺
BAD_CONF 仍觸發 [emerg]，九案回歸通過
賽前
4
P0
scripts/watchdog.py（新）
規則式巡檢，每 5 秒打一次健康檢查；連續兩次失敗就開事故並派工給 NemoClaw Agent
第一、二幕「沒人叫它修」
注入後 15 秒內自動開始，無人輸入
賽前
5
P0
nemoclaw-drill.sh
不再清空 ops-api.log；每場歸檔到 logs/runs/<時間>-<案例>/
回放、夜跑
一場跑完，目錄內有軌跡、審核、回報
賽前
6
P0
scripts/wan.sh（新）
down / up / status，用 iptables 封鎖公網出口，並計數 sandbox 的對外連線
斷網段落
down 後 curl 外部失敗，台上 dashboard 仍更新
賽前
7
P0
scripts/dashboard.py、dashboard.html（新）
四區畫面＋按鈕＋回放模式，詳見下方
全場
台上筆電經會場網路開啟，斷網後仍持續更新
賽前做骨架，10/22 補齊
8
P0
scripts/preflight.sh（新）
上台前一鍵檢查所有服務並預熱兩顆模型
上台前
全綠才上台
賽前
9
P0
scripts/serve-trt.sh、trt/*.yaml
Thor 映像與 runtime；支援第二個實例（4B，不同 port）；依機型分 128 GB / 64 GB 兩份記憶體設定
全場
兩個 server 同時健康，峰值記憶體有紀錄
10/21–22
10
P1
scripts/approval.py（新）、agent.py
人工審批閘門，接在審核代理 APPROVE 之後
第二幕階段 3
upstream-500 停在待審批，核准後完成；逾時則拒絕
10/22
11
P1
scripts/tier.py（新）、oai_shim.py、agent.py、reviewer.py
Tier 控制器與 nvpmodel 連動
第二幕階段 2
upstream-500 軌跡出現 Tier 1→2→1；nginx-stopped 全程 Tier-1
10/22
12
P1
scripts/power.py（新）
讀 tegrastats 寫入功耗紀錄，事故開始到結束積分出 Wh
功耗曲線、數據頁
曲線即時顯示；每場能耗寫進歸檔目錄
10/22
13
P1
scripts/overnight.sh（新）
夜跑：排程注入，A/B 兩種 Tier 模式，審批設為 auto
數據頁
隔天早上有兩組可比的數據
10/22 晚上
14
P2
score.py
計分卡新增 Tier、能耗、審批三欄
數據頁
docs/scorecard.md 出現新欄位
10/23 早上
1. 遠端 victim（#1–#3）
新增 scripts/victim.py，集中所有跟 victim 位置有關的東西，faults.py 和 agent.py 都從它取：
• VICTIM_HOST 為空時一切照舊；有值時，ops_api 與 faults 的環境設 DOCKER_HOST=ssh://ops@<VICTIM_HOST>，agent.sh() 裡所有 docker 指令自動打到遠端。
• HEALTH 從 127.0.0.1:8880 改成 http://<VICTIM_ADDR>:8880；faults.http() 同步改。http_check 本來就允許私有網段，不用動。
• 最大的坑：bind mount 的路徑是在 docker host 上解析的。SPECS 裡的 -v {CONF_DIR} 必須是 victim 筆電上的路徑，read_config、write_config、faults.inject 寫 default.conf 都要改成 victim.read_conf() / victim.write_conf()，遠端時用 ssh host 'cat > path'。
• SPECS 的 -p 127.0.0.1:8880:80 改成綁監控網段的 IP，例如 10.0.0.2:8880:80。
• ~/.ssh/config 開 ControlMaster auto 與 ControlPersist 10m，每次 docker 呼叫只多零點幾秒。SSH key 只放 Thor host 端，sandbox 裡的 Agent 拿不到，台上可以講這點。
• 主機層工具（disk_usage、top_processes、kill_process、cleanup_disk、service_status、journal_tail）仍作用在 Thor 上，這次不改；抽卡的四個案例和 upstream-500 都用不到它們。
• 看板頁：GOOD_CONF 加一段 location /board/，掛載 victim/board/ 靜態頁，頁面每 2 秒打 /api/ 顯示紅綠燈。BAD_CONF 是刪掉 return 200 那行的分號，加 /board/ 不影響故障注入；write_config 要多一條規則拒絕少了 /board/ 的設定。
2. watchdog 與歸檔（#4、#5）
目前 nemoclaw-drill.sh 是注入後由腳本直接送 prompt，台上看起來像人在下指令。改成 watchdog.py 常駐巡檢：每 5 秒檢查 / 與 /api/，連續兩次失敗就寫一筆事故開始到 logs/incidents.jsonl，用固定的警報句呼叫 nemoclaw edge-agent agent，事故結束後才解鎖。
巡檢本身是規則式、不呼叫模型，這比投影片第 11 頁寫的「Tier-1 巡檢」更省電，建議簡報措辭改成「平時規則巡檢不推論，異常才由 Tier-1 接手」。
nemoclaw-drill.sh 第 6 行的 : > logs/ops-api.log 會把上一場的紀錄清掉，改成記下起始行號，結束時把本場的片段、reviews.jsonl、審批、Tier、功耗與 Agent 的回報一起複製到 logs/runs/<時間>-<案例>/。回放模式與夜跑都讀這個目錄。
3. 斷網（#6）
wan.sh down 在 OUTPUT 與 FORWARD 加規則，拒絕往 RFC1918 以外位址的流量，會場內網與監控網段不受影響。對外連線計數只算 FORWARD 鏈上「來自 sandbox bridge 網段」的封包，README 第 10 節提到的是 172.24.0.0/16，現場要再確認。只算 sandbox 是為了避免 OS 的 NTP、套件更新讓計數器跳動，在台上解釋不清。iptables 需要 sudo，sudoers 只開 iptables 和 nvpmodel 兩個指令。
4. Dashboard（#7）
dashboard.py 用標準函式庫起 HTTP server，綁會場網段的介面，單頁 HTML 透過 SSE 更新。
• 服務狀態區：反向代理 victim 的 /board/，台上連不到監控網段也看得到看板。
• 軌跡區：tail ops-api.log 與 reviews.jsonl，把 refused:、review-rejected、denied: 標成不同顏色。這一區用來回答「你們是不是只裝了 OpenClaw」。
• 功耗區：power.py 的即時曲線，上面疊 Tier 與 nvpmodel 模式的變化。
• 網路與審批區：WAN 狀態、對外連線計數、審批通道、待審批卡片與核准／拒絕按鈕。
• 按鈕：注入（四張抽卡加 upstream-500）、reset、切斷／恢復外網。
• 回放模式：讀 logs/runs/ 的某一場，依原始時間戳重播，畫面左上角固定顯示 REPLAY。
5. 人工審批（#10）
插在 agent.py 的 reviewed() 之後：審核代理 APPROVE 才進人工審批，所以順序是程式鑰匙 → 審核代理 → 人。approval.request() 寫一筆待審批到 logs/approvals/pending.json，然後每秒輪詢，最多等 APPROVAL_TIMEOUT（建議 180 秒），dashboard 按鈕寫入決定。
• 逾時一律當拒絕（fail-closed），回給 Agent 的文字要叫它升級給人。
• 模式：APPROVAL=manual（demo）、auto（夜跑，紀錄寫「benchmark 自動核准」）、off。
• 阻塞發生在 ops_api 的 MCP 呼叫內；OpenClaw 的 MCP timeout 目前設 600 秒，夠用。
• 審批通道顯示：沒做 Slack 的話就固定顯示「Jetson 本機」，不要演一個不存在的切換。
6. Tier 控制器（#11）
oai_shim.py 本來就攔截 NemoClaw 的每一次推論，是放 Tier 路由最省事的位置。
• tier.py 維護 logs/tier-state.json：set_tier(n, reason) 寫入狀態、呼叫 sudo nvpmodel -m <模式>、記一筆到 logs/tier.jsonl。
• 升級條件用客觀訊號，不用模型自評信心：with_health() 回報「動作後仍失敗」；即將呼叫 reviewed()；同一事故閘門拒絕兩次以上。降級條件是健康恢復，也就是 with_health() 和 check_health 重置事故狀態的那兩處。
• shim 每次請求讀狀態檔，在 UPSTREAM_T1 與 UPSTREAM_T2 之間選。reviewer.py 的 VLLM_URL 固定指向 Tier-2，審核本來就是重判斷。
• TIER_MODE=adaptive|always2 給夜跑做 A/B。
• 要先驗證：對話中途換模型時，4B 和 30B 的 tool parser 與 reasoning parser 是否都能被同一個 shim 正確處理。
7. 功耗（#12）
power.py 包一層 tegrastats --interval 500，用寬鬆的正則抓 名稱 現值mW/平均mW，因為 Thor 的電源軌名稱可能和 Orin 不同。每列寫入 logs/power.jsonl，事故能耗用 incidents.jsonl 的起訖時間積分。數據頁與投影片都要標明這是模組電源軌，不是整機。
時程對照
10/22 傍晚是唯一的硬性檢查點：第二幕在 Thor 上連續成功三次才保留 Tier 與 nvpmodel，否則砍掉，只保留人工審批，能耗改用口頭與夜跑數據交代。
現在到 10/20（在 Spark 上做，全部列入既有成果揭露）
[ ] #1–#3 遠端 victim 與看板頁，九案回歸通過
[ ] #4 watchdog、#5 歸檔、#6 wan.sh、#8 preflight
[ ] #7 dashboard 骨架：四區顯示與注入、reset、斷網按鈕
[ ] victim 筆電：Ubuntu、docker、nginx:latest amd64、Thor 的 SSH 公鑰
[ ] 離線包兩份：Thor 推論映像、4B 模型、HF cache、wheels
[ ] 向主辦確認：電源供應器、sudo、自帶 switch、台上能否有線連到會場網路
[ ] 更新簡報「既有成果揭露」，列入上面這些基礎設施
10/21 工作坊（3 人）
[ ] 確認配到的機型：T5000 128 GB 還是 T4000 64 GB，決定用哪份記憶體設定
[ ] nvpmodel -q --verbose 抄下模式表，實測切換延遲、哪些模式需要重開機
[ ] 確認 MIG 能否切給兩個推論實例；不行就把簡報裡「架構保證」的措辭改掉
[ ] 決定推論引擎：TensorRT-LLM Jetson 版、TensorRT Edge-LLM，或 vLLM 退路
[ ] NemoClaw onboard（Thor 上帶 NEMOCLAW_SANDBOX_GPU=0）
[ ] 記下 tegrastats 在 Thor 上的電源軌名稱
[ ] 和駐點網路組實測：從台上位置連到座位的 Thor
10/22 黑客松第一天
[ ] 上午：完整部署到 Thor，遠端 victim 九案回歸
[ ] 下午：#10 人工審批、#11 Tier 控制器、#12 功耗、#7 dashboard 補齊
[ ] 傍晚：第二幕連續三次成功的檢查點
[ ] 晚上：完整排練一次「冷開機即斷網」，抓出任何偷偷連外的服務
[ ] 睡前：啟動 #13 夜跑，A/B 各佔一半時間
10/23 黑客松第二天
[ ] 早上：收夜跑數據，更新投影片數據頁；#14 計分卡
[ ] 排練三次，其中一次從台上位置實際操作
[ ] 挑一場最乾淨的 Thor 真實演練，同步到簡報筆電與備援筆電當回放素材
[ ] 12:30 code freeze
[ ] 換場前 15 分鐘：跑完「上台前檢查」