---
layout:     post
title:      "Etcd: sync wal 时间过长导致切主"
date:       2022-3-20 10:10:00
author:     "decent"
header-img-credit: false
tags:
    - etcd
---


### 切主
一次 K8s 集群中的 Etcd 发生了切主，导致了很多请求超时。排查下来是 sync wal 过长导致的。记录下问题现场以及线索。学习 Etcd 不容易，杂事又特别多，一点一点积累。

Etcd 状态为：

`sudo bin/etcdctl --endpoints=https://node02:2379,https://node01:2379,https://node03:2379 --cert=/etc/kubernetes/ssl/kube-etcd-node02.pem --key=/etc/kubernetes/ssl/kube-etcd-node02-key.pem --cacert=/etc/kubernetes/ssl/kube-ca.pem endpoint status --write-out=table`

一开始 node02 是主节点，但是后来 node03 发起了切主，并成功当选为 leader。
```s
+----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
|          ENDPOINT          |        ID        | VERSION | DB SIZE | IS LEADER | IS LEARNER | RAFT TERM | RAFT INDEX | RAFT APPLIED INDEX | ERRORS |
+----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
| https://node02:2379 | 179e3b479c322b79 |   3.4.3 |  267 MB |     false |      false |        35 |  402135738 |          402135738 |        |
| https://node01:2379 |  d52f541376b969a |   3.4.3 |  267 MB |     false |      false |        35 |  402135740 |          402135740 |        |
| https://node03:2379 | 6cb8f75d6cb36170 |   3.4.3 |  267 MB |      true |      false |        35 |  402135741 |          402135741 |        |
+----------------------------+------------------+---------+---------+-----------+------------+-----------+------------+--------------------+--------+
```

