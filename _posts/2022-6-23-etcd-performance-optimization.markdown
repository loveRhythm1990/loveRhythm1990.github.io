---
layout:     post
title:      "Etcd 性能调优总结"
date:       2022-6-23 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - etcd
---

最近做了很多 Etcd 运维工作，也遇到了一些场景，这里结合 《Kubernetes 生产化实践之路》 （后面称这本书为《实践》）以及网上的一些思路，思考下 Etcd 性能调优相关总结，并对相关引用材料进行整理。这些引用材料可能并没有完全读完，或者理解思考，这里作为索引，后面再对这些内容作为迭代。也就是说性能调优作为主线，相关引用材料作为分支。

### 减少网络时延
《实践》这本书中提到“建议 Etcd 应尽量做到同地域部署”，这个信息很重要，因为最近也在做同城双活，一般来说，同城（同城双活）的时延在 1ms 左右，应该是还能符合一个 etcd 的时延要求的。这个我们也使用 tc 来模拟时延，在配置网卡时延在 20ms 时，etcd 性能即功能影响不是很大。 

在 etcd 节点网络流量很大时，我们可以提高 etcd 流量的优先级，这个也可以通过 tc 来完成，具体怎么做，可以参考[《How to use 'tc' (traffic control) to increase / decrease an application's network priority?》](https://www.reddit.com/r/linuxquestions/comments/5vsvnr/how_to_use_tc_traffic_control_to_increase/)，思路是通过 iptables 来对流量进行标记，然后把不同的流量放到 tc 的不同队列里。

tc 工具很复杂，难得有资料能把事情讲清楚。不过从上面来看，通过 tc 来控制流量优先级还是有一定成本的。《实践》中还提到了一种 case，leader 的连接太多，导致 follower 发往 leader 的请求被丢弃
```s
dropped MsgProp to 247ae21ff9436b2d since streamMsg's sending buffer is full
dropped MsgAppResp to 247ae21ff9436b2d since streamMsg's sending buffer is full
```

在官方文档中[https://etcd.io/docs/v3.4/tuning/#network](https://etcd.io/docs/v3.4/tuning/#network)，提供了使用 tc 提高 etcd 流量优先级的方法：

```s
# 提供优先级
tc qdisc add dev eth0 root handle 1: prio bands 3
tc filter add dev eth0 parent 1: protocol ip prio 1 u32 match ip sport 2380 0xffff flowid 1:1
tc filter add dev eth0 parent 1: protocol ip prio 1 u32 match ip dport 2380 0xffff flowid 1:1
tc filter add dev eth0 parent 1: protocol ip prio 2 u32 match ip sport 2379 0xffff flowid 1:1
tc filter add dev eth0 parent 1: protocol ip prio 2 u32 match ip dport 2379 0xffff flowid 1:1

# 取消 tc 限速配置
tc qdisc del dev eth0 root
```
其中，关于 tc 命令中 handle 的说明可以参考 [https://tldp.org/HOWTO/Traffic-Control-HOWTO/components.html#c-handle](https://tldp.org/HOWTO/Traffic-Control-HOWTO/components.html#c-handle)，看上去就是一个 ID，关于 `prio` 模块的说明可以参考：[https://linux.die.net/man/8/tc-prio](https://linux.die.net/man/8/tc-prio)
这种方式比 iptables 标记流量的方式好像简单一点。


### 减少磁盘 I/O 延迟
一般物理磁盘的时延为 10ms，SSD 的时延低于 1ms，所以 etcd 一般用 ssd。而且是单盘只给 ssd 用。另外因为 k8s 的 event 量多且频繁变更，我们可以将 event 单独存储，这个需要修改 kube-apiserver 的参数:`--etcd-servers-overrides`，可以参考[《Etcd 安装、部署、测试》](https://loverhythm1990.github.io/2021/08/28/etcd-hello/)

另外，如果 etcd 共享磁盘，那么可以使用 ionice 来提高 etcd 磁盘优先级，命令为：
```s
ionice -c2 -n0 -p 'pgrep etcd'
```

### 自动压缩历史版本
这个调整 etcd 的参数 `--auto--compaction` 即可，一般默认是开启的，这个有两种压缩模式，一种是周期性的，一种是根据历史记录数，这个看下文档即可，不再赘述。

### 定期消除碎片
etcd 是不会主动归还空闲的磁盘空间的，这个时候需要调用`etcdctl defrag`命令来释放空间，另外在 etcd 中，我们调用 delete 接口删除数据时，并不会从磁盘删数据，只是对对应的版本数据添加一个`tombstone` 标志，因此我们删除数据并不会释放空间，除非调用 `defrag` 命令释放磁盘空间。

### 优化运行参数
这个主要是`心跳周期(heartbeat，默认100ms)`和`选举超时时间(election timeout，默认1000ms)`，尤其是在高迟延场景下，需要增加这两个参数，避免造成频繁选主，造成集群不稳定。

心跳周期一般设置为 etcd 成员直接平均往返周期的最大值，一般是平均RTT 的 0.55~1.5 倍。选举超时一般设置为心跳周期的10倍。测量 TTL 最简单的方法是使用 ping 工具。

### 参考
《Kubernetes 生产化实践之路》ebay
[《etcd调优(Tuning)》](https://zhuanlan.zhihu.com/p/29806621)