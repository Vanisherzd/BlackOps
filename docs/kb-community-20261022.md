# 知識庫擴充：公開 Q&A 社群層（2026-10-22 研究紀錄）

依 research-protocol 的階段與閘門記錄。每一節先寫「做之前決定的事」，再補「量到的結果」。

## Phase 0 — 問題、貢獻、先前工作

**問題。** `docs/kb-coverage-survey.md`（2026-10-20）量到：知識庫 1,730 筆文件裡 1,685 筆是 Kubernetes 微服務
RCA benchmark 案例（只有標題，沒有修法）；70 條真實機房錯誤句只有 14 條（20%）推得出卡片；硬體／BMC、虛擬化、K8s
控制面、L2/L3 網路、時間同步的覆蓋是零。舞台的五張牌全靠 `runbooks/` 與 v4 repair cards，演練中 agent 從未呼叫
`search_incident_knowledge`（2026-10-22 全部 drill log 確認）。也就是說，舞台路徑不缺知識；缺的是「舞台以外的症狀」
被問到時，工具只能回 `insufficient`。

**支援的決定。** 評審或現場問到 runbook 以外的故障（停電／UPS、GPU Xid、conntrack、NTP、Proxmox…）時，agent 的
知識工具要能給出有來源、有授權、標明「他站經驗、未在本機驗證」的修復假設，而不是空手。這決定「要不要抓公開資料進
KB」以及「抓進來後放哪一層」。

**貢獻類別。** 不是新方法。屬於「新的資料＋可重跑的量測」：把 Server Fault／Unix & Linux Stack Exchange 的已接受
答案做成一個**獨立的社群參考層**，只在 `search_incident_knowledge` 且沒有任何修復卡命中時回傳，並用預先登記的
探針句與 held-out 問題量它的覆蓋與誤導率。負面結果一樣有用：若 BM25 在這種文字上連類別都對不準，就不該進 KB。

**先前工作／既有量測，與本設計的關係。**
- v1 檢索有害（63 案例：弄壞的多於修好的），原因是 `matched` 用子字串誤配；v3 用 phrase gate 修好；v4 卡片端到端
  差異在雜訊內（`docs/kb-retrieval-v2.md`、`docs/kb-repair-cards.md`）。→ 新層**不碰** `knowledge_note`（自動推
  卡路徑）與既有 `retrieve()`，不改任何既有排名：另開一個索引實例，只在明確工具呼叫時追加一段。
- `docs/rag-rerank-plan-20261021.md`：先量第一階段 recall，再談 reranker。→ 本紀錄的 P2 就是第一階段的類別 recall。
- 2026-10-21 隊友已把 111 筆本機失敗紀錄與 7 張卡編進 corpus_v3/v4。→ 不重做；本層只補「舞台以外」。
- 公開 Q&A 做 IT 運維 RAG 是常見做法（Stack Exchange 資料長期被用於此類檢索研究）；本專案的新點只在量測與分層，
  不宣稱方法新穎。
- 資料來源與授權：Stack Exchange 內容為 CC BY-SA（2011-04-08 前 2.5、2018-05-02 前 3.0、之後 4.0），API 無需金鑰、
  每 IP 每日 300 次、每次 100 筆。保留題目連結、作者顯示名、授權版本以履行署名。

**Gate 0。** 貢獻：一個獨立、可關閉（`KB_COMMUNITY=0`）、有署名的社群參考層，加上預先登記的覆蓋／誤導量測；舞台
路徑以建構方式保證不變。通過（2026-10-22 01:10）。

## Phase 1 — 模型與可否證的預測（資料抓取前寫定）

**系統模型。** 既有 `KnowledgeBaseV2`（`kb/src/blackout_ops_kb/retrieval_v2.py`）對 `case`／`solution`／`topology`
做 BM25；`solution` 另有 phrase gate 決定 `knowledge_match.status ∈ {matched, insufficient}`。agent 兩條路：
`knowledge_note(query)`（自動，跟著 `docker_logs`／`search_runbooks` 走，只在 matched 時推一張卡）與
`search_incident_knowledge(query)`（明確呼叫，回前兩張卡＋三筆案例標題，沒命中時說 insufficient）。

