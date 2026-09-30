# 解法卡依據

- Kubernetes Debug Running Pods：`kubectl logs --previous`、container crash 診斷
  - https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/
- Kubernetes Debugging DNS Resolution：Service、CoreDNS、EndpointSlice、`resolv.conf` 診斷
  - https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/
- Kubernetes Node-pressure Eviction：DiskPressure、nodefs、imagefs、containerfs、inode
  - https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/
- Kubernetes Garbage Collection：dead containers、unused images、kubelet GC
  - https://kubernetes.io/docs/concepts/architecture/garbage-collection/
- systemd `journalctl`：`--disk-usage`、`--vacuum-size`、`--vacuum-time`
  - https://www.freedesktop.org/software/systemd/man/latest/journalctl.html

資料集標註用於建立故障模式與相似案例；上述官方文件用於校正檢查與修復安全邊界。
