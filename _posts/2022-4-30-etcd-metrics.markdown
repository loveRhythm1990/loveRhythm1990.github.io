---
layout:     post
title:      "Etcd 监控之 Metrics 汇总"
date:       2022-3-2 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - etcd
---

Etcd 的官方文档 [https://etcd.io/docs/v3.4/metrics/](https://etcd.io/docs/v3.4/metrics/) 介绍了 Etcd 的核心监控 Metrics，主要分下面几部分。

### Server
Etcd 核心服务的监控指标，主要有：
* has_leader: 是否有 leader。如果一个 member 没有 leader，那么这个 member 是不可用的；如果集群中的所有 member 都没有 leader，那么整个集群都是不可用的。
* leader_changes_seen_total: 切主总次数。etcd 启动以来的切主次数，etcd 切主是十分影响性能的，如果一直切主，那么整个集群将十分不稳定（会有请求超时的问题），切主的原因可能有网络问题、也可能是 client 产生了大量的负载。
* proposals_committed_total: 已经 commit 的 proposal 总数，集群中每个节点可能这个指标不一致，一般来说，leader 的这个指标最大；如果有 member 刚刚重启，那么这个指标可能较小。但是如果一个 member 跟 leader 持续性差值比较大，那么这个 member 可能有问题了（slow or unhealthy）。
* proposals_applied_total: 已经 apply 的 proposal 总数。apply proposal 是异步的过程，proposals_committed_total 与 proposals_applied_total 的差值理论上也比较小，如果这两者的差值持续增大，那么说明 etcd 的负载太高了。一般是由于客户端请求的数据量太大了（applying expensive queries like heavy range queries or large txn operations）
* proposals_pending: 在队列中，等待 commit 的 proposal，如果这个指标一直增大，说明 client 负载太高了，或者当前 member 不能 commit proposals。
* proposals_failed_total: 失败的 proposal 总数，可能是由于集群切主或者超过 quorum 数量的 member 失败。

### Disk
磁盘的主要指标有下面两个，反应的是磁盘的性能问题：
* wal_fsync_duration_seconds：fsync wal 的时延，（fsync wal 发生在 apply 之前）
* backend_commit_duration_seconds： 提交增量 snapshot 到磁盘的时延。

* snapshot_save_total_duration_seconds：这个指标在 etcd 的 `etcd_debugging` metrics namespace 中，表示一些更详细的指标，表示 save snapshot 的时延，这个指标异常同样表示磁盘异常，并且集群可能不稳定。

官方文档中，在评估磁盘性能时，对这两个指标有要求，第一个好像是需要在 15s 以内，可以翻一下 文档看看。

### Network
网络指标一般都有 label：`to` 或者 `from`，表示发送给某个 peer 或者来自某个 peer。
* peer_sent_bytes_total: 发送给 peer 的字节总数，这个指标有个 label：to，表示发送给了哪个 peer。
* peer_received_bytes_total：收到的来自某个 peer 的字节总数，同样有 label。
* peer_sent_failures_total： 给某个 peer 发数据时，失败的总次数。
* peer_received_failures_total：接收某个 peer 数据时的失败数。这个一般是读取 http Response Body 失败了。使用的 label 是 `request.RemoteAddr`。
* peer_round_trip_time_seconds：跟某个 peer 通信的往返时延。
* client_grpc_sent_bytes_total：发送给 grpc client 的字节总数。这个好像是 grpc 提供的 hook 点实现的（自定义 Marshal 方法？）
* client_grpc_received_bytes_total：grpc client 接收的字节总数。

### Prometheus 提供的指标
* process_open_fds：进程打开的 fd 数量，打开的 fd 数量过多也会导致问题，（fd 消耗完了，http 连接无法建立、wal 文件也无法打开）
* process_max_fds：能打开的 fd 总数。

接下来的工作是顺着这些指标把 etcd 处理请求的几个步骤分割，毕竟指标反应的重要步骤的执行情况。

在官方 txt 文档[https://etcd.io/docs/v3.4/metrics/etcd-metrics-v3.4.3.txt](https://etcd.io/docs/v3.4/metrics/etcd-metrics-v3.4.3.txt)中，有更多指标，大部分是 `etcd_debugging` namespace 下的，下次再分析。