**新層定義。** 文件集 C（community）：每筆 = 一題有已接受答案的 SE 問題。索引文字 t(d) = 標題 + 標籤 + 題目內文
的「症狀行」（含 error/failed/denied/refused/timeout/emerg/… 或在 `<pre><code>` 區塊內的行，至多 6 行×160 字）+
已接受答案前 300 字。查詢 q 用同一個 `tokens()`。評分為同一組 BM25 參數的獨立索引（IDF 只在 C 內計算，所以既有
文件的分數一個位元都不變）。顯示條件：`search_incident_knowledge` 的 `status != matched`，且候選與查詢至少共享 2 個
非泛用 token（否則視為「無參考」），最多 2 筆，每筆 ≤ 320 字答案摘錄 + 連結 + 分數，總量 ≤ 700 字，整體仍受 2,500
字上限。`knowledge_note` 不變。

**資料抓取規格（固定）。** 站台：serverfault（全部類別）、unix（Linux 主機層類別）。每個標籤一頁
`/search/advanced?accepted=True&sort=votes&pagesize=100&filter=withbody`；答案以 `/answers/{ids}` 每 100 筆一批。
保留：question_id、title、tags、score、creation_date、link、owner.display_name、content_license（API 有給就用，否則
依日期推）、題目內文純文字（≤1,500 字）、accepted_answer_id、答案純文字（≤1,500 字）、答案分數。去重以 question_id。
丟棄：沒有已接受答案、答案純文字 < 80 字。**標籤→類別**（優先序由上而下，第一個命中者為主類別）：

| 類別 | serverfault 標籤 | unix 標籤 |
|---|---|---|
| hardware | ipmi, hardware, ecc, nvidia, gpu, pcie, temperature, ups, power-supply, smart, raid, mdadm, hard-drive, nvme | nvidia, raid, mdadm, hard-disk |
| virtualization | proxmox, ceph, kvm-virtualization, libvirt | kvm, libvirt |
| k8s | kubernetes, kubectl, etcd, kubeadm | — |
| docker | docker, docker-compose, containers, overlayfs | docker |
| disk_fs | disk-space-utilization, filesystems, ext4, xfs, nfs, lvm, inode | disk-usage, filesystems, ext4, xfs, nfs, lvm, inode |
| memory_process | memory, out-of-memory, swap, ulimit, file-descriptors, zombie-process, kernel-panic, kernel, load-average | out-of-memory, swap, ulimit, limit, kernel-panic, kernel |
| tls_time | ssl, ssl-certificate, openssl, certificate, ntp, chrony, time, timezone | openssl, ntp, time |
| network | networking, iptables, nftables, firewall, routing, mtu, arp, bonding, conntrack, dhcp, vlan, linux-networking, dns, bind | networking, iptables, routing, dns |
| service | nginx, apache-2.4, systemd, port, haproxy, php-fpm, tcp | systemd, nginx |
| permissions | ssh, permissions, sudo, selinux | ssh, permissions, sudo, selinux |
| boot_packages | grub, boot, fstab, apt, dpkg, yum, cron, logrotate, rsyslog | grub, boot, fstab, apt, dpkg, cron |
| app | java, mysql, postgresql, redis | — |

**預測與通過條件（先寫，後量）。**

- **C1（建構檢查，非預測）** 載入社群層前後，`knowledge_note` 對探針句集合 Q_gap ∪ Q_stage 的輸出逐位元相同；
  `scripts/eval-hard-repairs.py` 仍 8/8 命中、10/10 反例不誤選；`search_incident_knowledge` 在卡片 matched 時不附社群
  參考。理論上必然成立，所以只是 wrapper 驗證，不算預測。
