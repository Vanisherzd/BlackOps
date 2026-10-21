本次分析使用先前從 DGX 讀取的六份近期日誌，不表示目前遠端狀態。待加入的資料共有三類、24 筆紀錄：14 筆 FAIL、10 筆 TIMEOUT。新修法尚未重跑驗證，不能稱為已解決。

| 故障 | 明確 FAIL／TIMEOUT | 紀錄中沒修好的地方 | 資料集加入的修復路徑 |
|---|---:|---|---|
| upstream-pids-limit | 1／3 | 後端 Cannot fork；重複 start/restart 沒有解除容器 PID 限制。一筆還改動前端，使原本正常的首頁也失效。 | 先看後端 logs、PID 限制及使用量；確認後嘗試一次 target restart，再檢查健康。若同一問題持續，依既有 reviewer 重建指定的無狀態後端；人工替代是恢復核准的 PID 上限。 |
| front-worker-connections | 8／3 | 主設定 worker_connections=2。nginx -t 可以通過；還原站台 default.conf、修網路均未恢復。也有透過唯讀 docker_exec 執行 sed 寫入而遭拒。 | nginx -T 查看主設定，確認後端健康和主檔被變更；重啟前端一次仍失敗且符合檢查條件，才由 reviewer 評估重建前端。人工替代為備份後恢復同版本已知正常的單一 nginx.conf。 |
| upstream-config-crash | 5／4 | 後端自己的 default.conf 語法損壞；錯看前端設定，或在已停止後端執行 docker_exec。也有過早重建遭拒，之後 start 仍失敗卻沒走下一步。 | 先讀後端 logs，辨識檔名及行號；start/restart 後端並取新證據，同錯誤及健康失敗仍持續才走核准的後端重建。不要修改健康的前端。 |

可參考的現有工具呼叫（依序執行，不是可直接貼入 shell 的命令）：

```text
docker_logs {"name":"edge-victim-app","tail":60}
docker_ps {}
docker_exec {"name":"edge-victim-app","cmd":"cat /sys/fs/cgroup/pids.max /sys/fs/cgroup/pids.current /sys/fs/cgroup/pids.events"}
docker_exec {"name":"edge-victim","cmd":"nginx -T"}
```

PID 檢查只適用相應 cgroup v2 介面及能執行 exec 的容器；不存在或 exec 失敗時不能直接推論故障原因，需轉人工檢查。停止的後端應先看 logs。

修復不是直接跳到重建。先查 runbook、確認目標及原因，執行一次適用的 start/restart，再取失敗健康檢查；符合既有條件且 reviewer 同意後，才可對這個可拋棄 demo 容器使用：

```text
recreate_stack {"name":"edge-victim-app","replace":true}
```

前端重建另外需要主設定等 image-owned 檔案的實際變更證據，不是只看到兩個健康檢查失敗就可以做。不得把這條指令套到有持久狀態的正式服務。

每張卡另含人工單檔備份／恢復指令和限制。容器重建會丟失舊 writable layer，沒有自動復原承諾；需要保留的狀態必須在重建前備份。PID 上限及設定檔只能還原至確認正常的值，不能把注入故障值當復原目標。

修完需由 `check_health {}` 實測 `/` 與 `/api/` 都回傳 200，再觀察容器持續 Up 及新日誌，才能標為 resolved。工具呼叫成功不等於服務恢復。

另外，部分日誌出現 `Compaction timed out`、`Agent couldn't generate a response` 或工具存取遭拒。這些是代理執行流程／權限問題的線索，不是三種容器故障的修復成功證明；只加入 RAG 無法保證消除這類失敗，需保留未知原因並另查完整執行追蹤。SSH 的 `Connection timed out` 又是另一個連線問題，沒有證據顯示是上述故障或資料整理造成。

官方參考：

- [Kernel cgroup v2 PID controller](https://docs.kernel.org/admin-guide/cgroup-v2.html)
- [Docker 更新容器資源限制](https://docs.docker.com/reference/cli/docker/container/update/)
- [Nginx worker_connections](https://nginx.org/en/docs/ngx_core_module.html#worker_connections)
- [Nginx -t 與 -T](https://nginx.org/en/docs/switches.html)
- [Docker writable layer 與持久化](https://docs.docker.com/engine/storage/)
- [Docker 單檔複製，可用於停止容器](https://docs.docker.com/reference/cli/docker/container/cp/)
