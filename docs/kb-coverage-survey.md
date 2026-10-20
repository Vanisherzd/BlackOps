# 知識庫覆蓋度盤點 ＋ 機房故障 survey（2026-10-20）

範圍：`kb/`（v3 預設、v4 卡片）＋ `runbooks/`，對照實務機房故障。只讀未改 `kb/`；探測腳本只跑 CPU 上的 BM25 檢索，沒碰 DGX 上的實驗。

## TL;DR

1. **KB 量大但面窄。** 1,612 筆中 1,557 筆是 microservice RCA benchmark 案例（Cloud-OpsBench、RCAEval、AIOps2025：Online Boutique／Sock Shop／Train Ticket／TiDB），內容是 metrics 異常和「哪個 service 壞了」，**沒寫怎麼修**。真正可執行的是 31 張解法卡（17 張 Linux/K8s ＋ 14 張 docker repair card）、17 筆演練經驗，加上 `runbooks/` 的 11 份。
2. **實測覆蓋率：70 條真實機房錯誤句，只有 14 條（20%）推得出卡片**（`knowledge_note` 走的 v4 phrase gate）。完全沒覆蓋的有：硬體／BMC／電源（0/8）、虛擬化 Proxmox/Ceph（0/3）、K8s 控制面（0/6）、L2/L3 網路（2/12）、時間同步（0/1）。
3. **企劃書自己的情境有缺口。** §2.2 L1 列的「防火牆規則誤設」沒有卡片；§6.1 拓撲畫了 Proxmox，KB 一筆 Proxmox 都沒有。業界統計最大宗的「電源」「變更引發」兩類在 KB 也是零。題目叫 *Blackout*，卻沒有任何 UPS／停電卡，評審很容易問到。
4. **檢索 gate 有兩類失誤。** (a) 漏推：正確卡片明明是 BM25 第一名，卻被 phrase gate 擋掉（inode、`OOMKilled 137`、`CrashLoopBackOff`、`500 internal server error`）。(b) 誤推：`SSH permission denied publickey` 推到 `card-docker-upstream-content-4xx`，那張卡會建議重建 upstream，屬於危險誤導。
5. **建議**：補 3 組卡，共約 25 張。A 是 Linux 主機層，可以注入故障、可以修；B 是硬體帶外層，只負責辨識、收證據、升級，**不修**；C 是平台層（Proxmox／K8s）。A 組另外附了 8 個能在 victim 上注入的盲測案例。詳見 §4。

---

## 1. 現況盤點

| 層 | 內容 | 數量 | Agent 用得到嗎 |
|---|---|---|---|
| runbooks/ | demo stack 專用 SOP（nginx、upstream 5xx/502/504、detached、name-resolution、OOM、disk、crashloop、missing） | 11 | ✅ `rag.search` 主路徑，閘門依據 |
| v4 repair cards | `kb/cards/docker_stack_cards.json`：14 個公開故障族，對到本 stack 的工具 | 14 | ✅ 只有 `KB_RETRIEVAL=v4` 才推 |
| 解法卡（v1/v3） | Linux：disk log、inode、systemd loop、OOM、DNS resolver、TLS 過期、port 未監聽；K8s：DiskPressure、crashloop×5、DNS×4 | 17 | ⚠️ K8s 卡是 `kubectl` 指令，本 stack 跑不了 |
| stack 經驗 | 演練中自主修好、根因正確的 run | 17 | ✅ v4 `proven_here` |
| benchmark 案例 | 微服務 RCA，metrics 語彙（`rrt`、`node_memory_usage_rate`…） | 1,557 | ❌ 只有標題，沒有修法；`docs/kb-repair-cards.md` 統計 1,581 筆裡只有 85 筆提到修復字眼 |
| 拓撲 | 服務依賴 | 7 | 微服務 benchmark 的，不是本 demo |

情境標籤分布（v4）：`other` 1,517、`dns` 45、`disk_full` 31、`crashloop` 19。企劃書主打的三個場景合起來不到 6%。