- **P2（held-out 類別 recall，非平凡）** 抓到的題目依主類別分層、固定種子抽 15% 作 held-out，**不進索引**。查詢 =
  held-out 題目的標題＋症狀行（**不含標籤與答案**，因為 agent 的查詢也沒有這兩樣）。量 top-1 社群文件的主類別 = 查詢題目主類別的比例（category@1）。基線：多數類別預測器
  （= 最大類別占比，預期 0.15–0.25）、隨機（1/12）。**通過：category@1 ≥ 0.60 且 ≥ 2× 多數類別基線。** 不通過就回
  Phase 1 改索引文字（例如拿掉答案摘錄），並在此註明是事後修改。
- **P3（舞台以外探針句，描述性）** Q_gap = 下列 38 句（抓資料前寫定，取材自 survey 的類別表，用 log 口吻改寫，不是
  從 SE 抄的）。量：top-1 社群參考的類別正確比例，與「有回參考但類別錯」的比例（誤導）。**目標：類別正確 ≥ 0.70，
  誤導 ≤ 0.15。** 今日基線：38 句全部沒有任何參考（層不存在）。句子是作者寫的，只能當趨勢。
- **P4（舞台不變，端到端）** 載入社群層後 `scripts/drill-all.sh` 五張牌 5/5，且 `logs/ops-api.log` 無
  `search_incident_knowledge` 呼叫（與今晚一致）。**通過：5/5。**
- **延遲** 明確工具呼叫多出的社群檢索 ≤ 50 ms（純 Python BM25，≤ 5k 文件）。

**Q_stage（舞台路徑，C1 用）**：`nginx stopped exited 0`；`connect() failed (111: Connection refused) while connecting to upstream`；
`container edge-victim-app removed no such container`；`nginx: [emerg] unknown directive "proxy_pas" in /etc/nginx/conf.d/default.conf`；
`upstream returned 500 internal server error on /api/`；`SSH permission denied (publickey)`（survey 已知誤推句，描述性）。

**Q_gap（38 句，類別標籤）**：