### 日志
#### node1
```s
2022-03-19 02:50:13.833232 W | etcdserver: read-only range request "key:\"/registry/configmaps/ingress-nginx/ingress-controller-leader-nginx\" " with result "range_response_count:1 size:453" took too long (271.646915ms) to execute
2022-03-19 02:50:13.834859 W | etcdserver: read-only range request "key:\"/registry/persistentvolumeclaims/\" range_end:\"/registry/persistentvolumeclaims0\" " with result "range_response_count:62 size:49518" took too long (180.576091ms) to execute
raft2022/03/19 02:50:20 INFO: d52f541376b969a [term: 34] received a MsgVote message with higher term from 6cb8f75d6cb36170 [term: 35]
raft2022/03/19 02:50:20 INFO: d52f541376b969a became follower at term 35
raft2022/03/19 02:50:20 INFO: d52f541376b969a [logterm: 34, index: 397355953, vote: 0] cast MsgVote for 6cb8f75d6cb36170 [logterm: 34, index: 397355953] at term 35
raft2022/03/19 02:50:20 INFO: raft.node: d52f541376b969a lost leader 179e3b479c322b79 at term 35
raft2022/03/19 02:50:20 INFO: raft.node: d52f541376b969a elected leader 6cb8f75d6cb36170 at term 35
2022-03-19 02:50:20.985898 W | etcdserver: read-only range request "key:\"/registry/management.cattle.io/authconfigs\" range_end:\"/registry/management.cattle.io/authconfigt\" count_only:true " with result "error:etcdserver: leader changed" took too long (5.764087152s) to execute
2022-03-19 02:50:20.989864 W | etcdserver: read-only range request "key:\"/registry/tekton.dev/pipelineresources\" range_end:\"/registry/tekton.dev/pipelineresourcet\" count_only:true " with result "range_response_count:0 size:10" took too long (2.020004322s) to execute
2022-03-19 02:50:20.989894 W | etcdserver: read-only range request "key:\"/registry/services/endpoints/kube-system/kube-scheduler\" " with result "range_response_count:1 size:465" took too long (3.878584886s) to execute
```
#### node2
切主
```s
2022-03-19 02:50:13.832863 W | etcdserver: read-only range request "key:\"/registry/minions/yf-finrisk-saas-cloud-staging13\" " with result "range_response_count:1 size:10688" took too long (228.068936ms) to execute
2022-03-19 02:50:13.833014 W | etcdserver: read-only range request "key:\"/registry/configmaps/ingress-nginx/ingress-controller-leader-nginx\" " with result "range_response_count:1 size:453" took too long (204.285748ms) to execute
raft2022/03/19 02:50:20 INFO: 179e3b479c322b79 [logterm: 34, index: 397355968, vote: 179e3b479c322b79] ignored MsgVote from 6cb8f75d6cb36170 [logterm: 34, index: 397355953] at term 34: lease is not expired (remaining ticks: 2)
raft2022/03/19 02:50:20 INFO: 179e3b479c322b79 [term: 34] received a MsgApp message with higher term from 6cb8f75d6cb36170 [term: 35]
raft2022/03/19 02:50:20 INFO: 179e3b479c322b79 became follower at term 35
raft2022/03/19 02:50:20 INFO: found conflict at index 397355954 [existing term: 34, conflicting term: 35]
raft2022/03/19 02:50:20 INFO: truncate the unstable entries before index 397355954
raft2022/03/19 02:50:20 INFO: raft.node: 179e3b479c322b79 changed leader from 179e3b479c322b79 to 6cb8f75d6cb36170 at term 35
WARNING: 2022/03/19 02:50:25 grpc: Server.processUnaryRPC failed to write status: connection error: desc = "transport is closing"
2022-03-19 02:50:25.835981 W | etcdserver: read-only range request "key:\"/registry/leases/mcloud/mcloud\" " with result "error:context canceled" took too long (10.001058247s) to execute
WARNING: 2022/03/19 02:50:25 grpc: Server.processUnaryRPC failed to write status: connection error: desc = "transport is closing"
2022-03-19 02:50:25.856285 W | etcdserver: read-only range request "key:\"/registry/configmaps/kruise-system/kruise-manager\" " with result "error:context canceled" took too long (10.014405221s) to execute
WARNING: 2022/03/19 02:50:25 grpc: Server.processUnaryRPC failed to write status: connection error: desc = "transport is closing"
2022-03-19 02:50:26.250844 W | etcdserver: read-only range request "key:\"/registry/services/endpoints/kube-system/kube-scheduler\" " with result "error:context canceled" took too long (9.999569669s) to execute
WARNING: 2022/03/19 02:50:26 grpc: Server.processUnaryRPC failed to write status: connection error: desc = "transport is closing"
```
ReadIndex 超时
```s
WARNING: 2022/03/19 02:50:29 grpc: Server.processUnaryRPC failed to write status: connection error: desc = "transport is closing"
2022-03-19 02:50:30.130186 W | etcdserver: timed out waiting for read index response (local node might have slow network)
2022-03-19 02:50:30.130315 W | etcdserver: read-only range request "key:\"/registry/configmaps/kube-system/cattle-controllers\" " with result "error:etcdserver: request timed out" took too long (15.000244558s) to execute
2022-03-19 02:50:31.065856 W | etcdserver: failed to revoke 61707d802f6b4582 ("etcdserver: request timed out")
```
wal 写超时
```s
2022-03-19 02:51:30.134343 W | etcdserver: read-only range request "key:\"/registry/pods/kube-system/kubelet-connection-detector-4cf65\" " with result "error:etcdserver: request timed out" took too long (17.258563249s) to execute
2022-03-19 02:51:30.134395 W | etcdserver: read-only range request "key:\"/registry/iam.mae.io/loginrecords\" range_end:\"/registry/iam.mae.io/loginrecordt\" count_only:true " with result "error:etcdserver: request timed out" took too long (25.093309s) to execute
WARNING: 2022/03/19 02:51:31 grpc: Server.processUnaryRPC failed to write status: connection error: desc = "transport is closing"
WARNING: 2022/03/19 02:51:31 grpc: Server.processUnaryRPC failed to write status: connection error: desc = "transport is closing"
2022-03-19 02:51:31.085980 W | etcdserver: failed to revoke 61707d802f6b4582 ("etcdserver: request timed out")
2022-03-19 02:51:31.655916 W | wal: sync duration of 1m15.841622694s, expected less than 1s
2022-03-19 02:51:31.656083 W | etcdserver: ignored out-of-date read index response; local node read indexes queueing up and waiting to be in sync with leader (request ID want 3132673005432378348, got 3132673005432378156)
2022-03-19 02:51:31.656249 W | etcdserver: read-only range request "key:\"/registry/core.kubefed.io/federatedtypeconfigs\" range_end:\"/registry/core.kubefed.io/federatedtypeconfigt\" count_only:true " with result "error:etcdserver: leader changed" took too long (11.826510005s) to execute
```