已知效果（既有 eval，見 `docs/kb-eval-v3*.md`、`docs/kb-repair-cards.md`）：
- 公開 held-out：RAG 和無 RAG 差距在雜訊內（strict 22–29%）。unseen 時，label@1 掉到和查表一樣的水準（11%）。
- 盲測 batch 3：v3 4/12、v4 5/12。卡片只在 2/12 run 送到 agent 手上。瓶頸不在 KB 內容，在於 agent 很少去查，加上 guard 的條件只為已知故障寫。

**結論：KB 目前的價值在「demo stack 那十幾類故障」。對「機房大部分錯誤」來說，可修的知識幾乎都還沒進來。**

## 2. 覆蓋率探測

方法：手寫 70 條真實日誌／症狀句（log 原文語氣），丟進 agent 實際用的 `KnowledgeBaseV2(corpus_v4, **AGENT_DEFAULTS)`，`solution_k=6`，記錄 phrase gate 有沒有推卡，以及 BM25 第一名。腳本與原始輸出在 job tmp（`probe.py`、`probe.json`），可以重跑。

| 類別 | 推卡／總數 | 備註 |
|---|---|---|
| 磁碟／檔案系統 | 1/10 | 只有 `No space left` 推得出卡。inode 卡存在卻沒推；read-only remount、deleted-but-open、overlay2、RAID、SMART、NFS、LVM thin 全部沒卡 |
| 記憶體 | 2/3 | `OOMKilled exit code 137` 沒推 |
| CPU／kernel | 1/2 | `hung_task` 沒卡 |
| 程序資源 | 1/3 | EMFILE 推到 slow-dependency（勉強相關）；pids／fork EAGAIN、zombie 沒卡 |
| 服務層 | 4/8 | 502/504 由 runbooks 補上；`nginx emerg`、`address already in use` 沒推 |
| 網路 | 2/12 | conntrack full、MTU、ARP 衝突、link flap、bond、iptables、ephemeral port、DHCP、default route 全部沒卡 |
| TLS／時間 | 1/3 | unknown CA、NTP skew 沒卡 |
| 權限 | 2/4 | **兩條都推錯**：SSH publickey、檔案 ownership 都推到 upstream 4xx 卡 |
| 硬體／BMC | 0/8 | MCE、ECC、溫度、PSU、風扇、GPU Xid、PCIe AER、NVMe：零 |
| K8s | 0/6 | CrashLoopBackOff 卡存在卻沒推；ImagePull、NotReady、Pending、etcd、PVC 沒卡 |
| Docker daemon | 0/3 | daemon 掛掉沒卡；paused、detached 靠 runbooks |
| 虛擬化 | 0/3 | Proxmox quorum、VM lock、Ceph：零 |
| 其他 | 0/5 | cron、apt/dpkg、boot/fstab、Java GC、app 500 |
| **合計** | **14/70（20%）** | |

注意：這些是我寫的句子，不是 agent 的實際 query。agent 常用光禿的 "429"、"504" 去查，那種 query 命中率更低（見 `docs/kb-repair-cards.md`）。所以 20% 只能當趨勢看。

## 3. 實務故障 survey

### 3.1 公開統計怎麼說

| 來源 | 重點 | 對本案的意義 |
|---|---|---|
| Uptime Institute *Annual Outage Analysis 2025* | 造成重大影響的停機，**電源**仍是第一大原因；IT／網路類在 2024 年升到 23%；人為錯誤中「沒照程序做」的比例上升約 10 個百分點 | Blackout 主題最該有電源與 UPS 卡；SOP 遵循本身就是 agent 的賣點 |
| Liu et al., *What bugs cause production cloud incidents?* HotOS'19（Azure 112 件高嚴重度） | 約 40% 是軟體 bug；常見的是 timeout、錯誤處理、資料格式不相容 | upstream 5xx／timeout 族已有，可補「重試風暴」 |
| Azure 多年 postmortem 研究（2000+ 件、450+ 服務） | 多數事故有多個 contributing factor，共 346 種根因類別，分布在 code／config／infra | 卡片要能並列多個假設，不要只給單一答案 |
| Gunawi et al., *Why Does the Cloud Stop Computing?* SoCC'16；*Fail-Slow at Scale* FAST'18 | 公開雲停機：升級／設定變更、硬體、負載是主因；「變慢但沒掛」的硬體（fail-slow）特別難診斷 | 補「最近有什麼變更」卡、fail-slow 卡（NVMe／NIC 降速） |
| Meta Llama 3 訓練報告（16,384 H100，54 天 419 次中斷） | 約 58.7% 是 GPU 問題，HBM、SRAM、網路交換器是大宗；整體約 2/3 是硬體 | 機房裡 GPU 節點越來越多，**Xid／ECC 卡**可以直接對應 Thor／DGX 本身 |
| NCSA Delta GPU 韌性研究（arXiv 2503.11901） | 除了 MMU 和 NVLink 錯誤，其他 GPU 錯誤 app 層幾乎接不住，job 近 100% 失敗 | GPU 硬體錯誤卡要寫成辨識＋drain＋升級，不要重啟 |