| # | 類別 | 句子 |
|---|---|---|
| 1 | disk_fs | EXT4-fs error (device nvme0n1p2): ext4_journal_check_start: Detected aborted journal; remounting filesystem read-only |
| 2 | disk_fs | df shows / 100% used but du -sh /* adds up to half of that; a deleted log file is still held open by a process |
| 3 | disk_fs | docker: write /var/lib/docker/containers/<id>/<id>-json.log: no space left on device; container json logs grew to 40 GB |
| 4 | disk_fs | nfs: server storage01 not responding, still trying; processes stuck in D state |
| 5 | disk_fs | LVM thin pool data is 100% full, writes fail with input/output error |
| 6 | memory_process | accept4: Too many open files (EMFILE) in nginx worker; worker_connections exceed open file resource limit |
| 7 | memory_process | fork: retry: Resource temporarily unavailable; cgroup pids.max reached |
| 8 | service | nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address already in use) |
| 9 | service | systemd: start request repeated too quickly for app.service; Failed with result 'start-limit-hit' |
| 10 | network | kernel: nf_conntrack: table full, dropping packet |
| 11 | network | ping -M do -s 1472 fails with message too long, mtu=1400; large transfers hang while ssh works |
| 12 | network | no default route after reboot; ip route shows only the link-local subnet, external hosts unreachable |
| 13 | network | bond0: link status definitely down for interface eth1, disabling it; LACP partner not responding |
| 14 | network | connect: cannot assign requested address; thousands of sockets in TIME_WAIT, ephemeral port range exhausted |
| 15 | network | iptables rule added by mistake drops traffic to port 443; curl connection timed out but the service is listening |
| 16 | tls_time | chronyc tracking: Leap status: Not synchronised; clock is 11 minutes behind and TLS handshakes fail with certificate not yet valid |
| 17 | tls_time | curl: (60) SSL certificate problem: unable to get local issuer certificate; intermediate certificate missing from the chain |
| 18 | tls_time | x509: certificate has expired or is not yet valid; kubeadm certs check-expiration shows the apiserver certificate expired |
| 19 | hardware | NVRM: Xid (PCI:0000:01:00): 79, GPU has fallen off the bus |
| 20 | hardware | EDAC MC0: 1 UE memory read error on DIMM; mce: Hardware Error: Machine check events logged |
| 21 | hardware | ipmitool sel list: Power Supply #2 Power Supply AC lost Asserted |
| 22 | hardware | smartctl: SMART overall-health self-assessment test result: FAILED; Reallocated_Sector_Ct increasing; md0 degraded array |
| 23 | hardware | nvme nvme0: I/O timeout, controller reset; iostat shows await above 2000 ms |
| 24 | hardware | UPS on battery: upsc shows ups.status OB LB, battery.runtime 300 seconds; mains power lost |
| 25 | hardware | thermal throttling: CPU temperature above 95C, fan 3 reads 0 RPM in ipmitool sdr |
| 26 | k8s | Pod stuck in ImagePullBackOff: failed to pull image registry.local/app:1.2: manifest unknown |
| 27 | k8s | node worker-2 NotReady: kubelet stopped posting node status; PLEG is not healthy |
| 28 | k8s | PersistentVolumeClaim is Pending: no persistent volumes available for this claim and no storage class is set |
| 29 | k8s | etcdserver: request timed out; apiserver slow, etcd fsync latency above 1 s |
| 30 | virtualization | corosync: quorum lost, blocking activity; pve cluster node shows no quorum and VMs cannot start |
| 31 | virtualization | qm start 101 fails: VM is locked (backup); vzdump backup job failed last night |
| 32 | virtualization | ceph health HEALTH_WARN: 1 osds down; Degraded data redundancy, pgs undersized |
| 33 | docker | Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running? |
| 34 | docker | docker: Error response from daemon: failed to create shim task: OCI runtime create failed |
| 35 | boot_packages | cron job did not run: CRON (root) MAIL (mailed 1 byte of output but got status 0x004b); PATH differs under cron |
| 36 | boot_packages | apt: dpkg was interrupted, you must manually run dpkg --configure -a to correct the problem |
| 37 | boot_packages | boot drops to emergency mode: Timed out waiting for device dev-disk-by-uuid; fstab entry for an unplugged disk |
| 38 | app | java: GC overhead limit exceeded; long stop-the-world pauses, requests time out while the heap is near max |

**Gate 1。** 所有量都有單位、基線與門檻；C1 標明為建構檢查；P3 標明描述性。通過（2026-10-22 01:15）。

## Phase 2 — 設計審查（獨立 agent 對抗式審查，2026-10-22 01:20–01:30）

抓取與審查同時進行（抓取只是收資料，不建索引）；索引與評測在審查結論之後才定稿。12 項發現與處置：

| # | 嚴重度 | 發現 | 處置 |
|---|---|---|---|
| 1 | 高 | 「status != matched」對所有舞台句都成立（v3 下六句全是 insufficient），「舞台不變」只是因為 agent 從不呼叫這個工具，不是建構保證；`connect() failed (111…)` 正是 SE 上最高票的 nginx/php-fpm 題 | 加 `stack_covers()` 閘門：查詢含本 stack 實體、runbook 已描述的症狀或其處理的 HTTP 狀態碼時不附參考；C1 新增「六句舞台句與六句 agent 形狀查詢一律不顯示」為通過條件 |
| 2 | 高 | 審查當下 `agent.py` 尚未接線，C1 的「matched 時不顯示」可能空泛通過 | C1 新增 not_vacuous：索引非空時至少一句 Q_gap 要顯示參考 |
| 3 | 中 | 工具結果尾段 150 字進 incident trace，reviewer 會把 SE 片段當本次事故證據；guard 的注入文字 rail 也看不到 | `trace_summary` 對 `search_incident_knowledge` 先切掉 `community references` 區塊 |
| 4 | 中 | 模組層級 `sys.path.insert` + import，`kb/src` 壞掉會拖垮 agent 啟動 | agent 端已是 try 內延遲 import；模組改為在建索引時才 import tokenizer |
| 5 | 中 | CC BY-SA 署名不完整：缺答案作者、授權版本，連到題目而非答案；預算截斷可能切在 URL 中間 | 索引保留 `answer_owner`、`answer_license`、`answer_id`；每筆附「answer by …, CC BY-SA x.y, https://…/a/<id>」；放不下就整筆丟，不截斷 |
| 6 | 中 | 抓取到最後才寫檔，中途被限流就全失 | 本次已成功；未改（重抓時再做分檔落地） |
| 7 | 中 | 去重只看 question_id，關閉為重複的題目會跨 split；標題幾乎都含標籤字，P2 偏易 | 沒存 `closed_reason`，無法回溯過濾（列為限制）；P2 另報「只用症狀行」的數字 |
| 8 | 中 | agent 實際查詢是裸狀態碼或 `symptom_query` 的句子（`the service itself answers HTTP 504…`） | eval 加 Q_agent 六句，預期一律不顯示 |
| 9 | 低 | `incident_like` 篩選實際上是「有錯誤字或有程式碼區塊」 | manifest 欄位語意如實註明（見 Phase 3） |
| 10 | 低 | 程式的標籤表比計畫表少了幾個泛用標籤（hardware、port、tcp、time、boot、unix limit） | 本節註明偏差：抓取前就刪掉太泛的標籤以省配額，計畫表未同步 |
| 11 | 低 | 行內 `<code>` 會把整行散文標成程式碼行 | 症狀行抽取在抓取時固定，未重抓；列為限制 |
| 12 | 低 | 400 一律重試不對；`accepted=True` 應小寫 | 重試改成只對 502/503 與限流（睡 body 指定秒數）；`True` 實測 API 接受 |

**Gate 2。** 高、中項全部處置或列為已揭露限制；通過。

## Phase 3 — 資料稽核（抓取 2026-10-22 01:05–01:09，DGX 上執行）

| 項目 | 值 |
|---|---|
| API 呼叫 | 197（配額剩 99/300） |
| 看到的題目（有已接受答案） | 9,349 |
| 保留（標題含錯誤字、或內文有錯誤字／程式碼行） | 7,706 |
| 答案 < 80 字丟棄 | 111 |
| 最終列數 | 7,595（serverfault 4,709、unix 2,886） |
| 類別 | network 1,102、disk_fs 956、hardware 918、boot_packages 896、memory_process 705、permissions 632、tls_time 619、service 587、virtualization 424、docker 354、app 227、k8s 175 |
| 授權 | CC BY-SA 3.0 4,700、4.0 2,181、2.5 714（API 未給 `content_license` 時依日期推） |
| `qa.jsonl` sha256 | `ef27ec9d…293f7` |
| held-out（15%，種子 20261022，依類別分層） | 1,139 題，sha256 `123aa1b7…58347b` |
| 索引 | 6,456 筆，9.15 MB，sha256 `7db569d0…b8389` |

評測答案隔離：這一層沒有「答案」，P2 的標籤來自題目本身的 tag，held-out 題目不進索引；既有 `eval-hard-repairs` 的評分檔與
kb-eval 的 ground truth 都不被這一層讀取。已知限制：未存 `closed_reason`，SE 上被關閉為重複的高票題可能跨 split（P2 偏樂觀）；
症狀行抽取把含行內 `<code>` 的散文也當程式碼行。

**Gate 3。** 無未處理的高影響發現；通過。

## Phase 4 — 小規模驗證（2026-10-22 01:50–02:10，`scripts/kb-community.py eval`）

第一版閘門用「runbook 標題字重疊 ≥ 2」：把 `device`、`up`、`file` 這種字也算進去，誤壓了 6 句探針（boot/fstab、iptables、
default route…），而 `nginx stopped exited 0` 反而沒壓到。改成明確清單（實體＋runbook 症狀句＋狀態碼）後：

| 檢查 | 結果 | 門檻 |
|---|---|---|
| C1 `knowledge_note` 逐位元相同 | 是 | 必須 |
| C1 matched 時不顯示 | 是 | 必須 |
| C1 六句舞台句不顯示 | 是（第一版閘門時 `nginx stopped` 會顯示） | 必須 |
| C1 六句 agent 形狀查詢不顯示 | 是（裸 `504`/`502`/`429` 斷詞後為空；句型被清單壓住） | 必須 |
| C1 not_vacuous | 31/38 探針句有顯示 | ≥ 1 |
| P2 category@1（標題＋症狀行） | **0.702**（n = 1,139） | ≥ 0.60 且 ≥ 2 × 多數類別 0.145 → 通過 |
| P2 category@1（只用症狀行） | 0.654（n = 1,136） | 描述性 |
| P3 類別正確 / 誤導 / 無 | **31/38 / 5/38 / 2/38** | ≥ 0.70 / ≤ 0.15 → 達標 |
| 延遲 | 5.5 ms／查詢（6,456 筆純 Python BM25） | ≤ 50 ms |
| `eval-hard-repairs.py` | v3 7/8、v4 8/8、反例 0/10 誤選（與載入前相同；v3 的 7/8 在 033cee4 就是如此，對應 `test_repair_knowledge` 已知的 3 個失敗） | 不變 |

P3 的 5 句「誤導」裡有 3 句內容其實相關、只是標籤類別不同（ext4 變唯讀 → 標成 boot_packages 的 ext4 唯讀題；
kubeadm 憑證過期 → k8s 的憑證過期題；corosync quorum → pacemaker/drbd 題）。2 句「無」是閘門刻意壓的
（`no space left on device`、`worker_connections` 本 stack 有 runbook／卡）。反過來，類別對也不保證有用：
`NVRM: Xid 79, GPU has fallen off the bus` 回來的是「Force graphics to run on specific GPU」（共享 token 只有 gpu/nvidia），
BM25 在 6k 筆上對罕見症狀只有弱匹配，參考值靠標示「未驗證的假設」兜底，不靠排名品質。

**Gate 4。** 預先寫定的門檻全部達到；閘門在看到資料後改過一次（安全側，不是為了拉高指標），已在上面揭露。通過。

## Phase 5–6 — 端到端（P4，2026-10-22 02:17–02:24，`logs/drills-20261022-011755.log`）

社群層載入、ops_api 重啟後，五張舞台牌經 sandbox agent：nginx-stopped 25 s、upstream-paused 35 s、container-removed 21 s、
bad-config 33 s、upstream-500 28 s，**5/5 PASS**；整場 `logs/ops-api.log` 裡 `search_incident_knowledge` 呼叫 **0 次**（與過去所有
drill 一致）。之後 `scripts/stage-up.sh` 還原舞台，dashboard 注入 nginx-stopped 經 watchdog 到 resolved 18.4 s（見
`docs/optimizations-20261022.md`）。**Gate 6 通過。** 結論：這一層對舞台路徑的影響是量到的零，不是推論的零；它的價值只在 agent
主動問到 runbook 以外的症狀時，而那在 drill 裡從未發生——所以它是給評審問答與未排練故障的保險，不是分數。

## 決策與未做的事

- 社群層只進 `search_incident_knowledge`；`knowledge_note`、runbook 檢索、reviewer、guards 一律不變。
- 不用 LLM 改寫答案成卡片：來不及審，且自動生成的 FIX 可能危險。原文摘錄＋連結＋「未在本機驗證」標示。
- SE 完整資料傾印（archive.org，數百 MB）是之後擴量的路徑；今天用 API 的每標籤前 100 題。
