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

## Phase 2 — 設計審查

（待補：獨立 agent 對上述設計的對抗式審查結果與採納項目。）

## Phase 3 — 資料稽核

（待補：抓取統計、去重、授權欄位、held-out 切分雜湊、是否混入任何評測答案。）

## Phase 4 — 小規模驗證（C1、P2、P3、延遲）

（待補。）

## Phase 5–6 — 端到端（P4）

（待補：五張牌 drill 結果與 `logs/ops-api.log` 檢查。）

## 決策與未做的事

- 社群層只進 `search_incident_knowledge`；`knowledge_note`、runbook 檢索、reviewer、guards 一律不變。
- 不用 LLM 改寫答案成卡片：來不及審，且自動生成的 FIX 可能危險。原文摘錄＋連結＋「未在本機驗證」標示。
- SE 完整資料傾印（archive.org，數百 MB）是之後擴量的路徑；今天用 API 的每標籤前 100 題。