### 3.2 實務故障分類 vs. KB

✅ 有卡且推得出 ⚠️ 有卡但推不穩或只有 K8s 版 ❌ 沒有

| 層 | 實務常見故障 | KB |
|---|---|---|
| 電源／環境 | UPS 改用電池、市電中斷、PSU 冗餘失效、PDU 過載、溫度過高、風扇故障 | ❌ |
| 硬體 | ECC CE 持續增加→UE、MCE、磁碟 SMART 惡化、RAID degraded/rebuild、NVMe reset、PCIe AER、GPU Xid（48 DBE、79 fallen off bus、94/95 contained/uncontained ECC） | ❌ |
| 儲存／FS | 滿（bytes／inode）、deleted-but-open、EXT4/XFS 錯誤後變 read-only、docker data-root／container log 膨脹、NFS stale／hang、LVM thin 滿、hung task（D state） | ⚠️ 只有滿（bytes／inode） |
| 計算資源 | host OOM、cgroup OOM、CPU 飽和、EMFILE、pids 上限、zombie、swap thrash | ⚠️ 前三項有 |
| 網路 L2/L3 | link down/flap、bond／LACP failover、MTU 不一致、ARP／重複 IP、default route 不見、conntrack 表滿、ephemeral port 耗盡、iptables 誤設 | ❌（只有 latency/loss） |
| 網路服務 | DNS resolver／上游 DNS、DHCP、**NTP 漂移**（氣隙網段沒有公網 NTP，最常見）、內部 CA／憑證過期、憑證鏈不完整 | ⚠️ DNS、憑證過期有 |
| OS／服務 | systemd restart loop、dependency failed、start timeout、unit masked、設定語法錯、port 衝突、cron 沒跑、套件升級弄壞依賴、fstab 寫錯開不了機 | ⚠️ restart loop 有 |
| 容器 | daemon 無回應、image 缺失、network detach、paused、OOM、restart policy 迴圈 | ✅ runbooks 為主 |
| K8s | CrashLoop、ImagePull、Node NotReady、Pending、PVC、etcd、**kubeadm 憑證一年到期** | ⚠️ 卡片有，但推不出來，而且是 kubectl |
| 虛擬化 | Proxmox quorum 掉、VM lock、local-lvm 滿、vzdump 備份失敗、Ceph OSD down／HEALTH_WARN | ❌ |
| 人為／變更 | 最近一次部署或設定變更、沒照程序做 | ❌（`restore_config` 工具有，但沒有「先查最近變更」的卡） |
| 應用 | 連線池耗盡、重試風暴、GC pause、rate limit | ✅ slow-dependency／5xx 卡 |

## 4. 建議補進 KB 的內容

原則沿用現有紀律（`docs/kb-repair-cards.md`）：
- 每張卡要有 CHECK（R 級）、FIX（標 W/D）、`escalate`。
- symptom phrase 要過 `kb-cards.py phrases`。
- 寫完先 freeze，再用盲測 A/B。
- 只能辨識的卡（硬體類）FIX 寫 `none`，`escalate` 寫清楚要交給人的證據清單。這類卡的價值是**防止 agent 對著壞硬體一直 restart**，也就是降低 made-worse。

### A 組：Linux 主機層，可注入、可修（優先）