#### node3
```s
2022-03-19 02:50:13.834415 W | etcdserver: read-only range request "key:\"/registry/services/endpoints/kube-system/kube-scheduler\" " with result "range_response_count:1 size:465" took too long (315.159643ms) to execute
2022-03-19 02:50:13.838055 W | etcdserver: read-only range request "key:\"/registry/management.cattle.io/roletemplates\" range_end:\"/registry/management.cattle.io/roletemplatet\" count_only:true " with result "range_response_count:0 size:10" took too long (236.056749ms) to execute
raft2022/03/19 02:50:20 INFO: 6cb8f75d6cb36170 is starting a new election at term 34
raft2022/03/19 02:50:20 INFO: 6cb8f75d6cb36170 became candidate at term 35
raft2022/03/19 02:50:20 INFO: 6cb8f75d6cb36170 received MsgVoteResp from 6cb8f75d6cb36170 at term 35
raft2022/03/19 02:50:20 INFO: 6cb8f75d6cb36170 [logterm: 34, index: 397355953] sent MsgVote request to d52f541376b969a at term 35
raft2022/03/19 02:50:20 INFO: 6cb8f75d6cb36170 [logterm: 34, index: 397355953] sent MsgVote request to 179e3b479c322b79 at term 35
raft2022/03/19 02:50:20 INFO: raft.node: 6cb8f75d6cb36170 lost leader 179e3b479c322b79 at term 35
raft2022/03/19 02:50:20 INFO: 6cb8f75d6cb36170 received MsgVoteResp from d52f541376b969a at term 35
raft2022/03/19 02:50:20 INFO: 6cb8f75d6cb36170 has received 2 MsgVoteResp votes and 0 vote rejections
raft2022/03/19 02:50:20 INFO: 6cb8f75d6cb36170 became leader at term 35
raft2022/03/19 02:50:20 INFO: raft.node: 6cb8f75d6cb36170 elected leader 6cb8f75d6cb36170 at term 35
2022-03-19 02:50:20.985830 W | etcdserver: read-only range request "key:\"/registry/persistentvolumeclaims/zookeeper/data-zookeeper-leaf-q8fmn-p-zmvf2-2\" " with result "error:etcdserver: leader changed" took too long (5.817691359s) to execute
2022-03-19 02:50:22.676782 W | etcdserver: request "header:<ID:10852124241924061039 username:\"system:node\" auth_revision:1 > txn:<compare:<target:MOD key:\"/registry/events/default/yf-finrisk-saas-cloud-staging12.16dda86c76d4bf3c\" mod_revision:0 > success:<request_put:<key:\"/registry/events/default/yf-finrisk-saas-cloud-staging12.16dda86c76d4bf3c\" value_size:369 lease:7021249808575448020 >> failure:<>>" with result "size:22" took too long (181.196299ms) to execute
WARNING: 2022/03/19 02:50:25 grpc: Server.processUnaryRPC failed to write status: connection error: desc = "transport is closing"
```

### 日志分析
node02: 02:51:31 报错误：`wal: sync duration of 1m15.841622694s, expected less than 1s`

node03: 02:50:20 发起了新的选举：`6cb8f75d6cb36170 is starting a new election at term 34`

从日志中可以看出，node02 开始 sync wal 日志的时间为 02:51:31 - 1.16 = 02:50:15。sync 时延是 1m15.8s，当成 16s 算）而 node03 发起选举的时间是 02:50:20，正好相差 5s，也就是选举超时时间，sync wal 会阻塞发送心跳（不会影响当前轮次的心跳发送，影响的是下一轮），导致了心跳超时。

