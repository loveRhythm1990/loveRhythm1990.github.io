---
layout:     post
title:      "容器 PageCache 使用过多，导致内存利用率较高"
date:       2022-11-07 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - Linux
---

**文章目录**
- [workingset 不是实际使用内存](#workingset-不是实际使用内存)
- [为什么会有这么高的 PageCache 使用？](#为什么会有这么高的-pagecache-使用)

前段时间线上业务在排查监控时，发现监控显示容器的内存使用率很高，已经到了 90%，便反馈到我们这边，同时他们说通过 top 看实际使用的内存使用率很低，觉得监控图有问题，进行查看之后，发现监控问题不大，不过确实有些细节需要注意。

### workingset 不是实际使用内存
经过一番查看，业务说的没问题，通过 top 看内存使用时，RES 显示只有 1.7G，但是监控显示使用了接近 8G。我们监控使用的指标是`container_memory_working_set_bytes`，在监控图中，内存使用率的计算方式是 `container_memory_working_set_bytes / container_spec_memory_limit_bytes *100%`，其中：
* container_memory_working_set_bytes：cgroup 中 memory.usage_in_bytes 的值减去 inactive file 的内存使用，但是包含了 PageCache 的使用量。具体计算方式可以参考：[《Pod 被驱逐了？看看这篇文章》](https://loverhythm1990.github.io/2022/08/31/k8s-evict/)里面有一个计算 workingset 的脚本。同时需要注意，cgroup 中的 memory.usage_in_bytes 包含了两部分的值，一个是 RES，另一个就是 PageCache。这个可以参考[《Page Cache：为什么我的容器内存使用量总是在临界点?》]()
* container_spec_memory_limit_bytes：容器被限制的总内存大小，即 cgroup 中的 memory.limit_in_bytes。

通过对指标的解释，我们知道了监控图没错，业务说的也没错，其实在 grafana 的监控图中，还有个 RSS 的指标，业务方其实更多的关注的是这个（也就是图中黄色的线，而不是蓝色的线）。
![java-javascript](/img/in-post/all-in-one/2022-11-08-00-57-08.png){:height="70%" width="70%"}

**那这个监控指标重要吗？** 在 K8s 中，当 container_memory_working_set_bytes 接近 container_spec_memory_limit_bytes 时，就可能会触发 OOM（out-of-memory），导致进程被 kill。也就是说系统还是基本根据 workingset 来杀 Pod，那岂不是很危险，也未必，为啥呢？因为 PageCache 作为系统对磁盘读写的缓存，在进程需要申请内存时，是可以被释放的。所以文章[《Page Cache：为什么我的容器内存使用量总是在临界点?》]()说内存使用量总是在临界点，这里的临界点基本可以看成 limit 值。

### 为什么会有这么高的 PageCache 使用？
因为 PageCache 是对磁盘文件读写的缓存，看到这么高的PageCache 使用时，首先猜测业务是个高IO的应用，问下来其实不是，IO基本只有写日志。同时我们在查看 `/proc/meminfo` 时发现，`Cache`的值（也就是pagecache）基本等于 `active(file)` 的值，我们知道 `active(file)`是系统对磁盘文件的缓存，相对于匿名内存。同时我们观察到一个现象，这两者的值基本等于业务在磁盘上的日志文件的大小，业务把日志文件切割了，但是没有回收，导致日志文件非常多。

那有没有可能 PageCache 缓存了这些日志文件一直没有释放呢？我们先用 lsof 和 fuser 来查看进程有没有使用这些文件，发现没有。后来决定直接删除一个日志文件试一试，发现果然如此：删除一个日志文件，pagecache 使用量下降了，并且下降的量基本等于日志文件的大小，其中 `active(file)` 内存也有对应的下降量。关于 `/proc/meminfo` 文件的说明，可以参考[《The /proc/meminfo File in Linux》](https://www.baeldung.com/linux/proc-meminfo)，系统一直持有这些日志文件的缓存，可能是系统闲置内存够用，没有触发对PageCache 的回收。meminfo的输出示例如下，包含了系统使用内存情况的一个概览。
```s
[decent@ubuntu ~]$ cat /proc/meminfo
MemTotal:       16249468 kB
MemFree:          335616 kB
MemAvailable:   11232216 kB
Buffers:          755932 kB
Cached:          9903440 kB
SwapCached:            0 kB
Active:          9907088 kB
Inactive:        4180388 kB
Active(anon):    3718164 kB
Inactive(anon):   481436 kB
Active(file):    6188924 kB
Inactive(file):  3698952 kB
Unevictable:           0 kB
Mlocked:               0 kB
SwapTotal:             0 kB
SwapFree:              0 kB
Dirty:               896 kB
Writeback:             0 kB
AnonPages:       3428004 kB
Mapped:           525832 kB
Shmem:            771496 kB
Slab:            1516532 kB
SReclaimable:    1346936 kB
SUnreclaim:       169596 kB
KernelStack:       34960 kB
PageTables:        29540 kB
NFS_Unstable:          0 kB
Bounce:                0 kB
WritebackTmp:          0 kB
CommitLimit:     8124732 kB
Committed_AS:   20430316 kB
VmallocTotal:   34359738367 kB
VmallocUsed:      119456 kB
VmallocChunk:   34359493092 kB
HardwareCorrupted:     0 kB
AnonHugePages:    688128 kB
CmaTotal:              0 kB
CmaFree:               0 kB
HugePages_Total:       0
HugePages_Free:        0
HugePages_Rsvd:        0
HugePages_Surp:        0
Hugepagesize:       2048 kB
DirectMap4k:      292704 kB
DirectMap2M:    16484352 kB
```

乐观来说，pagecache 使用高并不会导致 oom，但保险起见还是让业务方清理一下日志吧。

参考：
[《Page Cache：为什么我的容器内存使用量总是在临界点?》]()