| # | 卡片 | CHECK（R） | FIX | 能否在 victim 注入 |
|---|---|---|---|---|
| A1 | deleted-but-open（df 滿、du 不滿） | `lsof +L1`、`df` vs `du` | 重啟持有該檔的 process（D） | ✅ container 內開著檔再刪 |
| A2 | filesystem 變 read-only | `dmesg \| grep -i 'EXT4-fs error\|remount'`、`findmnt -o OPTIONS` | 不寫入，升級 | ✅ `docker run --read-only` / tmpfs ro |
| A3 | docker data-root／container json log 膨脹 | `docker system df`、`du /var/lib/docker/containers/*/*-json.log` | prune build cache／dangling（W）、設 log-opts（D） | ✅ 狂寫 stdout |
| A4 | fd 耗盡（EMFILE） | `/proc/<pid>/limits`、`ls /proc/<pid>/fd \| wc -l` | 重啟 process；調 ulimit（D） | ✅ `--ulimit nofile=` |
| A5 | pids 上限／fork EAGAIN | `cat /sys/fs/cgroup/pids.*` | 重建時放寬 `--pids-limit` | ✅（blind 4 已有 upstream-pids-limit，補卡即可） |
| A6 | port 衝突（address already in use） | `ss -ltnp` | 停掉佔用者或改 port（D） | ✅ 先佔 port |
| A7 | conntrack 表滿 | `dmesg \| grep conntrack`、`sysctl net.netfilter.nf_conntrack_count/max` | 調 max（D）、找出源頭 | ⚠️ 要 host 權限 |
| A8 | ephemeral port 耗盡／TIME_WAIT | `ss -s`、`sysctl net.ipv4.ip_local_port_range` | 改用連線池／調 range（D） | ⚠️ |
| A9 | **iptables／防火牆誤設**（企劃書 L1 原文） | `iptables -S`、`nft list ruleset`、對照 `curl` | 回滾到上一版規則（D） | ✅ 在 victim 加 DROP 規則 |
| A10 | default route／MTU 不一致 | `ip route`、`ping -M do -s 1472` | 回復路由或 MTU（D） | ✅ container netns |
| A11 | **時間漂移／NTP 未同步**（氣隙必備） | `timedatectl`、`chronyc tracking` | 指向內網 NTP（D）；也是憑證「not yet valid」的根因 | ⚠️ |
| A12 | 憑證鏈／unknown CA、**到期前預警** | `openssl s_client -showcerts`、`openssl x509 -enddate` | 補中繼憑證（D） | ✅ nginx 掛自簽憑證 |
| A13 | 「最近變更了什麼」 | `/var/log/apt/history.log`、`docker inspect --format '{{.Created}}'`、設定檔 mtime、`git log` | `restore_config`（已有工具） | ✅ 通用 |
| A14 | hung task／D state（I/O stall、NFS） | `dmesg \| grep 'blocked for more than'`、`ps -eo stat \| grep D` | 不 kill（沒用），升級 | ❌ |

### B 組：硬體／帶外層，只辨識＋收證據＋升級（強化 BMC 敘事）

| # | 卡片 | 證據 | escalate 內容 |
|---|---|---|---|
| B1 | **UPS 改用電池／市電中斷** | `upsc <ups>`（NUT）、`apcaccess` | 預估剩餘時間；依優先序做 graceful shutdown 計畫（D、須審批） |
| B2 | PSU 冗餘失效 | `ipmitool sel list`、`ipmitool sdr type 'Power Supply'` | 換 PSU；不可重開機 |
| B3 | 溫度過高／風扇故障／thermal throttling | `ipmitool sdr type Temperature/Fan`、`dmesg \| grep -i thrott`；Jetson：`tegrastats` | 降載、通知廠務 |
| B4 | ECC CE 上升／UE、MCE | `ras-mc-ctl --errors`、`edac-util`、`dmesg \| grep -i mce` | 排程換 DIMM；UE 時 drain |
| B5 | 磁碟 SMART／RAID degraded | `smartctl -H -A`、`cat /proc/mdstat`、`mdadm --detail` | rebuild 期間不要加 I/O |
| B6 | NVMe reset／fail-slow | `dmesg \| grep nvme`、`nvme smart-log`、`iostat -x` | 換盤 |
| B7 | **GPU Xid**（48/63/64/79/94/95） | `dmesg \| grep NVRM`、`nvidia-smi -q -d ECC,PAGE_RETIREMENT` | drain 節點、回報 Xid 碼 |
| B8 | NIC link flap／bond failover | `ip -s link`、`ethtool`、`/proc/net/bonding/*` | 換線、換光模組 |
| B9 | PCIe AER | `dmesg \| grep AER` | 持續出現就升級 |