根据 Etcd 文档 [Why does etcd lose its leader from disk latency spikes](https://etcd.io/docs/v3.2/faq/#why-does-etcd-lose-its-leader-from-disk-latency-spikes)：

This is intentional; disk latency is part of leader liveness. Suppose the cluster leader takes a minute to fsync a raft log update to disk, but the etcd cluster has a one second election timeout. Even though the leader can process network messages within the election interval (e.g., send heartbeats), it’s effectively unavailable because it can’t commit any new proposals; it’s waiting on the slow disk. If the cluster frequently loses its leader due to disk latencies, try tuning the disk settings or etcd time parameters.

### 逻辑分析
这篇文章[《深入浅出 etcd》part 2 – 解析 etcd 的心跳和选举机制](https://www.infoq.cn/article/y2lv9mymjc9hnisiaeht) 介绍的很详细了，希望再看这篇文章的时候他还在。

Etcd raft 模块的输出结果都是通过 `Ready` 结构返回给 EtcdServer 模块的，后者是 raft 模块的应用层。这些输出结果有：
* raft 模块产生的待发送的消息，心跳消息就是其中一种。
* 当前未持久化的日志条目
* 确定可以 commit 的日志条目
* 一些状态：softState、hardState、readstate，一起打包到 Ready 数据结构中。

Ready 结构的具体定义为，定义的文件为 `etcd/raft/node.go` 中：
```go
// Ready encapsulates the entries and messages that are ready to read,
// be saved to stable storage, committed or sent to other peers.
// All fields in Ready are read-only.
type Ready struct {
	// The current volatile state of a Node.
	// SoftState will be nil if there is no update.
	// It is not required to consume or store SoftState.
	*SoftState

	// The current state of a Node to be saved to stable storage BEFORE
	// Messages are sent.
	// HardState will be equal to empty state if there is no update.
	pb.HardState

	// ReadStates can be used for node to serve linearizable read requests locally
	// when its applied index is greater than the index in ReadState.
	// Note that the readState will be returned when raft receives msgReadIndex.
	// The returned is only valid for the request that requested to read.
	ReadStates []ReadState

	// Entries specifies entries to be saved to stable storage BEFORE
	// Messages are sent.
	Entries []pb.Entry

	// Snapshot specifies the snapshot to be saved to stable storage.
	Snapshot pb.Snapshot

	// CommittedEntries specifies entries to be committed to a
	// store/state-machine. These have previously been committed to stable
	// store.
	CommittedEntries []pb.Entry

	// Messages specifies outbound messages to be sent AFTER Entries are
	// committed to stable storage.
	// If it contains a MsgSnap message, the application MUST report back to raft
	// when the snapshot has been received or has failed by calling ReportSnapshot.
	Messages []pb.Message

	// MustSync indicates whether the HardState and Entries must be synchronously
	// written to disk or if an asynchronous write is permissible.
	MustSync bool
}
``` 
raft 模块的心跳消息也是消息的一种，所以我们只要看 EtcdServer 怎么处理 Ready 结构体，怎么处理消息就行了。

#### EtcdServer 消费 Ready
EtcdServer 在启动的时候（启动是在 `func (s *EtcdServer) run()` 方法中），启动一个单独的 goroutine 来跟 raft 模块交互，是通过调用 raftNode 的 start 方法进行的，`s.r.start(rh)`，消费 Ready 也是在这个 goroutine 中进行的。

下面梳理下这个方法的主干，主要关注下处理 Ready 以及消费 message 的逻辑。其实也可以说，这个 start 的主要方法就是消费 Ready。
```go
func (r *raftNode) start(rh *raftReadyHandler) {
	internalTimeout := time.Second

	// 启动一个单独的 goroutine
	go func() {
		defer r.onStop()
		islead := false

		// 这是一个无限循环，任务就是不断消费三个 channel，没有其他逻辑
		for {
			// 等待三个 channel
			select {
				// 这个是时间单位，触发 raft 的 tick，是供 raft 消费的。
			case <-r.ticker.C:
				r.tick()
			
			// 消费 raftNode 的 Ready channel。
			case rd := <-r.Ready(): // 读取并处理 Ready 结构体，该结构体是 raft 模块发出来的
				if rd.SoftState != nil {
					// 处理 SoftState 先不考虑
				}

				if len(rd.ReadStates) != 0 {
					// 处理 ReadState 先不考虑
				}

				notifyc := make(chan struct{}, 1)
				ap := apply{
					entries:  rd.CommittedEntries,
					snapshot: rd.Snapshot,
					notifyc:  notifyc,
				}

				// TODO 更新 commit index
				updateCommittedIndex(&ap, rh)

				select {
				case r.applyc <- ap:
				case <-r.stopped:
					return
				}

				// the leader can write to its disk in parallel with replicating to the followers and them
				// writing to their disks.
				// For more details, check raft thesis 10.2.1
				if islead {
					// 这里是发送消息给其他 peer。
					r.transport.Send(r.processMessages(rd.Messages))
				}

				// 这里的 Save 是写 wal 日志。
				if err := r.storage.Save(rd.HardState, rd.Entries); err != nil {
					if r.lg != nil {
						r.lg.Fatal("failed to save Raft hard state and entries", zap.Error(err))
					} else {
						plog.Fatalf("raft save state and entries error: %v", err)
					}
				}
				if !raft.IsEmptyHardState(rd.HardState) {
					proposalsCommitted.Set(float64(rd.HardState.Commit))
				}

				// 暂时不考虑其他逻辑
				r.raftStorage.Append(rd.Entries)
				// TODO 收到 message 之后，进行 Advance
				r.Advance()
			case <-r.stopped:
				return
			}
		}
	}()
}
```
从上面可以看出，对于 leader 来说，其实是先发送的消息，然后写 wal 日志，那么写 wal 日志为什么会阻塞心跳消息呢？其实是阻塞的下一轮的消息，也就是下一个 for 循环。处理这个 for 循环太久了，下一轮消息就没办法发送了。

## 参考
[《深入浅出 etcd》part 2 – 解析 etcd 的心跳和选举机制](https://www.infoq.cn/article/y2lv9mymjc9hnisiaeht)

[grpc: Server.processUnaryRPC failed to write status connection error: desc = "transport is closing #12895](https://github.com/etcd-io/etcd/issues/12895): 这个 issue 说 `transport is closing` 是给 client 写返回数据的时候，client 关闭了，比如重启了 kube-apiserver。


[Etcd-tuning parameters](https://etcd.io/docs/v3.4/tuning/)

[proposal dropped easily when connection with leader lost #3380](https://github.com/etcd-io/etcd/issues/3380): 这个 issue 考虑切主的时候，要不要把请求缓存起来，并转发，后来考虑了一下，说不要。因此，只要发生了切主，就会发生超时。