這組在 demo 用不到，但直接回答評審的「機房真的會壞的是硬體，你們只會修 nginx？」。資料可以從 BMC 拿，正好對應企劃書 §2.1 的 BMC 類比。

### C 組：平台層（企劃書拓撲有畫，就該有）

- Proxmox：corosync quorum lost、VM `lock` 卡住（`qm unlock`）、local-lvm thin 滿、vzdump 失敗。
- Ceph：OSD down、PG degraded、near-full。
- K8s：ImagePullBackOff、Node NotReady、Pending/Insufficient、PVC Pending、**kubeadm 憑證到期**（`kubeadm certs check-expiration`）、etcd 延遲。現有 K8s 卡要修 phrase，因為 CrashLoopBackOff 本身就推不出來。

如果時間不夠，C 組可以只寫「辨識＋escalate」版本。簡報上寫「拓撲支援範圍」時，要老實寫成「Docker／Linux 可修，Proxmox／K8s 能辨識」。

### D 組：檢索修補（不用加內容）

1. **漏推**：phrase bar 8.0 把 inode、`OOMKilled 137`、`CrashLoopBackOff` 擋掉了。替這三張卡補 agent 會講的 phrase（`exit code 137`、`back-off restarting`、`df -i`），不要調低 bar。
2. **誤推**：`permission denied` 這個 phrase 太泛，SSH／檔案權限都會推到 upstream 4xx 卡。改成 `403 forbidden` 這種只在 HTTP 情境出現的 phrase。
3. 把 §2 的 70 條句子固定成 regression set，每次改卡都重跑，記錄 推對／推錯／沒推。

### 建議順序（到 10/23 code freeze 前）

1. **先做 D**：半天，不用新增故障，直接提升 v4。
2. **再做 A9、A13、A1、A3、A4、A6**：這幾張能注入，可以跑一輪盲測，給簡報一個「新卡 → 盲測」的數據點。
3. **B1、B7 各寫一張**：只放簡報上的「辨識＋升級」範例，扣題 Blackout 和 GPU 機房。
4. C 組和其餘 B 組：比賽後再做。簡報 Q&A 時說「列在 roadmap」。

**demo 前不要切換 `KB_RETRIEVAL` 預設值。** v3 → v4 的效果還在雜訊內。現在去動 retrieval，等於讓已經穩定的第二幕承擔風險。

---

## 5. Demo 最低條件檢查（HEAD 189de86 ＋ 未 commit 修改，只讀檢查）

> **2026-10-20 晚更新**：#4、#5、#6、#7、#8 與 P1 的 #10、#11、#12 都已用新檔補上骨架，預設皆為 inert 或 opt-in。#3 只完成看板頁本身。尚未完成的是對既有檔的修改（#1、#2、#3 的掛載、#10/#11 的接線），因為當時 DGX 正在跑實驗，沒有動既有檔。這些修改的精確 diff 與套用順序見 `docs/demo-pending-edits.md`。下表為更新前的狀態。

**結論：執行手冊列的 9 項 P0 一項都還沒完成。** 手冊裡提到的新檔都不存在：`victim.py`、`watchdog.py`、`wan.sh`、`dashboard.py/.html`、`preflight.sh`、`approval.py`、`tier.py`、`power.py`、`overnight.sh`、`victim/board/`。程式全部仍假設 victim 和 agent 在同一台機器、走 `127.0.0.1:8880`。

| # | 項目 | 狀態 | 證據 |
|---|---|---|---|
| 1 | 遠端 victim | ❌ | `faults.py:126`、`agent.py:30` 寫死 HEALTH；`faults.py:137` 綁 `127.0.0.1:8880`；設定檔在本機讀寫（`agent.py:610,626,637`）；沒有 VICTIM_HOST／DOCKER_HOST |
| 2 | prompt 去掉 8880 | ⚠️ | `skills/ops/SKILL.md:10`、`nemoclaw/workspace/AGENTS.md:6`、`TOOLS.md:6` 仍寫 8880；`runbooks/upstream-502.md:4` 叫 agent 自己 curl，這在 sandbox 裡做不到；`oai_shim.py:183` 預設值也是 8880 |
| 3 | /board/ 看板 | ❌ | GOOD_CONF（`faults.py:34-40`）只有 `/` 和 `/api/` |
| 4 | watchdog | ❌ | 只在 `docs/agent-design.md:343` 提到 |
| 5 | 不清 log＋歸檔 | ❌ | `nemoclaw-drill.sh:17` 仍是 `: > logs/ops-api.log`（未 commit 的 diff 只是移動這行位置） |
| 6 | wan.sh | ❌ | — |
| 7 | dashboard | ❌ | — |
| 8 | preflight | ❌ | — |
| 9 | Thor serve | ⚠️ | `serve-trt.sh` 已能用 env 開第二個實例；缺 `--runtime nvidia`、4B 設定與 parser、128／64 GB 的 yaml；這台 HF cache 沒有 4B 模型 |
| P1 | approval／tier／power／overnight／score 欄位 | ❌ | — |

**已經可用的部分**：四張抽卡案例加 upstream-500 都在 `faults.py` 裡，各有 runbook，`reset` 都涵蓋得到。小缺口：`nemoclaw/workspace/AGENTS.md:13` 的 Change 工具清單漏了 `recreate_stack` 和 `restore_config`。MCP 有提供這兩個工具，但第二幕要用到 `recreate_stack`，建議補進清單。

**未 commit 的修改**：`TRIAGE=1`（drill.sh）、`SHIM_ADAPTIVE=1`（shim）、`triage.py`。三個都是 opt-in，只讀，預設關閉，不會弄壞任何東西。`triage.py` 之後可以拿來產生 watchdog 的警報文字。

**今天就能跑的退路 demo**：`faults.py setup` → `scripts/nemoclaw-drill.sh <case>`，在終端看 `logs/ops-api.log`。前提是 shim、ops_api 和推論 server 都已經起來。

**最小可 demo 路徑**（victim 先跑在 Thor 本機，暫不做 #1），依阻塞程度排序：

1. #9 Thor 上的推論 server。沒有它就沒有 demo，這台也驗證不了。0.5–1 天，加 10/21 實機驗證。
2. #5 歸檔。約 1 小時，現在每跑一場就清掉上一場，回放素材留不下來。
3. #8 preflight。20 行的版本就夠，1–2 小時。
4. #2 修 `upstream-502.md:4` 那行 curl，再補 AGENTS.md 的工具清單。1 小時。
5. #7 精簡版 dashboard：tail log 加注入／reset 按鈕。約半天。不做的話，退路是用終端跑。
6. #4 watchdog（3–4 小時）、#6 wan.sh（2–3 小時）、#3 /board/（2–3 小時）。
7. #1 遠端 victim：1–1.5 天，只有「兩台機器」的敘事才需要。

依手冊的時程，賽前（10/20）應完成 #1–#8，**實際落後約 3–4 人天**。建議 10/21 前先保住 2、3、4，加上 #9 的實機驗證。#1 遠端 victim 改到 10/22 上午再決定要不要做，不做就退回「victim 跑在 Thor 本機」的版本。

## 參考

- Uptime Institute, *Annual Outage Analysis 2025*: https://uptimeinstitute.com/about-ui/press-releases/uptime-announces-annual-outage-analysis-report-2025
- Liu et al., *What bugs cause production cloud incidents?* HotOS'19: https://www.microsoft.com/en-us/research/?p=610323 （摘要：https://blog.acolyer.org/2019/06/21/what-bugs-cause-cloud-production-incidents/）
- Azure multi-year postmortem taxonomy: https://par.nsf.gov//servlets/purl/10429072
- Microsoft Teams incident study SoCC'22: https://www.microsoft.com/en-us/research/?p=881646
- Meta AI hardware reliability: https://engineering.fb.com/2025/07/22/data-infrastructure/how-meta-keeps-its-ai-hardware-reliable/
- Characterizing GPU Resilience (NCSA Delta): https://arxiv.org/html/2503.11901v3
- Gunawi et al., *Why Does the Cloud Stop Computing?* SoCC'16；*Fail-Slow at Scale* FAST'18
- NVIDIA Xid errors: https://docs.nvidia.com/deploy/xid-errors/
